"""
migrate_to_neon.py — Supabase(현재 secrets) → Neon 전체 데이터 이전.

전제: Supabase 프로젝트가 Active(Restore 됨). supabase_schema.sql 로 대상 스키마 생성.
실행(주로 Claude 가 실행):
  NEON_DATABASE_URL="postgresql://<user>:<pw>@ep-xxx.<region>.aws.neon.tech/neondb?sslmode=require" \
  /c/Users/jk941/miniconda3/python.exe -X utf8 migrate_to_neon.py

동작: (1) Neon 에 pgvector 확장 + 전체 스키마 생성 → (2) 원본 테이블 데이터 복사(FK 우회)
     → (3) IDENTITY 시퀀스 보정 → (4) 원본/대상 행 수 대조 검증.
안전: 원본(Supabase)은 읽기만 함(변경 없음). 문제 시 secrets 를 원복하면 그대로 복귀.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text


def main():
    neon_url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if not neon_url:
        print("[!] NEON_DATABASE_URL 환경변수가 필요합니다."); sys.exit(1)
    if neon_url.startswith("postgresql://"):
        neon_url = "postgresql+psycopg2://" + neon_url[len("postgresql://"):]

    # jsonb 컬럼(dict) 어댑트 + 안전
    import psycopg2.extras
    psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)

    from db_config import get_engine as _supa
    src = _supa()                       # 원본: Supabase (현재 secrets)
    dst = create_engine(neon_url, pool_pre_ping=True, future=True)

    # 연결 확인
    with src.connect() as c:
        print("[OK] 원본(Supabase) 연결:", c.execute(text("SELECT 1")).scalar() == 1)
    with dst.connect() as c:
        print("[OK] 대상(Neon) 연결:", c.execute(text("SELECT 1")).scalar() == 1)

    # 1) 대상 스키마: pgvector + supabase_schema.sql
    ddl = open(os.path.join(os.path.dirname(__file__), "supabase_schema.sql"),
               encoding="utf-8").read()
    with dst.begin() as c:
        try:
            c.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
        except Exception as e:
            print("[WARN] pgvector 확장:", str(e)[:80])
        c.exec_driver_sql(ddl)
    print("[OK] Neon 스키마 생성 완료")

    # 2) 원본 테이블 목록
    with src.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))]
    print("이전 대상 테이블:", tables)

    # 3) 데이터 복사 (FK 트리거 우회 → 순서 무관)
    with dst.begin() as dc:
        dc.exec_driver_sql("SET session_replication_role = replica;")
        for t in tables:
            with src.connect() as sc:
                rows = sc.execute(text(f'SELECT * FROM "{t}"')).mappings().all()
            if not rows:
                print(f"  {t}: 0행"); continue
            cols = list(rows[0].keys())
            collist = ", ".join(f'"{c}"' for c in cols)
            params = ", ".join(f":{c}" for c in cols)
            dc.exec_driver_sql(f'TRUNCATE "{t}" CASCADE;')
            # 배치 삽입
            payload = [dict(r) for r in rows]
            dc.execute(text(f'INSERT INTO "{t}" ({collist}) VALUES ({params})'), payload)
            print(f"  {t}: {len(rows)}행 복사")
        dc.exec_driver_sql("SET session_replication_role = default;")

    # 4) IDENTITY 시퀀스 보정
    with dst.begin() as dc:
        for t in tables:
            try:
                dc.exec_driver_sql(
                    f"SELECT setval(pg_get_serial_sequence('\"{t}\"','id'), "
                    f"COALESCE((SELECT MAX(id) FROM \"{t}\"),1), true) "
                    f"WHERE pg_get_serial_sequence('\"{t}\"','id') IS NOT NULL;")
            except Exception:
                pass
    print("[OK] 시퀀스 보정 완료")

    # 5) 검증: 행 수 대조
    print("=== 행 수 대조 (Supabase → Neon) ===")
    all_ok = True
    with src.connect() as sc, dst.connect() as dc:
        for t in tables:
            a = sc.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            b = dc.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            ok = (a == b)
            all_ok = all_ok and ok
            print(f"  {'OK ' if ok else 'DIFF'} {t}: {a} -> {b}")
    print("=== 이전", "성공 (모든 테이블 행 수 일치)" if all_ok else "불일치 — 확인 필요", "===")


if __name__ == "__main__":
    main()
