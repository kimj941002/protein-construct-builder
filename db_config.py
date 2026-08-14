# db_config.py
# Supabase(Postgres) 연결 단일 진입점.
# secrets(Streamlit st.secrets → 로컬 .streamlit/secrets.toml → 환경변수) 에서
# 컴포넌트를 읽어 SQLAlchemy Engine 을 안전하게 조립한다.
# UNIFIED_MIGRATION_PLAN.md §3.
from __future__ import annotations

import os
import functools
from pathlib import Path

from sqlalchemy import create_engine, URL
from sqlalchemy.engine import Engine, make_url

_SECRETS_FILE = Path(__file__).parent / ".streamlit" / "secrets.toml"
# 기본 pooler 호스트(리전) — secrets 에 SUPABASE_POOLER_HOST 가 있으면 그 값을 우선.
_DEFAULT_POOLER_HOST = "aws-0-ap-northeast-2.pooler.supabase.com"


def _load_secrets() -> dict:
    """Streamlit st.secrets → 로컬 secrets.toml → 환경변수 순으로 설정을 읽는다."""
    # 1) Streamlit 런타임
    try:
        import streamlit as st
        keys = list(st.secrets.keys())
        if keys:
            return {k: st.secrets[k] for k in keys}
    except Exception:
        pass
    # 2) 로컬 secrets.toml
    if _SECRETS_FILE.exists():
        try:
            import tomllib  # py3.11+
        except ModuleNotFoundError:  # pragma: no cover
            import tomli as tomllib  # type: ignore
        return tomllib.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
    # 3) 환경변수
    return dict(os.environ)


def _require(secrets: dict, key: str) -> str:
    val = str(secrets.get(key, "") or "")
    if not val or "여기에" in val or "PASTE" in val:
        raise RuntimeError(f"secrets 에 {key} 가 설정되지 않았습니다. .streamlit/secrets.toml 확인.")
    return val


def _database_url_override() -> str:
    """DATABASE_URL 이 설정돼 있으면 그 표준 Postgres 연결 문자열을 그대로 사용.
    (Neon / 로컬 PostgreSQL / Oracle VM 등 — Supabase 컴포넌트 방식 대신). 없으면 빈 문자열."""
    s = _load_secrets()
    return str(s.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()


def build_url(direct: bool = False) -> URL:
    """
    연결 URL 조립. DATABASE_URL 이 있으면 그것을 우선 사용(표준 Postgres),
    없으면 Supabase 컴포넌트(REF/HOST/PW)로 조립.

    direct=False : transaction pooler (포트 6543) — 앱 런타임용. user = postgres.<ref>
    direct=True  : direct connection (포트 5432) — 일회성 마이그레이션용. user = postgres
    """
    dburl = _database_url_override()
    if dburl:
        if dburl.startswith("postgres://"):
            dburl = "postgresql+psycopg2://" + dburl[len("postgres://"):]
        elif dburl.startswith("postgresql://"):
            dburl = "postgresql+psycopg2://" + dburl[len("postgresql://"):]
        return make_url(dburl)

    s = _load_secrets()
    pw = _require(s, "SUPABASE_DB_PASSWORD")
    ref = _require(s, "SUPABASE_PROJECT_REF")

    if direct:
        return URL.create(
            "postgresql+psycopg2",
            username="postgres",
            password=pw,
            host=f"db.{ref}.supabase.co",
            port=5432,
            database="postgres",
        )

    host = str(s.get("SUPABASE_POOLER_HOST") or _DEFAULT_POOLER_HOST)
    return URL.create(
        "postgresql+psycopg2",
        username=f"postgres.{ref}",
        password=pw,
        host=host,
        port=6543,
        database="postgres",
    )


@functools.lru_cache(maxsize=2)
def get_engine(direct: bool = False) -> Engine:
    """
    프로세스당 1회 생성하는 SQLAlchemy Engine (캐시).

    psycopg2 는 서버사이드 named prepared statement 를 기본으로 사용하지 않아
    Supabase pooler(pgbouncer transaction 모드)와 호환된다.
    """
    # DATABASE_URL 사용 시엔 sslmode 를 강제하지 않음(로컬 Postgres 는 SSL 미사용,
    # Neon 등은 URL 쿼리(?sslmode=require)로 직접 지정). Supabase 컴포넌트 방식만 강제.
    connect_args = {} if _database_url_override() else {"sslmode": "require"}
    return create_engine(
        build_url(direct=direct),
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )
