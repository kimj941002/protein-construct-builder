# collect.py
# 단백질 수집 파이프라인 (서비스 계층 — UI 비종속).
# Streamlit app.py 의 검색 핸들러에 인라인돼 있던 로직을 함수로 추출.
# UniProt → PDB 구조 → 복합체 → KLIFS → mutation 순으로 수집/저장한다.
from __future__ import annotations

from typing import Callable, Optional

from config import RCSB_ENTRY_API
from utils import api_call_with_retry, create_cached_session
from uniprot_fetcher import fetch_protein
from pdb_fetcher import fetch_all_structures
from complex_fetcher import process_complex
from klifs_fetcher import fetch_klifs_for_structures
from mutation_analyzer import analyze_mutations
from database import (
    get_structures_by_uniprot,
    get_klifs_bulk,
    save_last_selected_protein,
)

ProgressFn = Callable[[str, int, int], None]


def collect_protein(query: str, progress: Optional[ProgressFn] = None) -> dict:
    """
    검색어로 전체 수집 파이프라인을 실행한다 (증분 — 이미 수집된 항목은 건너뜀).

    Args:
        query:    단백질 검색어 (gene name 또는 UniProt ID)
        progress: 선택적 진행 콜백 progress(stage_text, current, total)

    Returns:
        성공: {"uniprot_id", "gene_name", "sequence_length", "n_pdb", "n_new", "message"}
        실패: {"error": str}
    """
    def _p(stage: str, cur: int = 0, tot: int = 1):
        if progress:
            try:
                progress(stage, cur, tot)
            except Exception:
                pass

    session = create_cached_session()

    # 1) UniProt
    _p("UniProt 단백질 정보 수집 중...")
    protein_data, pdb_ids, message = fetch_protein(query, session=session)
    if not protein_data:
        return {"error": message or "단백질을 찾을 수 없습니다."}
    uid = protein_data["uniprot_id"]

    # 2) PDB 구조 (증분, 신규만 collected 에 반환)
    collected = fetch_all_structures(
        pdb_ids, uid,
        progress_callback=lambda cur, tot: _p("PDB 구조 수집 중", cur, tot),
    )

    # 3) 복합체 정보 (신규 구조만)
    total_c = len(collected)
    for i, s in enumerate(collected, 1):
        _p("복합체 정보 수집 중", i, total_c)
        try:
            entry = api_call_with_retry(
                f"{RCSB_ENTRY_API}/{s['structure_id']}", session=session
            )
            if entry:
                process_complex(s["structure_id"], uid, entry, session=session)
        except Exception:
            pass

    # 4) KLIFS (미수집분 자동 보완)
    all_structs = get_structures_by_uniprot(uid)
    existing = set(get_klifs_bulk([s["structure_id"] for s in all_structs]).keys())
    pending = [s for s in all_structs if s["structure_id"] not in existing]
    if pending:
        _p("KLIFS 키나아제 정보 수집 중", 0, len(pending))
        try:
            fetch_klifs_for_structures(pending)
        except Exception:
            pass

    # 5) Mutation 분석 (신규 구조만)
    for i, s in enumerate(collected, 1):
        _p("Mutation 분석 중", i, total_c)
        try:
            analyze_mutations(s["structure_id"], uid, session)
        except Exception:
            pass

    save_last_selected_protein(uid)
    return {
        "uniprot_id": uid,
        "gene_name": protein_data.get("gene_name", ""),
        "sequence_length": protein_data.get("sequence_length", 0),
        "n_pdb": len(pdb_ids),
        "n_new": len(collected),
        "message": message or "",
    }
