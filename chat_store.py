# chat_store.py
# AI 질의 대화 기록을 chat_history.json에 저장·조회·삭제합니다.
# 기록은 uniprot_id 기준으로 필터링할 수 있으며,
# 단백질 전환 시 sidebar 목록이 자동으로 해당 단백질 기록만 표시됩니다.

import json
import uuid
from datetime import datetime
from pathlib import Path

CHAT_HISTORY_PATH = Path(__file__).parent / "chat_history.json"


# ─────────────────────────────────────────────
# 내부 I/O
# ─────────────────────────────────────────────
def _load_raw() -> list[dict]:
    if not CHAT_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(CHAT_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_raw(records: list[dict]) -> None:
    CHAT_HISTORY_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────
def load_history(uniprot_id: str | None = None) -> list[dict]:
    """
    대화 기록을 반환합니다. (최신순 정렬)
    uniprot_id 를 지정하면 해당 단백질의 기록만 반환합니다.
    """
    records = _load_raw()
    if uniprot_id:
        records = [r for r in records if r.get("uniprot_id") == uniprot_id]
    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def save_chat(uniprot_id: str, question: str, result: dict) -> dict:
    """
    새 대화 기록을 저장하고 저장된 record를 반환합니다.
    result 키: queries(list), answer(str), error(str|None)
    """
    records = _load_raw()
    record = {
        "id":         str(uuid.uuid4()),
        "timestamp":  datetime.now().isoformat(),
        "uniprot_id": uniprot_id,
        "question":   question,
        "queries":    result.get("queries", []),
        "answer":     result.get("answer", ""),
        "error":      result.get("error"),
    }
    records.append(record)
    _save_raw(records)
    return record


def get_chat(chat_id: str) -> dict | None:
    """ID로 특정 대화 기록을 조회합니다."""
    for r in _load_raw():
        if r.get("id") == chat_id:
            return r
    return None


def delete_chat(chat_id: str) -> None:
    """ID로 특정 대화 기록을 삭제합니다."""
    records = [r for r in _load_raw() if r.get("id") != chat_id]
    _save_raw(records)
