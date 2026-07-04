# playground_llm.py
# 문헌 Playground 의 Claude 오케스트레이션.
#  - run_chat: 대화(기본 opus). P6 에서 web_search + DB 조회 툴 연결.
#  - generate_digest: 살아있는 정리본(수동 '정리' 버튼, sonnet).
# API 키: paper_pipeline._ensure_anthropic_key() 재사용(secrets→env).
from __future__ import annotations

CHAT_MODEL = "claude-opus-4-8"
DIGEST_MODEL = "claude-sonnet-4-6"

_SYS_CHAT = (
    "당신은 구조생물학·의약화학 연구 조수입니다. 사용자와 특정 단백질을 주제로 심화 연구 "
    "대화를 진행합니다. 정확하고 근거 있는 답변을 하되, 불확실하면 불확실하다고 말합니다. "
    "한국어로 답합니다. 전문 용어는 원어를 병기합니다.\n"
    "이 대화는 '연구 Playground'의 일부이며, 나눈 내용은 나중에 체계적으로 정리됩니다. "
    "따라서 답변은 사실·수치·출처를 명확히 하고 논리적으로 구조화하세요."
)

_SYS_DIGEST = (
    "당신은 연구 노트 정리 전문가입니다. 아래는 한 연구 Playground 에서 사용자와 조수가 나눈 "
    "전체 대화(raw)입니다. 이 Playground 가 지금까지 '다룬 내용'을 **체계적으로 정리**하세요.\n"
    "규칙:\n"
    "1. 과도하게 요약하지 말 것. 다룬 핵심 내용·수치·결론을 충실히 담을 것.\n"
    "2. 부차적/곁가지 잡담은 제외하고, 다룬 주제를 체계적으로 조직할 것.\n"
    "3. 마크다운 제목/불릿으로 구조화. 주제별 섹션 → 핵심 사실 → 결론/미해결 질문 순.\n"
    "4. 새로운 정보를 지어내지 말 것. 대화에 있는 내용만 정리.\n"
    "5. 한국어. 전문 용어 원어 병기.\n"
    "이 정리본은 Playground 를 다시 열 때 가장 먼저 보이는 '살아있는 요약본'입니다."
)


def _client():
    from paper_pipeline import _ensure_anthropic_key
    _ensure_anthropic_key()
    import anthropic
    return anthropic.Anthropic()


def _text_of(msg) -> str:
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts).strip()


def run_chat(history: list[dict], extra_context: str = "",
             model: str = CHAT_MODEL, max_tokens: int = 4000) -> str:
    """대화 히스토리(raw) → 조수 답변 텍스트.

    history: [{"role":"user"|"assistant", "content": str}, ...] (마지막이 방금 user 메시지).
    extra_context: 연결 문헌·DB 팩트 등 시스템에 주입할 추가 컨텍스트(P5).
    """
    msgs = [{"role": m["role"], "content": m["content"]}
            for m in history if m.get("content")]
    if not msgs:
        return "(질문이 비어 있습니다.)"
    system = _SYS_CHAT
    if extra_context.strip():
        system += "\n\n[연결된 문헌·DB 컨텍스트]\n" + extra_context.strip()[:20000]
    client = _client()
    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=msgs,
    )
    out = _text_of(msg)
    return out or "(답변을 생성하지 못했습니다.)"


def generate_digest(history: list[dict], model: str = DIGEST_MODEL,
                    max_tokens: int = 4000) -> str:
    """대화 전체 → 체계적 정리본(digest) 마크다운."""
    convo = []
    for m in history:
        if not m.get("content"):
            continue
        who = "사용자" if m["role"] == "user" else "조수"
        convo.append(f"### {who}\n{m['content']}")
    transcript = "\n\n".join(convo)
    if not transcript.strip():
        return ""
    client = _client()
    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=_SYS_DIGEST,
        messages=[{"role": "user",
                   "content": "다음 대화를 정리하세요:\n\n" + transcript[:120000]}],
    )
    return _text_of(msg)
