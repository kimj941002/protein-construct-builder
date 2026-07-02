# paper_pipeline.py
# 논문 분석 오케스트레이션 (서비스 계층 — UI 비종속).
# paper-analyzer app.py 의 4단계 실행 순서를 함수로 추출. analyze_paper.py 재사용.
from __future__ import annotations

import os


def _ensure_anthropic_key() -> None:
    """ANTHROPIC_API_KEY 가 env 에 없으면 secrets 에서 주입 (PaperAnalyzer 가 env 사용)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        from db_config import _load_secrets
        key = str(_load_secrets().get("ANTHROPIC_API_KEY", "") or "")
        if key and "PASTE" not in key and "여기에" not in key:
            os.environ["ANTHROPIC_API_KEY"] = key
    except Exception:
        pass


def run_full_analysis(
    pdf_path: str,
    model: str = "claude-sonnet-4-6",
    lang: str = "ko",
    text_only: bool = False,
    max_images: int = 30,
) -> dict:
    """
    PDF 1개를 4단계로 분석한다 (전처리→텍스트→Figure→종합).
    Returns 성공: {"output_md", "result", "doi", "cost"} / 실패: {"error"}.
    """
    _ensure_anthropic_key()
    from analyze_paper import extract_from_pdf, PaperAnalyzer, _build_output, _estimate_cost

    extraction = extract_from_pdf(pdf_path, max_images=max_images)
    if not extraction.full_text.strip():
        return {"error": "PDF에서 텍스트를 추출할 수 없습니다 (스캔 PDF일 수 있음)."}

    analyzer = PaperAnalyzer(model=model, lang=lang)
    analyzer.analyze_text(extraction)
    if not text_only and extraction.images:
        analyzer.analyze_figures(extraction)
    analyzer.synthesize(extraction)

    class _Args:
        pass

    args = _Args()
    args.model = model
    output_md = _build_output(analyzer.result, extraction, args)

    usage = analyzer.result.token_usage
    cost = _estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    return {
        "output_md": output_md,
        "result": analyzer.result,
        "doi": extraction.metadata.get("doi", ""),
        "cost": cost,
    }


# 구조화 추출 항목 (PDB database 의 PDB별 논문 분석)
_COND_KEYS = ["topic", "insights", "cloning", "expression",
             "purification", "crystallization", "assay"]


def extract_conditions_from_text(text: str, model: str = "claude-sonnet-4-6") -> dict:
    """이미 확보한 텍스트(논문 본문 또는 기존 분석본)에서 실험 세부조건을 구조화 추출.

    PDF 가 없어도(예: 과거 업로드분 유실) 저장된 분석본으로 구조화 분석을 가능케 한다.
    Returns: {"conditions": {...7항목...}} / {"error": ...}
    """
    import json
    import re

    text = (text or "").strip()
    if not text:
        return {"error": "분석할 텍스트가 없습니다."}

    _ensure_anthropic_key()
    import anthropic

    prompt = _COND_PROMPT_PREFIX + text[:60000]
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "{}")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}
    conditions = {k: str(data.get(k, "정보 없음") or "정보 없음") for k in _COND_KEYS}
    return {"conditions": conditions}


_COND_PROMPT_PREFIX = (
    "다음 구조생물학 논문 텍스트(또는 분석본)에서 아래 항목을 한국어로 추출해 **JSON 객체만** "
    "출력하라 (코드펜스/설명 없이). 각 값은 구체적 수치·시약·조건을 포함한 문자열.\n"
    '스키마: {"topic": "논문 주제 한 줄", '
    '"insights": "핵심 통찰 3~5개 (줄바꿈으로 구분)", '
    '"cloning": "DNA cloning: 벡터, 태그, construct 경계(잔기범위), 도입 변이 등", '
    '"expression": "단백질 발현: 발현 숙주/시스템, 유도·배양 조건", '
    '"purification": "단백질 정제: 컬럼/태그/단계/버퍼", '
    '"crystallization": "결정화: 방법, 침전제 조성, pH/온도, space group, 해상도", '
    '"assay": "활성·분석 어세이: 종류(kinase/SPR/BLI 등), 조건, 주요 파라미터"}\n'
    '해당 정보가 논문에 없으면 그 값은 "정보 없음".\n\n논문 텍스트:\n'
)


def extract_construct_conditions(pdf_path: str, model: str = "claude-sonnet-4-6") -> dict:
    """
    논문 PDF 에서 주제·통찰 + 실험 세부조건(클로닝/발현/정제/결정화/어세이)을
    구조화(JSON)하여 추출한다.
    Returns: {"conditions": {topic, insights, cloning, expression, purification,
                              crystallization, assay}, "doi": str} / {"error": ...}
    """
    import json
    import re

    _ensure_anthropic_key()
    from analyze_paper import extract_from_pdf
    import anthropic

    extraction = extract_from_pdf(pdf_path, max_images=0)  # 조건 추출은 텍스트만
    text = (extraction.full_text or "").strip()
    if not text:
        return {"error": "PDF에서 텍스트를 추출할 수 없습니다 (스캔 PDF일 수 있음)."}

    prompt = (
        "다음 구조생물학 논문 텍스트에서 아래 항목을 한국어로 추출해 **JSON 객체만** 출력하라 "
        "(코드펜스/설명 없이). 각 값은 구체적 수치·시약·조건을 포함한 문자열.\n"
        '스키마: {"topic": "논문 주제 한 줄", '
        '"insights": "핵심 통찰 3~5개 (줄바꿈으로 구분)", '
        '"cloning": "DNA cloning: 벡터, 태그, construct 경계(잔기범위), 도입 변이 등", '
        '"expression": "단백질 발현: 발현 숙주/시스템, 유도·배양 조건", '
        '"purification": "단백질 정제: 컬럼/태그/단계/버퍼", '
        '"crystallization": "결정화: 방법, 침전제 조성, pH/온도, space group, 해상도", '
        '"assay": "활성·분석 어세이: 종류(kinase/SPR/BLI 등), 조건, 주요 파라미터"}\n'
        '해당 정보가 논문에 없으면 그 값은 "정보 없음".\n\n논문 텍스트:\n' + text[:60000]
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "{}")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}
    # 누락 키 보정
    conditions = {k: str(data.get(k, "정보 없음") or "정보 없음") for k in _COND_KEYS}
    return {"conditions": conditions, "doi": extraction.metadata.get("doi", "")}

