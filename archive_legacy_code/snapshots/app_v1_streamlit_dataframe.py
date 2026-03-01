# app.py
# Protein Construct Builder — Streamlit 메인 앱
# 실행 방법: python -m streamlit run app.py

import streamlit as st
import pandas as pd
import io
from datetime import datetime

from config import RCSB_ENTRY_API
from uniprot_fetcher import fetch_protein, normalize_gene_name
from pdb_fetcher import fetch_all_structures
from complex_fetcher import process_complex
from mutation_analyzer import analyze_mutations
from database import (
    get_all_proteins, get_protein,
    get_structures_by_uniprot,
    get_ligands_by_structure,
    get_partners_by_structure, get_all_chains_by_structure,
    get_mutations_by_structure,
    get_oligosaccharides_by_structure,
    migrate_database,
)
from uniprot_fetcher import load_sequence_from_file
from utils import create_cached_session, api_call_with_retry

# ─────────────────────────────────────────────
# 앱 시작 시 DB 마이그레이션 (컬럼 추가 등)
# ─────────────────────────────────────────────
migrate_database()

# ─────────────────────────────────────────────
# 페이지 기본 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Protein Construct Builder",
    page_icon="🧬",
    layout="wide",
)

# ─────────────────────────────────────────────
# 복합체 유형 이모지 태그
# ─────────────────────────────────────────────
COMPLEX_TYPE_EMOJI = {
    "apo":             "🔵 apo",
    "ligand":          "🟢 ligand",
    "protein-protein": "🟠 protein-protein",
    "mixed":           "🔴 mixed",
}


def format_complex_type(ct: str) -> str:
    return COMPLEX_TYPE_EMOJI.get(ct, ct or "")


# ─────────────────────────────────────────────
# 서열 포매터
# ─────────────────────────────────────────────
def format_sequence(sequence: str, block_size: int = 10) -> str:
    if not sequence:
        return "(서열 없음)"
    lines = []
    for i in range(0, len(sequence), 60):
        chunk = sequence[i:i+60]
        blocks = [chunk[j:j+block_size] for j in range(0, len(chunk), block_size)]
        lines.append(f"{i+1:>6}  {'  '.join(blocks)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# On-demand 복합체 데이터 확보
# 클릭한 구조의 ligand/partner 데이터가 DB에 없으면 즉시 API 조회
# ─────────────────────────────────────────────
def ensure_complex_data(pdb_id: str, target_uniprot_id: str, complex_type: str):
    """
    Ligand/Partner/Oligosaccharide 데이터가 DB에 없으면 API에서 가져옵니다.
    검색 시 process_complex가 실패하거나 건너뛴 구조를 보완합니다.

    Returns:
        (ligands, partners, oligos) 튜플
    """
    needs_ligand  = complex_type in ("ligand", "mixed")
    needs_partner = complex_type in ("protein-protein", "mixed")

    ligands  = get_ligands_by_structure(pdb_id)
    partners = get_partners_by_structure(pdb_id)
    oligos   = get_oligosaccharides_by_structure(pdb_id)

    # 이미 필요한 데이터가 있으면 그대로 반환
    if (not needs_ligand or ligands) and (not needs_partner or partners):
        return ligands, partners, oligos

    # 없으면 on-demand 수집
    with st.spinner(f"{pdb_id} 복합체 데이터 불러오는 중..."):
        session = create_cached_session()
        entry_data = api_call_with_retry(
            f"{RCSB_ENTRY_API}/{pdb_id}", session=session
        )
        if entry_data:
            try:
                process_complex(pdb_id, target_uniprot_id, entry_data, session=session)
            except Exception as e:
                st.warning(f"{pdb_id} 복합체 데이터 수집 오류: {e}")

    return (
        get_ligands_by_structure(pdb_id),
        get_partners_by_structure(pdb_id),
        get_oligosaccharides_by_structure(pdb_id),
    )


# ─────────────────────────────────────────────
# Excel 내보내기용 DataFrame 생성
# ─────────────────────────────────────────────
def build_export_dataframe(uniprot_id: str) -> pd.DataFrame:
    structures = get_structures_by_uniprot(uniprot_id)
    rows = []
    for s in structures:
        muts     = get_mutations_by_structure(s["structure_id"])
        mut_str  = "; ".join(m["mutation"] for m in muts) if muts else ""
        ligs  = get_ligands_by_structure(s["structure_id"])
        lig_str  = "; ".join(f"{l['ligand_id']}({l['ligand_name']})" for l in ligs)
        parts = get_partners_by_structure(s["structure_id"])
        part_str = "; ".join(
            f"{p['partner_uniprot_id']}({p['partner_gene_name']})" for p in parts
        )
        rows.append({
            "PDB ID":             s["structure_id"],
            "Method":             s["method"],
            "Resolution (A)":     s["resolution"],
            "Complex Type":       s["complex_type"],
            "Chain":              s["chain_id"],
            "Residue Range":      s["residue_range"],
            "Expression System":  s["expression_system"],
            "Host Cell Line":     s["host_cell_line"],
            "Crystal Method":     s["crystal_method"],
            "Crystal pH":         s["crystal_ph"],
            "Crystal Temp (K)":   s["crystal_temp"],
            "Crystal Details":    s["crystal_details"],
            "Space Group":        s["space_group"],
            "Mutations":          mut_str,
            "Ligands":            lig_str,
            "Partner Proteins":   part_str,
            "DOI":                s["doi"],
            "Deposition Date":    s["deposition_date"],
        })
    return pd.DataFrame(rows)


# ═════════════════════════════════════════════
# 사이드바
# ═════════════════════════════════════════════
with st.sidebar:
    st.title("⚙️ 수집 옵션")
    max_count = st.slider(
        "최대 수집 건수",
        min_value=10, max_value=2000, value=100, step=10,
        help="PDB 구조를 최대 몇 개까지 수집할지 설정합니다."
    )
    resolution_cutoff = st.number_input(
        "Resolution 컷오프 (Å, 0=제한 없음)",
        min_value=0.0, max_value=10.0, value=0.0, step=0.5,
        help="이 값 이하의 Resolution 구조만 표시합니다. 0이면 제한 없음."
    )

    # ── 수집된 단백질 브라우저 (Issue 2) ──────────────────
    st.markdown("---")
    st.subheader("📚 수집된 단백질")

    db_proteins = get_all_proteins()
    if db_proteins:
        # gene_name (uniprot_id) 형태로 표시
        protein_labels = [
            f"{p['gene_name']}  ({p['uniprot_id']})" for p in db_proteins
        ]
        # 현재 보고 있는 단백질을 기본 선택으로 표시
        current_uid = st.session_state.get("uniprot_id", "")
        default_idx = 0
        for i, p in enumerate(db_proteins):
            if p["uniprot_id"] == current_uid:
                default_idx = i
                break

        selected_label = st.radio(
            "단백질 선택",
            options=protein_labels,
            index=default_idx,
            label_visibility="collapsed",
        )
        # 라디오 선택 → session_state 업데이트
        selected_uid = db_proteins[protein_labels.index(selected_label)]["uniprot_id"]
        if selected_uid != current_uid:
            st.session_state["uniprot_id"]   = selected_uid
            st.session_state["protein_data"] = None  # DB에서 다시 로드
            st.rerun()
    else:
        st.info("아직 수집된 단백질이 없습니다.\n위에서 검색해주세요.")

    st.markdown("---")
    st.markdown("**사용법:**")
    st.markdown("1. 단백질 이름 입력 후 검색")
    st.markdown("2. 표에서 행 클릭 → 상세 정보 표시")
    st.markdown("3. 컬럼 헤더 클릭 → 정렬")
    st.markdown("4. Excel/CSV 내보내기")


# ═════════════════════════════════════════════
# 메인 영역 — 검색창
# ═════════════════════════════════════════════
st.title("🧬 Protein Construct Builder")
st.markdown("단백질 이름을 입력하면 PDB 구조 데이터를 자동 수집합니다.")

col_input, col_button = st.columns([4, 1])
with col_input:
    query = st.text_input(
        "단백질 이름 입력",
        placeholder="예: cMET, CDK2, HER2, EGFR ...",
        label_visibility="collapsed",
    )
with col_button:
    search_clicked = st.button("🔍 검색", width='stretch')


# ═════════════════════════════════════════════
# 검색 실행
# ═════════════════════════════════════════════
if search_clicked and query.strip():
    normalized, norm_msg = normalize_gene_name(query)
    if normalized != query.strip().upper():
        st.info(f"'{query}' → '{normalized}' 로 정규화하여 검색합니다.")
    if norm_msg and "[안내]" in norm_msg:
        st.warning(norm_msg)

    session = create_cached_session()

    # Step 1: UniProt
    with st.spinner("UniProt에서 단백질 정보 수집 중..."):
        protein_data, pdb_ids, message = fetch_protein(query, session=session)

    if not protein_data:
        st.error(f"단백질을 찾을 수 없습니다: {message}")
        st.stop()

    st.success("단백질 정보 수집 완료!")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("UniProt ID",    protein_data["uniprot_id"])
    c2.metric("Gene Name",     protein_data["gene_name"])
    c3.metric("Sequence Len",  f"{protein_data['sequence_length']} aa")
    c4.metric("PDB 구조 수",   f"{len(pdb_ids)}개")

    with st.expander("아미노산 서열 보기"):
        seq_path = protein_data.get("sequence_path", "")
        seq = load_sequence_from_file(seq_path) if seq_path else ""
        st.code(format_sequence(seq), language=None)

    # Step 2: PDB 구조 수집
    st.markdown("### PDB 구조 수집 중...")
    progress_bar = st.progress(0)
    status_text  = st.empty()

    def update_progress(current, total):
        progress_bar.progress(int(current / total * 100))
        status_text.text(f"{current}/{total} 처리 중...")

    collected = fetch_all_structures(
        pdb_ids, protein_data["uniprot_id"],
        max_count=max_count, progress_callback=update_progress,
    )
    progress_bar.progress(100)
    status_text.text(f"완료: {len(collected)}개 구조 수집됨")

    # Step 3: Complex 정보 수집
    complex_progress = st.progress(0)
    complex_status   = st.empty()
    total_c = len(collected)
    for idx_c, s in enumerate(collected, 1):
        complex_status.text(f"복합체 정보 수집 중: {idx_c}/{total_c}  ({s['structure_id']})")
        complex_progress.progress(int(idx_c / total_c * 100))
        try:
            entry_data = api_call_with_retry(
                f"{RCSB_ENTRY_API}/{s['structure_id']}", session=session
            )
            if entry_data:
                process_complex(
                    s["structure_id"], protein_data["uniprot_id"],
                    entry_data, session=session,
                )
        except Exception as e:
            st.warning(f"{s['structure_id']} 복합체 수집 오류: {e}")
    complex_status.text("복합체 정보 수집 완료")

    # Step 4: Mutation 분석
    with st.spinner("Mutation 분석 중..."):
        for s in collected:
            try:
                analyze_mutations(s["structure_id"], protein_data["uniprot_id"], session)
            except Exception:
                pass

    st.session_state["uniprot_id"]   = protein_data["uniprot_id"]
    st.session_state["protein_data"] = protein_data
    st.rerun()


# ═════════════════════════════════════════════
# 결과 표시
# ═════════════════════════════════════════════
if "uniprot_id" in st.session_state:
    uniprot_id   = st.session_state["uniprot_id"]
    protein_data = st.session_state.get("protein_data") or get_protein(uniprot_id)

    if not protein_data:
        st.info("단백질을 검색하세요.")
        st.stop()

    # 기본 정보 카드
    if not search_clicked:
        c1, c2, c3 = st.columns(3)
        c1.metric("UniProt ID",  protein_data["uniprot_id"])
        c2.metric("Gene Name",   protein_data["gene_name"])
        c3.metric("Sequence Len", f"{protein_data['sequence_length']} aa")
        with st.expander("아미노산 서열 보기"):
            seq_path = protein_data.get("sequence_path", "")
            seq = load_sequence_from_file(seq_path) if seq_path else ""
            st.code(format_sequence(seq), language=None)

    # ── PDB 구조 테이블 ─────────────────────────
    structures = get_structures_by_uniprot(uniprot_id)
    if not structures:
        st.info("수집된 PDB 구조가 없습니다. 검색 버튼을 눌러주세요.")
        st.stop()

    # Resolution 필터
    if resolution_cutoff > 0:
        structures = [
            s for s in structures
            if s.get("resolution") is None or s["resolution"] <= resolution_cutoff
        ]

    # DataFrame 구성
    df_rows = []
    for s in structures:
        muts    = get_mutations_by_structure(s["structure_id"])
        mut_str = ", ".join(m["mutation"] for m in muts) if muts else "-"
        df_rows.append({
            "PDB ID":       s["structure_id"],
            "Method":       (s["method"] or "")[:20],
            "Res (Å)":      s["resolution"],
            "Complex":      format_complex_type(s["complex_type"]),
            "Chain":        s["chain_id"] or "",
            "Residues":     s["residue_range"] or "",
            "Mutations":    mut_str,
            "Deposit Date": s["deposition_date"] or "",
        })

    df = pd.DataFrame(df_rows).reset_index(drop=True)

    st.markdown(f"### PDB 구조 목록 ({len(df)}개)  — 컬럼 헤더 클릭으로 정렬")
    selection = st.dataframe(
        df,
        width='stretch',
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    # ── 행 선택 → 상세 패널 ──────────────────────
    selected_rows = selection.selection.rows if selection.selection else []
    if not selected_rows:
        st.stop()

    idx          = selected_rows[0]
    selected_pdb = df.iloc[idx]["PDB ID"]
    s = next((x for x in structures if x["structure_id"] == selected_pdb), None)
    if not s:
        st.stop()

    st.markdown(f"---\n### 상세 정보: **{selected_pdb}**")

    # ── 📋 기본 정보 ──────────────────────────────
    with st.expander("📋 기본 정보", expanded=True):
        rcsb_url = f"https://www.rcsb.org/structure/{selected_pdb}"
        st.markdown(f"**RCSB-PDB:** [{selected_pdb}]({rcsb_url})")
        c1, c2 = st.columns(2)
        c1.markdown(f"**실험 방법:** {s['method'] or '-'}")
        if s.get("resolution"):
            c1.markdown(f"**해상도:** {s['resolution']} Å")
        elif s.get("mean_plddt"):
            c1.markdown(f"**평균 pLDDT:** {s['mean_plddt']}")
        else:
            c1.markdown("**해상도:** -")
        c1.markdown(f"**발현 시스템:** {s['expression_system'] or '-'}")
        c1.markdown(f"**숙주 세포:** {s['host_cell_line'] or '-'}")
        c2.markdown(f"**결정화 방법:** {s['crystal_method'] or '-'}")
        c2.markdown(f"**결정화 pH:** {s['crystal_ph'] if s['crystal_ph'] is not None else '-'}")
        c2.markdown(
            f"**결정화 온도:** {str(s['crystal_temp']) + ' K' if s['crystal_temp'] else '-'}"
        )
        c2.markdown(f"**공간군:** {s['space_group'] or '-'}")
        if s.get("doi"):
            st.markdown(f"**DOI:** [{s['doi']}](https://doi.org/{s['doi']})")
        if s.get("crystal_details"):
            with st.expander("결정화 세부사항"):
                st.write(s["crystal_details"])

    # ── on-demand 복합체 데이터 확보 ─────────────
    ligands, partners, oligos = ensure_complex_data(
        selected_pdb, uniprot_id, s["complex_type"]
    )

    # ── 💊 Ligand 테이블 ───────────────────────────
    with st.expander(
        "💊 Ligand 정보" if s["complex_type"] in ("ligand", "mixed") else "💊 Ligand 정보 (없음)",
        expanded=(s["complex_type"] in ("ligand", "mixed")),
    ):
        if s["complex_type"] in ("ligand", "mixed"):
            if ligands:
                lig_rows = []
                for l in ligands:
                    lig_rows.append({
                        "ID":     l["ligand_id"],
                        "이름":   l["ligand_name"] or "-",
                        "화학식": l["formula"] or "-",
                        "유형":   l["ligand_type"] or "-",
                        "SMILES": l.get("smiles") or "",
                    })
                lig_df = pd.DataFrame(lig_rows).reset_index(drop=True)
                st.caption("컬럼 헤더를 클릭하면 정렬됩니다.")
                st.dataframe(
                    lig_df,
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "SMILES": st.column_config.TextColumn("SMILES", width="large"),
                    },
                )
            else:
                st.info("Ligand 데이터를 찾을 수 없습니다. (리간드가 없거나 API 수집 실패)")
        else:
            st.info(f"이 구조는 '{s['complex_type']}' 유형이므로 Ligand가 없습니다.")

    # ── 🤝 Partner Protein 테이블 ─────────────────
    with st.expander(
        "🤝 Partner Protein 정보" if s["complex_type"] in ("protein-protein", "mixed")
        else "🤝 Partner Protein 정보 (없음)",
        expanded=(s["complex_type"] in ("protein-protein", "mixed")),
    ):
        if s["complex_type"] in ("protein-protein", "mixed"):
            if partners:
                chains_map = get_all_chains_by_structure(selected_pdb)
                part_rows = []
                for p in partners:
                    chains_list = chains_map.get(p["id"], [])
                    chains_str  = ", ".join(chains_list) if chains_list else (p.get("partner_chain_id") or "-")

                    part_rows.append({
                        "Entity ID":    p.get("entity_id") or "-",
                        "Molecule":     p["partner_gene_name"] or "-",
                        "Chains":       chains_str,
                        "Seq Length":   p.get("sequence_length") or "-",
                        "Organism":     p.get("organism") or "-",
                        "Details":      p.get("partner_uniprot_id") or "-",
                    })
                part_df = pd.DataFrame(part_rows).reset_index(drop=True)
                st.caption("컬럼 헤더를 클릭하면 정렬됩니다.")
                st.dataframe(
                    part_df,
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "Details": st.column_config.TextColumn("UniProt ID"),
                    },
                )
            else:
                st.info("Partner Protein 데이터를 찾을 수 없습니다.")
        else:
            st.info(f"이 구조는 '{s['complex_type']}' 유형이므로 Partner Protein이 없습니다.")

    # ── 🍬 PTM / Oligosaccharides 테이블 ─────────
    with st.expander("🍬 PTM / Oligosaccharides", expanded=bool(oligos)):
        if oligos:
            oligo_rows = []
            for o in oligos:
                oligo_rows.append({
                    "Entity ID": o.get("entity_id") or "-",
                    "이름":      o.get("name") or "-",
                    "Chain":     o.get("chain_id") or "-",
                    "결합 Chain":   o.get("linked_chain") or "-",
                    "결합 Position": o.get("linked_position") if o.get("linked_position") is not None else "-",
                    "결합 Residue":  o.get("linked_residue") or "-",
                })
            oligo_df = pd.DataFrame(oligo_rows).reset_index(drop=True)
            st.caption("컬럼 헤더를 클릭하면 정렬됩니다. | 결합 Residue: 3-letter 아미노산 코드")
            st.dataframe(oligo_df, width='stretch', hide_index=True)
        else:
            st.info("이 구조에는 Oligosaccharide / PTM 데이터가 없습니다.")

    # ── 🧬 Mutation 테이블 ───────────────────────
    with st.expander("🧬 Mutation 정보", expanded=True):
        muts = get_mutations_by_structure(selected_pdb)
        if muts:
            mut_df = pd.DataFrame([{
                "Mutation": m["mutation"],
                "Type":     m["type"],
            } for m in muts]).reset_index(drop=True)
            st.dataframe(mut_df, width='stretch', hide_index=True)
        else:
            st.success("Wild-type (mutation 없음 또는 분석 전)")

    # ── 내보내기 ──────────────────────────────────
    st.markdown("---")
    st.markdown("### 데이터 내보내기")
    export_df = build_export_dataframe(uniprot_id)

    col_excel, col_csv = st.columns(2)
    with col_excel:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="PDB_Structures")
        buf.seek(0)
        st.download_button(
            label="📥 Excel 다운로드",
            data=buf,
            file_name=f"{protein_data['gene_name']}_structures_{datetime.today().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='stretch',
        )
    with col_csv:
        csv_data = export_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 CSV 다운로드",
            data=csv_data.encode("utf-8-sig"),
            file_name=f"{protein_data['gene_name']}_structures_{datetime.today().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            width='stretch',
        )

else:
    # 초기 화면
    st.info("위 검색창에 단백질 이름을 입력하고 검색 버튼을 누르세요.")
    st.markdown("""
    **지원하는 검색어 예시:**
    - `cMET`, `c-MET` → MET 로 자동 변환
    - `HER2` → ERBB2 로 자동 변환
    - `CDK2`, `EGFR`, `TP53` 등 표준 유전자 이름
    - `PDGFR-alpha` → PDGFRA 로 자동 변환
    """)
