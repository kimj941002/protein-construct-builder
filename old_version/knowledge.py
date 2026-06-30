# knowledge.py
# 주제(Topic) 기반 지식베이스 (서비스 계층).
# 주제로 단백질·구조·논문을 묶고, LLM 으로 교차 종합한 '인사이트'를 만들어 임베딩하고,
# pgvector 의미검색으로 누적 지식을 질의한다. (REFLEX_UNIFIED_PLAN.md Phase 3)
from __future__ import annotations

from sqlalchemy import text

from db_config import get_engine
import embeddings as E


# ── 주제 CRUD ──
def create_topic(name: str, description: str = "") -> int:
    with get_engine().begin() as c:
        row = c.execute(
            text("INSERT INTO topics (name, description) VALUES (:n, :d) RETURNING id"),
            {"n": name, "d": description},
        ).first()
    return row[0]


def list_topics() -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT * FROM topics ORDER BY created_at DESC")).mappings().all()
    return [dict(r) for r in rows]


def delete_topic(topic_id: int) -> None:
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM topics WHERE id = :i"), {"i": topic_id})


# ── 연결(링크) ──
def add_link(topic_id: int, entity_type: str, entity_id) -> None:
    with get_engine().begin() as c:
        c.execute(
            text("INSERT INTO topic_links (topic_id, entity_type, entity_id) "
                 "VALUES (:t, :et, :ei)"),
            {"t": topic_id, "et": entity_type, "ei": str(entity_id)},
        )


def get_links(topic_id: int) -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(
            text("SELECT * FROM topic_links WHERE topic_id = :t ORDER BY created_at"),
            {"t": topic_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def list_insights(topic_id: int) -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(
            text("SELECT * FROM insights WHERE topic_id = :t ORDER BY created_at DESC"),
            {"t": topic_id},
        ).mappings().all()
    return [dict(r) for r in rows]


# ── LLM 종합(Synthesize) ──
def _gather_context(topic_id: int) -> str:
    parts: list[str] = []
    with get_engine().connect() as c:
        for l in get_links(topic_id):
            et, ei = l["entity_type"], l["entity_id"]
            if et == "paper":
                r = c.execute(text("SELECT title, analysis_md FROM papers WHERE paper_id = :i"),
                              {"i": ei}).mappings().first()
                if r:
                    parts.append(f"[논문] {r['title']}\n{(r['analysis_md'] or '')[:3000]}")
            elif et == "structure":
                r = c.execute(text("SELECT structure_id, raw_text FROM paper_analysis WHERE structure_id = :i"),
                              {"i": ei}).mappings().first()
                if r and r.get("raw_text"):
                    parts.append(f"[구조 {ei} 논문분석]\n{r['raw_text'][:3000]}")
            elif et == "protein":
                r = c.execute(text("SELECT uniprot_id, gene_name, function_desc FROM proteins WHERE uniprot_id = :i"),
                              {"i": ei}).mappings().first()
                if r:
                    parts.append(f"[단백질] {r['gene_name']} ({r['uniprot_id']}): {r.get('function_desc') or ''}")
    return "\n\n".join(parts)[:30000]


def synthesize_topic(topic_id: int) -> dict:
    """주제에 연결된 자료를 LLM 으로 교차 종합 → insights 저장 + 임베딩."""
    with get_engine().connect() as c:
        topic = c.execute(text("SELECT name FROM topics WHERE id = :i"),
                          {"i": topic_id}).mappings().first()
    name = topic["name"] if topic else ""
    context = _gather_context(topic_id)
    if not context.strip():
        return {"error": "연결된 자료가 없습니다. 단백질/구조/논문을 먼저 연결하세요."}

    from paper_pipeline import _ensure_anthropic_key
    _ensure_anthropic_key()
    import anthropic
    client = anthropic.Anthropic()
    prompt = (
        f"다음은 '{name}' 주제로 모은 연구 자료다. 교차 종합하여 핵심 인사이트"
        f"(공통 패턴, 차이/상충, 구조-기능 시사점, 추가로 확인할 점)를 "
        f"한국어로 구조적으로 정리하라.\n\n{context}"
    )
    msg = client.messages.create(
        model="claude-opus-4-8", max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    body = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")

    with get_engine().begin() as c:
        row = c.execute(
            text("INSERT INTO insights (topic_id, title, body) VALUES (:t, :ti, :b) RETURNING id"),
            {"t": topic_id, "ti": f"{name} 종합 인사이트", "b": body},
        ).first()
    insight_id = row[0]
    E.store_embedding("insight", str(insight_id), body)
    add_link(topic_id, "insight", insight_id)
    return {"body": body, "insight_id": insight_id}


# ── 의미검색 / 재색인 ──
def semantic_search(query: str, k: int = 5) -> list[dict]:
    return E.semantic_search(query, k=k)


def reindex_papers() -> int:
    """기존 papers.analysis_md + paper_analysis.raw_text 를 임베딩(의미검색 대상으로)."""
    with get_engine().connect() as c:
        papers = c.execute(text(
            "SELECT paper_id, title, analysis_md FROM papers WHERE analysis_md IS NOT NULL"
        )).mappings().all()
        pas = c.execute(text(
            "SELECT structure_id, raw_text FROM paper_analysis WHERE raw_text IS NOT NULL"
        )).mappings().all()
    n = 0
    for p in papers:
        E.delete_embeddings("paper", p["paper_id"])
        E.store_embedding("paper", p["paper_id"],
                          (p["title"] or "") + "\n" + (p["analysis_md"] or ""))
        n += 1
    for pa in pas:
        E.delete_embeddings("structure", pa["structure_id"])
        E.store_embedding("structure", pa["structure_id"], pa["raw_text"] or "")
        n += 1
    return n
