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
