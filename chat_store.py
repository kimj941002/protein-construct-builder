# chat_store.py
# AI 질의 대화 기록을 chat_history.json에 저장·조회·삭제합니다.
#
# 핵심 설계:
#   - 각 대화는 related_uniprot_ids (list[str]) 로 복수의 단백질에 연결됩니다.
#   - 관련 단백질은 질문 + 답변 + SQL 텍스트에서 gene_name / protein_name을 매칭하여 자동 추출합니다.
#   - 사이드바에서는 현재 선택된 단백질이 related_uniprot_ids 에 포함된 기록만 표시합니다.
#   - 구버전 기록(단일 uniprot_id 필드)도 하위 호환됩니다.

from __future__ import annotations

import json
import re
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
# 단백질 키워드 분석
# ─────────────────────────────────────────────
def extract_related_proteins(
    question: str,
    result: dict,
    proteins: list[dict],
) -> list[str]:
    """
    질문·답변·SQL 텍스트에서 언급된 단백질의 uniprot_id 목록을 반환합니다.

    Args:
        question:  사용자 질문 원문
        result:    query_db_with_llm 반환값 (answer, queries 포함)
        proteins:  DB의 proteins 테이블 행 목록 (gene_name, protein_name, uniprot_id 필요)

    Returns:
        관련 단백질 uniprot_id 리스트 (일치 없으면 빈 리스트)
    """
    # 검색 대상 텍스트: 질문 + LLM 답변 + 실행된 모든 SQL
    sql_text = " ".join(q.get("sql", "") for q in result.get("queries", []))
    answer_text = result.get("answer", "")
    corpus = (question + " " + answer_text + " " + sql_text).lower()

    related: list[str] = []
    seen: set[str] = set()

    for p in proteins:
        uid = p.get("uniprot_id", "")
        if not uid or uid in seen:
            continue

        # 검색 후보: gene_name 우선, protein_name 보조
        candidates = []
        gene = (p.get("gene_name") or "").strip()
        pname = (p.get("protein_name") or "").strip()
        if gene:
            candidates.append(gene)
        if pname:
            candidates.append(pname)

        for name in candidates:
            if len(name) < 2:          # 너무 짧은 이름은 오탐 방지
                continue
            pattern = r"\b" + re.escape(name.lower()) + r"\b"
            if re.search(pattern, corpus):
                related.append(uid)
                seen.add(uid)
                break

    return related


# ─────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────
def load_history(uniprot_id: str | None = None) -> list[dict]:
    """
    대화 기록을 최신순으로 반환합니다.
    uniprot_id 지정 시 related_uniprot_ids 에 해당 ID가 포함된 기록만 반환합니다.
    구버전 기록(단일 uniprot_id 필드)도 호환합니다.
    """
    records = _load_raw()
    if uniprot_id:
        filtered = []
        for r in records:
            if "related_uniprot_ids" in r:
                # 신버전: 리스트에 포함 여부 확인
                if uniprot_id in r["related_uniprot_ids"]:
                    filtered.append(r)
            elif r.get("uniprot_id") == uniprot_id:
                # 구버전: 단일 필드 호환
                filtered.append(r)
        records = filtered

    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)


def save_chat(
    related_uniprot_ids: list[str],
    question: str,
    result: dict,
) -> dict:
    """
    새 대화 기록을 저장하고 저장된 record를 반환합니다.

    Args:
        related_uniprot_ids: 이 질의와 관련된 단백질 uniprot_id 목록
        question:            사용자 질문 원문
        result:              query_db_with_llm 반환값
    """
    records = _load_raw()
    record = {
        "id":                  str(uuid.uuid4()),
        "timestamp":           datetime.now().isoformat(),
        "related_uniprot_ids": related_uniprot_ids,
        "question":            question,
        "queries":             result.get("queries", []),
        "answer":              result.get("answer", ""),
        "error":               result.get("error"),
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
