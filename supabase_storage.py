"""
supabase_storage.py — Supabase Storage 로 대용량 PDF 직접 업로드.

Reflex Cloud 는 요청 본문을 ~5MB 로 제한하므로, 큰 PDF 를 Reflex 백엔드(/_upload)로
보내면 실패한다. 대신 **브라우저 → Supabase Storage 직접 업로드**로 우회한다:
  1) 서버가 service_role 키로 '서명 업로드 URL'을 만든다(키는 서버에만).
  2) 브라우저가 그 URL 로 파일을 PUT (Reflex Cloud 를 안 거침 → 크기 무제한).
  3) 서버는 Storage 경로만 DB(paper_analysis.pdf_storage_path)에 저장.
  4) 구조화 분석·열람 시 Storage 에서 다운로드/서명 URL 로 처리.

필요 secrets: SUPABASE_PROJECT_REF (기존), SUPABASE_SERVICE_KEY (신규 — 대시보드
  Project Settings → API → service_role secret).
"""
from __future__ import annotations

import functools
import re

from db_config import _load_secrets

BUCKET = "papers"


def has_storage_config() -> bool:
    s = _load_secrets()
    return bool(s.get("SUPABASE_PROJECT_REF")) and bool(s.get("SUPABASE_SERVICE_KEY"))


@functools.lru_cache(maxsize=1)
def _client():
    from supabase import create_client
    s = _load_secrets()
    ref = str(s.get("SUPABASE_PROJECT_REF", "") or "")
    key = str(s.get("SUPABASE_SERVICE_KEY", "") or "")
    if not ref or not key:
        raise RuntimeError(
            "Supabase Storage 사용에는 SUPABASE_PROJECT_REF 와 SUPABASE_SERVICE_KEY 가 "
            "secrets 에 필요합니다 (대시보드 Settings→API→service_role).")
    return create_client(f"https://{ref}.supabase.co", key)


def ensure_bucket() -> None:
    c = _client()
    try:
        c.storage.get_bucket(BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(BUCKET, options={"public": False})
        except Exception:
            pass  # 이미 있거나 권한 문제 — 업로드에서 다시 드러남


def storage_path_for(sid: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-80:] or "paper.pdf"
    return f"{sid}/{safe}"


def create_signed_upload(path: str) -> dict:
    """브라우저가 PUT 할 서명 업로드 URL. {signed_url, token, path}.

    같은 경로에 파일이 이미 있으면 create_signed_upload_url 이 409(Duplicate)를 내므로,
    기존 객체를 먼저 지우고(덮어쓰기) 재시도한다.
    """
    ensure_bucket()
    b = _client().storage.from_(BUCKET)
    try:
        res = b.create_signed_upload_url(path)
    except Exception as e:
        s = str(e)
        if "409" in s or "Duplicate" in s or "already exist" in s.lower():
            try:
                b.remove([path])   # 덮어쓰기 위해 기존 삭제
            except Exception:
                pass
            res = b.create_signed_upload_url(path)
        else:
            raise
    if isinstance(res, dict):
        url = res.get("signed_url") or res.get("signedUrl") or res.get("signedURL")
        return {"signed_url": url, "token": res.get("token"), "path": res.get("path") or path}
    return {"signed_url": getattr(res, "signed_url", None),
            "token": getattr(res, "token", None), "path": path}


def object_exists(path: str) -> bool:
    """업로드 검증용 — Storage 에 실제로 객체가 있는지 확인."""
    try:
        b = _client().storage.from_(BUCKET)
        parts = path.rsplit("/", 1)
        folder = parts[0] if len(parts) == 2 else ""
        name = parts[-1]
        items = b.list(folder) or []
        return any((it.get("name") if isinstance(it, dict) else getattr(it, "name", None)) == name
                   for it in items)
    except Exception:
        return False


def create_signed_download(path: str, expires: int = 3600) -> str:
    """열람용 서명 다운로드 URL (기본 1시간)."""
    res = _client().storage.from_(BUCKET).create_signed_url(path, expires)
    if isinstance(res, dict):
        return res.get("signedURL") or res.get("signed_url") or res.get("signedUrl") or ""
    return getattr(res, "signed_url", "") or ""


def download_bytes(path: str) -> bytes | None:
    """구조화 분석용 — Storage 에서 PDF 바이트 다운로드."""
    try:
        return _client().storage.from_(BUCKET).download(path)
    except Exception as e:
        print(f"[WARN] Storage 다운로드 실패 ({path}): {e}")
        return None
