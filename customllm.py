# customllm.py
# Custom LLM — 주제별 '트리형 대화방' + DB 접근 LLM (서비스 계층).
# 대화형 챗봇의 맥락 휘발 문제를, 주제→소주제 분화 트리 + DB(run_sql) 항상 참조로 해소.
# REFLEX_UNIFIED_PLAN.md / REDESIGN_PLAN.md Phase R4.
from __future__ import annotations

from sqlalchemy import text

from db_config import get_engine


# ── 방 트리 CRUD ──
def _create_room(title: str, parent_id: int | None) -> int:
    with get_engine().begin() as c:
        row = c.execute(
            text("INSERT INTO llm_rooms (title, parent_id) VALUES (:t, :p) RETURNING id"),
            {"t": title, "p": parent_id},
        ).first()
    return row[0]


def create_topic_room(title: str) -> int:
    return _create_room(title, None)


def list_rooms() -> list[dict]:
    """모든 방을 트리 깊이(depth) 포함해 반환(부모 먼저 정렬)."""
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(
            text("SELECT * FROM llm_rooms ORDER BY created_at")
        ).mappings().all()]
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        depth, pid = 0, r["parent_id"]
        while pid and pid in by_id and depth < 20:
            depth += 1
            pid = by_id[pid]["parent_id"]
        r["depth"] = depth
        r["indent"] = "　" * depth  # 전각 공백 들여쓰기
    # 트리 순서(부모 바로 뒤에 자식) 정렬
    ordered: list[dict] = []

    def _add_children(parent):
        for r in rows:
            if r["parent_id"] == parent:
                ordered.append(r)
                _add_children(r["id"])

    _add_children(None)
    return ordered


def delete_room(room_id: int) -> None:
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM llm_rooms WHERE id = :i"), {"i": room_id})


# ── 메시지 ──
def add_message(room_id: int, role: str, content: str) -> None:
    with get_engine().begin() as c:
        c.execute(
            text("INSERT INTO llm_messages (room_id, role, content) VALUES (:r, :ro, :c)"),
            {"r": room_id, "ro": role, "c": content},
        )


def get_messages(room_id: int) -> list[dict]:
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(
            text("SELECT * FROM llm_messages WHERE room_id = :r ORDER BY created_at"),
            {"r": room_id},
        ).mappings().all()]


# ── DB 접근 LLM (run_sql 도구 재사용) ──
_SYS = None


def _system_prompt() -> str:
    global _SYS
    if _SYS is None:
        from llm_query import _SCHEMA
        _SYS = (
            "당신은 단백질 구조·논문 데이터베이스에 접근하는 연구 보조 LLM입니다. "
            "필요하면 run_sql 도구로 아래 DB를 조회해 근거를 확보하고, 구조생물학적으로 "
            "의미 있는 한국어 답변을 하세요. papers/paper_analysis(structured JSONB: 클로닝·발현·"
            "정제·결정화·어세이 조건 포함) 에 수집된 분석도 적극 활용하세요.\n\n" + _SCHEMA
        )
    return _SYS


def _chat(messages: list[dict]) -> str:
    """run_sql 도구를 쓰는 Claude 대화 루프 → 최종 답변 텍스트."""
    from paper_pipeline import _ensure_anthropic_key
    from llm_query import execute_sql, _TOOLS, _format_rows_for_llm
    import anthropic

    _ensure_anthropic_key()
    client = anthropic.Anthropic()
    msgs = list(messages)
    for _ in range(12):
        resp = client.messages.create(
            model="claude-opus-4-8", max_tokens=8000,
            system=_system_prompt(), tools=_TOOLS, messages=msgs,
        )
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "tool_use":
            results = []
            for b in resp.content:
                if getattr(b, "type", "") == "tool_use" and b.name == "run_sql":
                    rows, err = execute_sql(b.input.get("query", ""))
                    content = (f"SQL 오류: {err}" if err
                               else (_format_rows_for_llm(rows) if rows else "결과 없음 (0 rows)"))
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": content})
            msgs.append({"role": "user", "content": results})
            continue
        if resp.stop_reason == "pause_turn":
            continue
        return next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return "(최대 반복 도달)"


def send_message(room_id: int, user_text: str) -> str:
    """사용자 메시지 저장 → (방 히스토리 + 도구) LLM 호출 → 답변 저장·반환."""
    add_message(room_id, "user", user_text)
    history = get_messages(room_id)
    msgs = []
    for m in history:
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msgs.append({"role": "assistant", "content": m["content"]})
        elif m["role"] == "insight":
            msgs.append({"role": "assistant", "content": "[맥락/통찰]\n" + (m["content"] or "")})
    answer = _chat(msgs)
    add_message(room_id, "assistant", answer)
    return answer


def branch_room(parent_id: int, title: str) -> int:
    """부모 방의 맥락 요약을 시드로 자식 방 생성(맥락 휘발 방지)."""
    cid = _create_room(title, parent_id)
    history = get_messages(parent_id)
    if history:
        convo = "\n".join(f"{m['role']}: {(m['content'] or '')[:1500]}" for m in history[-20:])
        summary = _chat([{
            "role": "user",
            "content": f"다음 대화를, 새 소주제 '{title}' 로 이어가기 위한 핵심 맥락으로 "
                       f"5줄 이내로 요약하라:\n\n{convo}",
        }])
        add_message(cid, "insight", "[상위 맥락 요약]\n" + summary)
    return cid


def rollup_insight(parent_id: int) -> dict:
    """하위 방들의 대화를 교차 종합해 상위 방에 통찰 메시지로 저장."""
    children = [r for r in list_rooms() if r["parent_id"] == parent_id]
    if not children:
        return {"error": "하위 대화방이 없습니다. 먼저 분화하세요."}
    parts = []
    for ch in children:
        msgs = get_messages(ch["id"])
        txt = "\n".join(f"{m['role']}: {(m['content'] or '')[:1200]}" for m in msgs[-12:])
        parts.append(f"## 하위주제: {ch['title']}\n{txt}")
    ctx = "\n\n".join(parts)[:30000]
    insight = _chat([{
        "role": "user",
        "content": "다음 하위 주제 대화들을 교차 종합하여 상위 통찰"
                   "(공통 패턴, 차이/상충, 시사점, 추가로 볼 것)을 한국어로 구조적으로 정리하라:\n\n" + ctx,
    }])
    add_message(parent_id, "insight", insight)
    return {"body": insight}
