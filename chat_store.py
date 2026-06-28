# chat_store.py
# AI 질의 대화 기록을 Supabase chats 테이블에 저장·조회·삭제합니다.
# (구) chat_history.json 파일 저장 대체. UNIFIED_MIGRATION_PLAN.md §7.
#
# 핵심 설계:
#   - 각 대화는 related_uniprot_ids (list[str]) 로 복수의 단백질에 연결됩니다 (JSONB).
#   - 관련 단백질은 질문 + 답변 + SQL 텍스트에서 gene_name / protein_name을 매칭하여 자동 추출합니다.
#   - 사이드바에서는 현재 선택된 단백질이 related_uniprot_ids 에 포함된 기록만 표시합니다.

from __future__ import annotations

import json
import re

from sqlalchemy import text

from db_config import get_engine


# ─────────────────────────────────────────────
# 단백질 키워드 분석 (순수 로직 — 변경 없음)
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
    sql_text = " ".join(q.get("sql", "") for q in result.get("queries", []))
    answer_text = result.get("answer", "")
    corpus = (question + " " + answer_text + " " + sql_text).lower()

    related: list[str] = []
    seen: set[str] = set()

    for p in proteins:
        uid = p.get("uniprot_id", "")
        if not uid or uid in seen:
            continue

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
# 내부: DB row → app 호환 record dict
# ─────────────────────────────────────────────
def _row_to_record(r) -> dict:
    created = r["created_at"]
    return {
        "id":                  r["id"],
        "timestamp":           created.isoformat() if created else "",
        "related_uniprot_ids": r["related_uniprot_ids"] or [],
        "question":            r["question"],
        "queries":             r["queries"] or [],
        "answer":              r["answer"],
        "error":               r["error"],
    }


# ─────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────
def load_history(uniprot_id: str | None = None) -> list[dict]:
    """
    대화 기록을 최신순으로 반환합니다.
    uniprot_id 지정 시 related_uniprot_ids 에 해당 ID가 포함된 기록만 반환합니다.
    """
    if uniprot_id:
        sql = text(
            "SELECT * FROM chats WHERE related_uniprot_ids @> CAST(:one AS JSONB) "
            "ORDER BY created_at DESC"
        )
        params = {"one": json.dumps([uniprot_id])}
    else:
        sql = text("SELECT * FROM chats ORDER BY created_at DESC")
        params = {}
    with get_engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [_row_to_record(r) for r in rows]


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
        result:              query_db_with_llm 반환값 (queries, answer, error)
    """
    params = {
        "question": question,
        "answer":   result.get("answer", ""),
        "queries":  json.dumps(result.get("queries", []), ensure_ascii=False, default=str),
        "error":    result.get("error"),
        "related":  json.dumps(related_uniprot_ids, ensure_ascii=False),
    }
    with get_engine().begin() as conn:
        row = conn.execute(text("""
            INSERT INTO chats (question, answer, queries, error, related_uniprot_ids)
            VALUES (:question, :answer, CAST(:queries AS JSONB), :error, CAST(:related AS JSONB))
            RETURNING id, created_at
        """), params).mappings().first()
    return {
        "id":                  row["id"],
        "timestamp":           row["created_at"].isoformat(),
        "related_uniprot_ids": related_uniprot_ids,
        "question":            question,
        "queries":             result.get("queries", []),
        "answer":              result.get("answer", ""),
        "error":               result.get("error"),
    }


def get_chat(chat_id) -> dict | None:
    """ID로 특정 대화 기록을 조회합니다."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM chats WHERE id = :id"), {"id": int(chat_id)}
        ).mappings().first()
    return _row_to_record(row) if row else None


def delete_chat(chat_id) -> None:
    """ID로 특정 대화 기록을 삭제합니다."""
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM chats WHERE id = :id"), {"id": int(chat_id)})


def update_chat_tags(chat_id, related_uniprot_ids: list[str]) -> None:
    """특정 채팅 기록의 related_uniprot_ids를 업데이트합니다."""
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE chats SET related_uniprot_ids = CAST(:r AS JSONB) WHERE id = :id"),
            {"r": json.dumps(related_uniprot_ids), "id": int(chat_id)},
        )
