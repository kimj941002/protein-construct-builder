# paper_store.py
# 논문 분석 결과를 Supabase(Postgres) papers 테이블에 저장/조회한다.
# (구) Google Sheets/Drive(drive_storage.py) 대체. UNIFIED_MIGRATION_PLAN.md §2-B / Stage 2.
#
# 저장 범위: 메타데이터 + 분석 MD 전문(analysis_md). PDF 원본 바이너리 보관은
# 향후 Supabase Storage 로 분리(현재 pdf_object_path 는 NULL).
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from db_config import get_engine


def _extract_title_from_synthesis(synthesis: str) -> str:
    """종합 보고서에서 논문 제목 추출 (drive_storage 로직 보존)."""
    for pattern in [
        r"[#*]*\s*(?:논문\s*제목|Paper\s*Title|Title)\s*[:：]\s*(.+)",
        r"^#\s+(.+)",
    ]:
        m = re.search(pattern, synthesis, re.MULTILINE | re.IGNORECASE)
        if m:
            title = m.group(1).strip().strip("*").strip('"').strip("'")
            if len(title) > 10:
                return title[:200]
    return ""


def _extract_authors_from_synthesis(synthesis: str) -> str:
    """종합 보고서에서 저자 추출."""
    m = re.search(
        r"[#*]*\s*(?:저자|Authors?)\s*[:：]\s*(.+)",
        synthesis, re.MULTILINE | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().strip("*")[:300]
    return ""


class PaperStore:
    """Supabase papers 테이블 기반 논문 분석 저장소 (DriveStorage 대체)."""

    def save(
        self,
        pdf_path: str,
        analysis_md: str,
        result,            # AnalysisResult (.synthesis, .token_usage)
        model: str,
        lang: str,
        cost: float,
        doi: str = "",
        title: str = "",
        authors: str = "",
        tags: str = "",
        structure_id: str | None = None,
    ) -> dict:
        """논문 분석 결과를 papers 테이블에 1행 삽입. Returns {paper_id, title}."""
        paper_id = str(uuid.uuid4())[:8]
        if not title:
            title = _extract_title_from_synthesis(result.synthesis)
        if not title:
            title = Path(pdf_path).stem if pdf_path else "(제목 없음)"
        if not authors:
            authors = _extract_authors_from_synthesis(result.synthesis)

        params = {
            "paper_id":        paper_id,
            "title":           title,
            "doi":             doi,
            "authors":         authors,
            "analyzed_at":     datetime.now(),
            "model":           model,
            "language":        lang,
            "input_tokens":    result.token_usage.get("input_tokens"),
            "output_tokens":   result.token_usage.get("output_tokens"),
            "cost_usd":        cost,
            "analysis_md":     analysis_md,
            "pdf_object_path": None,   # 향후 Supabase Storage 경로
            "tags":            tags,
            "structure_id":    structure_id,
        }
        with get_engine().begin() as conn:
            conn.execute(text("""
                INSERT INTO papers
                    (paper_id, title, doi, authors, analyzed_at, model, language,
                     input_tokens, output_tokens, cost_usd, analysis_md,
                     pdf_object_path, tags, structure_id)
                VALUES
                    (:paper_id, :title, :doi, :authors, :analyzed_at, :model, :language,
                     :input_tokens, :output_tokens, :cost_usd, :analysis_md,
                     :pdf_object_path, :tags, :structure_id)
            """), params)
        return {"paper_id": paper_id, "title": title}

    def list_papers(self) -> list[dict]:
        """저장된 논문 목록을 오래된 순으로 반환 (UI 에서 reversed 하여 최신순 표시)."""
        with get_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM papers ORDER BY created_at ASC")
            ).mappings().all()
        return [dict(r) for r in rows]
