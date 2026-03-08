# app.py
# Protein Construct Builder — Streamlit + Ag-Grid UI (schema v2)
# 실행: python -m streamlit run app.py

from __future__ import annotations
import base64
import io
import os
from datetime import datetime

import anthropic
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, GridUpdateMode, JsCode

from complex_fetcher import process_complex
from config import PAPERS_DIR, RCSB_ENTRY_API
from database import (
    get_all_proteins,
    get_all_chains_by_structure,
    get_klifs_bulk,
    get_ligands_by_structure,
    get_mutations_by_structure,
    get_oligosaccharides_by_structure,
    get_paper_analysis,
    get_partners_by_structure,
    get_protein,
    get_structures_by_uniprot,
    migrate_database,
    upsert_paper_analysis,
)
from chat_store import (
    delete_chat, extract_related_proteins, get_chat,
    load_history, save_chat, update_chat_tags,
)
from klifs_fetcher import fetch_klifs_for_structures
from llm_query import query_db_with_llm
from mutation_analyzer import analyze_mutations
from pdb_fetcher import fetch_all_structures
from uniprot_fetcher import fetch_protein, load_sequence_from_file, normalize_gene_name
from utils import api_call_with_retry, create_cached_session

# ─────────────────────────────────────────────
# 앱 시작 시 DB 마이그레이션
# ─────────────────────────────────────────────
migrate_database()

# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Protein Construct Builder",
    page_icon="🧬",
    layout="wide",
)

# ─────────────────────────────────────────────
# 서열 포매터
# ─────────────────────────────────────────────
def format_sequence(sequence: str, block_size: int = 10) -> str:
    if not sequence:
        return "(서열 없음)"
    lines = []
    for i in range(0, len(sequence), 60):
        chunk = sequence[i:i + 60]
        blocks = [chunk[j:j + block_size] for j in range(0, len(chunk), block_size)]
        lines.append(f"{i + 1:>6}  {'  '.join(blocks)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# On-demand 복합체 데이터 확보
# ─────────────────────────────────────────────
def ensure_complex_data(pdb_id: str, target_uniprot_id: str, complex_type: str):
    needs_ligand  = complex_type in ("ligand", "mixed")
    needs_partner = complex_type in ("protein-protein", "mixed")

    ligands  = get_ligands_by_structure(pdb_id)
    partners = get_partners_by_structure(pdb_id)
    oligos   = get_oligosaccharides_by_structure(pdb_id)

    if (not needs_ligand or ligands) and (not needs_partner or partners):
        return ligands, partners, oligos

    with st.spinner(f"{pdb_id} 복합체 데이터 불러오는 중..."):
        session    = create_cached_session()
        entry_data = api_call_with_retry(f"{RCSB_ENTRY_API}/{pdb_id}", session=session)
        if entry_data:
            try:
                process_complex(pdb_id, target_uniprot_id, entry_data, session=session)
            except Exception as e:
                st.warning(f"{pdb_id} 복합체 수집 오류: {e}")

    return (
        get_ligands_by_structure(pdb_id),
        get_partners_by_structure(pdb_id),
        get_oligosaccharides_by_structure(pdb_id),
    )


# ─────────────────────────────────────────────
# 구조 목록 → DataFrame 변환
# ─────────────────────────────────────────────
def build_grid_dataframe(structures: list[dict]) -> pd.DataFrame:
    # KLIFS 데이터를 한 번에 조회 (N+1 쿼리 방지)
    sids      = [s["structure_id"] for s in structures]
    klifs_map = get_klifs_bulk(sids)

    rows = []
    for s in structures:
        muts    = get_mutations_by_structure(s["structure_id"])
        mut_str = "; ".join(m["mutation"] for m in muts) if muts else "-"
        k       = klifs_map.get(s["structure_id"])  # None if not a kinase
        rows.append({
            "PDB ID":        s["structure_id"],
            "Method":        (s["method"] or "")[:25],
            "Res (Å)":       s["resolution"],
            "Complex":       s["complex_type"] or "",
            "Chain":         s["chain_id"] or "",
            "Residue Range": s["residue_range"] or "",
            "Mutations":     mut_str,
            "Organism":      s["expression_system"] or "",
            "Expr System":   s["host_cell_line"] or "",
            "Space Group":   s["space_group"] or "",
            "Crystal pH":    s["crystal_ph"],
            "Deposit Date":  s["deposition_date"] or "",
            "DOI":           s["doi"] or "",
            # KLIFS 컬럼 (비키나아제는 "-")
            "DFG":      (k["dfg"]      if k and k.get("dfg")      else "-"),
            "αC Helix": (k["ac_helix"] if k and k.get("ac_helix") else "-"),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# Ag-Grid 옵션 빌더
# ─────────────────────────────────────────────
def build_grid_options(df: pd.DataFrame):
    # PDB ID 셀에 RCSB 링크 렌더러
    pdb_link_renderer = JsCode("""
        class PdbLinkRenderer {
            init(params) {
                this.eGui = document.createElement('a');
                this.eGui.innerText = params.value;
                this.eGui.setAttribute(
                    'href',
                    'https://www.rcsb.org/structure/' + params.value
                );
                this.eGui.setAttribute('target', '_blank');
                this.eGui.style.color = '#1a73e8';
                this.eGui.style.textDecoration = 'none';
                this.eGui.style.fontWeight = 'bold';
            }
            getGui() { return this.eGui; }
        }
    """)

    # Method / Complex — 인라인 드롭다운 floatingFilter (Community 버전)
    def _make_select_filter_js(class_name: str, values: list[str]) -> JsCode:
        opts = "".join(
            f'<option value="{v}">{v}</option>'
            for v in values
        )
        return JsCode(f"""
            class {class_name} {{
                init(params) {{
                    this.eGui = document.createElement('select');
                    this.eGui.className = 'ag-input-field-input';
                    this.eGui.style.width = '100%';
                    this.eGui.style.height = '100%';
                    this.eGui.style.margin = '0';
                    this.eGui.style.padding = '0 4px';
                    this.eGui.style.boxSizing = 'border-box';
                    this.eGui.style.cursor = 'pointer';
                    this.eGui.innerHTML = '<option value="">(전체)</option>{opts}';
                    this.eGui.addEventListener('change', () => {{
                        const val = this.eGui.value;
                        if (val === '') {{
                            params.parentFilterInstance(i => i.setModel(null));
                        }} else {{
                            params.parentFilterInstance(i => i.setModel({{
                                filterType: 'text',
                                type: 'equals',
                                filter: val,
                            }}));
                        }}
                        params.api.onFilterChanged();
                    }});
                }}
                getGui() {{ return this.eGui; }}
                onParentModelChanged(model) {{
                    this.eGui.value = (model && model.filter) ? model.filter : '';
                }}
            }}
        """)

    method_values  = sorted(df["Method"].dropna().unique().tolist())
    complex_values = sorted(df["Complex"].dropna().unique().tolist())
    method_select_js  = _make_select_filter_js("MethodSelectFilter",  method_values)
    complex_select_js = _make_select_filter_js("ComplexSelectFilter", complex_values)

    gb = GridOptionsBuilder.from_dataframe(df)

    # 기본 컬럼 설정
    gb.configure_default_column(
        filter=True,
        sortable=True,
        resizable=True,
        floatingFilter=True,
        minWidth=80,
        wrapText=False,
    )

    # 개별 컬럼 설정
    gb.configure_column(
        "PDB ID",
        pinned="left",
        width=100,
        filter="agTextColumnFilter",
        cellRenderer=pdb_link_renderer,
    )
    # Method / Complex — agTextColumnFilter(equals) + 커스텀 드롭다운 floatingFilter
    gb.configure_column(
        "Method",
        width=180,
        filter="agTextColumnFilter",
        filterParams={"filterOptions": ["equals"], "suppressAndOrCondition": True},
        floatingFilter=True,
        floatingFilterComponent=method_select_js,
    )
    gb.configure_column(
        "Complex",
        width=160,
        filter="agTextColumnFilter",
        filterParams={"filterOptions": ["equals"], "suppressAndOrCondition": True},
        floatingFilter=True,
        floatingFilterComponent=complex_select_js,
    )
    gb.configure_column("Res (Å)",       width=100, filter="agNumberColumnFilter",
                        type=["numericColumn", "numberColumnFilter"])
    gb.configure_column("Chain",         width=80,  filter="agTextColumnFilter")
    gb.configure_column("Residue Range", width=140, filter="agTextColumnFilter")
    gb.configure_column("Mutations",     width=200, filter="agTextColumnFilter")
    gb.configure_column("Organism",      width=180, filter="agTextColumnFilter")
    gb.configure_column("Expr System",   width=180, filter="agTextColumnFilter")
    gb.configure_column("Space Group",   width=130, filter="agTextColumnFilter")
    gb.configure_column("Crystal pH",    width=100, filter="agNumberColumnFilter",
                        type=["numericColumn", "numberColumnFilter"])
    gb.configure_column("Deposit Date",  width=130, filter="agDateColumnFilter")
    gb.configure_column("DOI",           width=200, filter="agTextColumnFilter")

    # KLIFS 컬럼 — DFG / αC Helix: 드롭다운, 나머지: 일반 필터
    dfg_values    = sorted(df["DFG"].dropna().unique().tolist())
    helix_values  = sorted(df["αC Helix"].dropna().unique().tolist())
    dfg_select_js   = _make_select_filter_js("DfgSelectFilter",   dfg_values)
    helix_select_js = _make_select_filter_js("HelixSelectFilter", helix_values)

    gb.configure_column(
        "DFG",
        width=110,
        filter="agTextColumnFilter",
        filterParams={"filterOptions": ["equals"], "suppressAndOrCondition": True},
        floatingFilter=True,
        floatingFilterComponent=dfg_select_js,
    )
    gb.configure_column(
        "αC Helix",
        width=110,
        filter="agTextColumnFilter",
        filterParams={"filterOptions": ["equals"], "suppressAndOrCondition": True},
        floatingFilter=True,
        floatingFilterComponent=helix_select_js,
    )
    # 단일 행 선택 (클릭 → 상세 패널)
    gb.configure_selection(
        selection_mode="single",
        use_checkbox=False,
    )

    gb.configure_grid_options(
        domLayout="normal",
        rowHeight=36,
        headerHeight=48,
        suppressMovableColumns=False,
        enableCellTextSelection=True,
    )

    return gb.build()


# ═════════════════════════════════════════════
# 사이드바
# ═════════════════════════════════════════════
with st.sidebar:
    st.title("⚙️ 수집 옵션")
    st.subheader("📚 수집된 단백질")

    db_proteins = get_all_proteins()
    if db_proteins:
        protein_labels = [f"{p['gene_name']}  ({p['uniprot_id']})" for p in db_proteins]
        current_uid    = st.session_state.get("uniprot_id", "")
        default_idx    = next(
            (i for i, p in enumerate(db_proteins) if p["uniprot_id"] == current_uid), 0
        )
        selected_label = st.radio(
            "단백질 선택",
            options=protein_labels,
            index=default_idx,
            label_visibility="collapsed",
        )
        selected_uid = db_proteins[protein_labels.index(selected_label)]["uniprot_id"]
        if selected_uid != current_uid:
            st.session_state["uniprot_id"]   = selected_uid
            st.session_state["protein_data"] = None
            st.session_state.pop("ai_selected_chat_id", None)
            st.rerun()
    else:
        st.info("아직 수집된 단백질이 없습니다.\n위에서 검색해주세요.")


# ═════════════════════════════════════════════
# 사이드바 — AI 질의응답
# st.stop() 이전에 렌더링되므로 단백질 미선택 상태에서도 항상 표시됩니다.
# ═════════════════════════════════════════════
with st.sidebar:
    st.divider()
    st.subheader("AI 질의응답")
    st.caption("DB 전체를 대상으로 자연어로 질문하세요.")

    ai_q = st.text_area(
        "질문 입력",
        placeholder=(
            "예: DFGin 구조 중 해상도 2Å 미만인 것들을 알려줘\n"
            "예: EGFR과 복합체를 형성하는 파트너 단백질 목록"
        ),
        height=110,
        label_visibility="collapsed",
        key="ai_question_input",
    )

    ai_clicked = st.button("질의하기", key="ai_query_btn", use_container_width=True)

    if ai_clicked:
        if ai_q.strip():
            try:
                _api_key = st.secrets.get("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
            except (AttributeError, FileNotFoundError):
                _api_key = os.getenv("ANTHROPIC_API_KEY")

            if not _api_key:
                st.error("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
            else:
                with st.spinner("AI가 DB를 분석 중입니다..."):
                    try:
                        _result = query_db_with_llm(ai_q, api_key=_api_key)
                    except Exception as _e:
                        _result = {"queries": [], "answer": "", "error": str(_e)}

                # 질문·답변 텍스트에서 관련 단백질 자동 추출
                _all_proteins = get_all_proteins()
                _related_ids = extract_related_proteins(ai_q, _result, _all_proteins)
                # 매칭된 단백질이 없으면 현재 선택 단백질로 폴백
                if not _related_ids:
                    _cur = st.session_state.get("uniprot_id", "")
                    if _cur:
                        _related_ids = [_cur]
                # 파일에 저장 후 해당 기록을 선택 상태로 전환
                _saved = save_chat(_related_ids, ai_q, _result)
                st.session_state["ai_selected_chat_id"] = _saved["id"]
                st.rerun()
        else:
            st.warning("질문을 입력해주세요.")

    # ── 대화 기록 리스트 ──────────────────────
    _cur_uid = st.session_state.get("uniprot_id", "")
    _history = load_history(uniprot_id=_cur_uid) if _cur_uid else []
    if _history:
        st.divider()
        st.caption(f"대화 기록 ({len(_history)}개)")
        _selected_id = st.session_state.get("ai_selected_chat_id")

        for _rec in _history:
            _label = _rec["question"][:24] + ("…" if len(_rec["question"]) > 24 else "")
            _is_sel = _selected_id == _rec["id"]
            _col_q, _col_menu = st.columns([5, 1])

            with _col_q:
                if st.button(
                    _label,
                    key=f"chat_hist_{_rec['id']}",
                    use_container_width=True,
                    type="primary" if _is_sel else "secondary",
                ):
                    st.session_state["ai_selected_chat_id"] = _rec["id"]
                    st.session_state.pop("tagging_chat_id", None)
                    st.rerun()

            with _col_menu:
                with st.popover("···", use_container_width=True):
                    if st.button(
                        "🗑 삭제",
                        key=f"del_{_rec['id']}",
                        use_container_width=True,
                    ):
                        delete_chat(_rec["id"])
                        if st.session_state.get("ai_selected_chat_id") == _rec["id"]:
                            st.session_state.pop("ai_selected_chat_id", None)
                        st.session_state.pop("tagging_chat_id", None)
                        st.rerun()

                    if st.button(
                        "🏷 Tag",
                        key=f"tag_btn_{_rec['id']}",
                        use_container_width=True,
                    ):
                        st.session_state["tagging_chat_id"] = _rec["id"]
                        st.rerun()

    # ── Tag 편집 패널 ─────────────────────────
    _tagging_id = st.session_state.get("tagging_chat_id")
    if _tagging_id:
        _tag_chat = get_chat(_tagging_id)
        if _tag_chat:
            st.divider()
            _preview = _tag_chat["question"][:22] + ("…" if len(_tag_chat["question"]) > 22 else "")
            st.caption(f"🏷 Tag 편집: {_preview}")

            _all_ps = get_all_proteins()
            _opt_map = {
                f"{p['gene_name']}  ({p['uniprot_id']})": p["uniprot_id"]
                for p in _all_ps
            }
            _cur_ids  = _tag_chat.get("related_uniprot_ids", [])
            _cur_lbls = [k for k, v in _opt_map.items() if v in _cur_ids]

            _selected_lbls = st.multiselect(
                "단백질 선택",
                options=list(_opt_map.keys()),
                default=_cur_lbls,
                key=f"tag_ms_{_tagging_id}",
                label_visibility="collapsed",
                placeholder="단백질 검색 또는 선택…",
            )

            _tc1, _tc2 = st.columns(2)
            with _tc1:
                if st.button("저장", key="tag_save", use_container_width=True, type="primary"):
                    update_chat_tags(_tagging_id, [_opt_map[l] for l in _selected_lbls])
                    st.session_state.pop("tagging_chat_id", None)
                    st.rerun()
            with _tc2:
                if st.button("취소", key="tag_cancel", use_container_width=True):
                    st.session_state.pop("tagging_chat_id", None)
                    st.rerun()
        else:
            st.session_state.pop("tagging_chat_id", None)


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
    search_clicked = st.button("🔍 검색", use_container_width=True)


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
    c1.metric("UniProt ID",   protein_data["uniprot_id"])
    c2.metric("Gene Name",    protein_data["gene_name"])
    c3.metric("Sequence Len", f"{protein_data['sequence_length']} aa")
    c4.metric("PDB 구조 수",  f"{len(pdb_ids)}개")

    with st.expander("아미노산 서열 보기"):
        seq = load_sequence_from_file(protein_data.get("sequence_path", ""))
        st.code(format_sequence(seq), language=None)

    # Step 2: PDB 구조 수집 (증분 — 신규 항목만)
    st.markdown(f"### PDB 구조 수집 중... (UniProt 전체 {len(pdb_ids)}개)")
    progress_bar = st.progress(0)
    status_text  = st.empty()

    def update_progress(current, total):
        progress_bar.progress(int(current / total * 100))
        status_text.text(f"신규 {current}/{total} 처리 중...")

    collected = fetch_all_structures(
        pdb_ids, protein_data["uniprot_id"],
        progress_callback=update_progress,
    )
    progress_bar.progress(100)
    if collected:
        status_text.text(f"완료: 신규 {len(collected)}개 구조 수집됨")
    else:
        status_text.text("이미 모든 PDB가 수집되어 있습니다.")

    # Step 3: 복합체 정보 수집 (신규 구조만)
    total_c = len(collected)
    if total_c > 0:
        complex_progress = st.progress(0)
        complex_status   = st.empty()
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

    # Step 3.5: KLIFS 수집 (전체 구조 대상 — 미수집분 자동 보완)
    all_structures_for_klifs = get_structures_by_uniprot(protein_data["uniprot_id"])
    existing_klifs = set(
        get_klifs_bulk([s["structure_id"] for s in all_structures_for_klifs]).keys()
    )
    klifs_pending = [
        s for s in all_structures_for_klifs
        if s["structure_id"] not in existing_klifs
    ]
    if klifs_pending:
        with st.spinner(f"KLIFS 키나아제 구조 정보 수집 중... (미수집 {len(klifs_pending)}개)"):
            klifs_n = fetch_klifs_for_structures(klifs_pending)
        if klifs_n > 0:
            st.caption(f"KLIFS 수집 완료: {klifs_n}개 키나아제 구조 정보 저장됨")

    # Step 4: Mutation 분석 (신규 구조만)
    if collected:
        with st.spinner("Mutation 분석 중..."):
            for s in collected:
                try:
                    analyze_mutations(s["structure_id"], protein_data["uniprot_id"], session)
                except Exception:
                    pass

    st.session_state["uniprot_id"]   = protein_data["uniprot_id"]
    st.session_state["protein_data"] = protein_data
    st.session_state.pop("ai_selected_chat_id", None)
    st.rerun()


# ═════════════════════════════════════════════
# 결과 표시
# ═════════════════════════════════════════════
if "uniprot_id" not in st.session_state:
    st.info("위 검색창에 단백질 이름을 입력하고 검색 버튼을 누르세요.")
    st.markdown("""
**지원하는 검색어 예시:**
- `cMET`, `c-MET` → MET 로 자동 변환
- `HER2` → ERBB2 로 자동 변환
- `CDK2`, `EGFR`, `TP53` 등 표준 유전자 이름
- `PDGFR-alpha` → PDGFRA 로 자동 변환
    """)
    st.stop()

uniprot_id   = st.session_state["uniprot_id"]
protein_data = st.session_state.get("protein_data") or get_protein(uniprot_id)

if not protein_data:
    st.info("단백질을 검색하세요.")
    st.stop()

# ── 단백질 헤더 카드 ────────────────────────
if not search_clicked:
    c1, c2, c3 = st.columns(3)
    c1.metric("UniProt ID",   protein_data["uniprot_id"])
    c2.metric("Gene Name",    protein_data["gene_name"])
    c3.metric("Sequence Len", f"{protein_data['sequence_length']} aa")
    with st.expander("아미노산 서열 보기"):
        seq = load_sequence_from_file(protein_data.get("sequence_path", ""))
        st.code(format_sequence(seq), language=None)

# ── 구조 목록 로드 ───────────────────────────
structures = get_structures_by_uniprot(uniprot_id)
if not structures:
    st.info("수집된 PDB 구조가 없습니다. 검색 버튼을 눌러주세요.")
    st.stop()

# ── Ag-Grid ──────────────────────────────────
st.markdown(f"### PDB 구조 목록 ({len(structures)}개)")

df = build_grid_dataframe(structures)

st.caption("열 헤더 아래 필터 입력창을 사용하세요. Method·Complex는 드롭다운으로 선택됩니다.")

go = build_grid_options(df)

grid_response = AgGrid(
    df,
    gridOptions=go,
    data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
    update_mode=GridUpdateMode.MODEL_CHANGED,
    fit_columns_on_grid_load=False,
    theme="streamlit",
    height=480,
    allow_unsafe_jscode=True,
    enable_enterprise_modules=False,
)

# ── 내보내기 (드롭다운 + Ag-Grid 필터 모두 적용된 결과) ──
filtered_df = pd.DataFrame(grid_response["data"])
row_count   = len(filtered_df)
gene_name   = protein_data.get("gene_name", "protein")
date_str    = datetime.today().strftime("%Y%m%d")

st.markdown(f"**필터 결과: {row_count}개** 행 (전체 {len(df)}개) — 아래 버튼으로 내보내기")
col_excel, col_csv = st.columns(2)

with col_excel:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        filtered_df.to_excel(writer, index=False, sheet_name="PDB_Structures")
    buf.seek(0)
    st.download_button(
        label="📥 Excel 내보내기",
        data=buf,
        file_name=f"{gene_name}_filtered_{date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

with col_csv:
    csv_data = filtered_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        label="📥 CSV 내보내기",
        data=csv_data.encode("utf-8-sig"),
        file_name=f"{gene_name}_filtered_{date_str}.csv",
        mime="text/csv",
        use_container_width=True,
    )

# ═════════════════════════════════════════════
# AI 질의 결과 상세 패널
# ai_selected_chat_id 가 설정된 경우에만 렌더링됩니다.
# 단백질 전환·재검색 시 세션에서 이 키가 제거되어 패널이 사라집니다.
# ═════════════════════════════════════════════
_selected_chat_id = st.session_state.get("ai_selected_chat_id")
if _selected_chat_id:
    _chat = get_chat(_selected_chat_id)
    if _chat:
        with st.expander("🤖 AI 질의 결과", expanded=True):
            st.markdown(f"**질문:** {_chat['question']}")
            st.caption(f"질의 시각: {_chat['timestamp'][:19].replace('T', ' ')}")
            st.divider()

            if _chat.get("error") and not _chat.get("queries"):
                st.error(f"오류 발생: {_chat['error']}")
            else:
                _queries = _chat.get("queries", [])
                if _queries:
                    with st.expander(f"실행된 SQL 쿼리 ({len(_queries)}개)", expanded=False):
                        for _i, _q in enumerate(_queries, 1):
                            st.markdown(f"**쿼리 {_i}**")
                            st.code(_q["sql"], language="sql")
                            if _q.get("error"):
                                st.error(f"오류: {_q['error']}")
                            elif _q.get("rows") is not None:
                                _rows = _q["rows"]
                                if _rows:
                                    st.dataframe(
                                        pd.DataFrame(_rows).reset_index(drop=True),
                                        use_container_width=True,
                                        hide_index=True,
                                    )
                                else:
                                    st.caption("결과 없음 (0 rows)")

                if _chat.get("answer"):
                    st.markdown("### 답변")
                    st.markdown(_chat["answer"])
                elif _chat.get("error"):
                    st.warning(f"부분 실행 후 오류: {_chat['error']}")

# ── 상세 패널 (행 선택 시) ───────────────────
selected_rows = grid_response.get("selected_rows")
if selected_rows is None or (hasattr(selected_rows, "__len__") and len(selected_rows) == 0):
    st.stop()

# selected_rows: DataFrame 또는 list[dict] 모두 처리
if hasattr(selected_rows, "to_dict"):
    sel_list = selected_rows.to_dict("records")
else:
    sel_list = list(selected_rows)

if not sel_list:
    st.stop()

selected_pdb = sel_list[0]["PDB ID"]
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

# ── on-demand 복합체 데이터 확보 ────────────
ligands, partners, oligos = ensure_complex_data(
    selected_pdb, uniprot_id, s["complex_type"]
)

# ── 💊 Ligand 정보 ────────────────────────────
with st.expander(
    "💊 Ligand 정보" if s["complex_type"] in ("ligand", "mixed") else "💊 Ligand 정보 (없음)",
    expanded=(s["complex_type"] in ("ligand", "mixed")),
):
    if s["complex_type"] in ("ligand", "mixed"):
        if ligands:
            lig_df = pd.DataFrame([{
                "ID":     l["ligand_id"],
                "이름":   l["ligand_name"] or "-",
                "화학식": l["formula"] or "-",
                "유형":   l["ligand_type"] or "-",
                "SMILES": l.get("smiles") or "",
            } for l in ligands])
            st.caption("컬럼 헤더를 클릭하면 정렬됩니다.")
            st.dataframe(
                lig_df,
                use_container_width=True,
                hide_index=True,
                column_config={"SMILES": st.column_config.TextColumn("SMILES", width="large")},
            )
        else:
            st.info("Ligand 데이터를 찾을 수 없습니다.")
    else:
        st.info(f"이 구조는 '{s['complex_type']}' 유형이므로 Ligand가 없습니다.")

# ── 🤝 Partner Protein 정보 ───────────────────
with st.expander(
    "🤝 Partner Protein 정보" if s["complex_type"] in ("protein-protein", "mixed")
    else "🤝 Partner Protein 정보 (없음)",
    expanded=(s["complex_type"] in ("protein-protein", "mixed")),
):
    if s["complex_type"] in ("protein-protein", "mixed"):
        if partners:
            chains_map = get_all_chains_by_structure(selected_pdb)
            part_rows  = []
            for p in partners:
                chains_list = chains_map.get(p["id"], [])
                chains_str  = ", ".join(chains_list) if chains_list else (p.get("partner_chain_id") or "-")
                part_rows.append({
                    "Entity ID":  p.get("entity_id") or "-",
                    "Molecule":   p["partner_gene_name"] or "-",
                    "Chains":     chains_str,
                    "Seq Length": p.get("sequence_length") or "-",
                    "Organism":   p.get("organism") or "-",
                    "UniProt ID": p.get("partner_uniprot_id") or "-",
                })
            st.caption("컬럼 헤더를 클릭하면 정렬됩니다.")
            st.dataframe(
                pd.DataFrame(part_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Partner Protein 데이터를 찾을 수 없습니다.")
    else:
        st.info(f"이 구조는 '{s['complex_type']}' 유형이므로 Partner Protein이 없습니다.")

# ── 🍬 PTM / Oligosaccharides ─────────────────
with st.expander("🍬 PTM / Oligosaccharides", expanded=bool(oligos)):
    if oligos:
        oligo_df = pd.DataFrame([{
            "Entity ID":      o.get("entity_id") or "-",
            "이름":           o.get("name") or "-",
            "Chain":          o.get("chain_id") or "-",
            "결합 Chain":     o.get("linked_chain") or "-",
            "결합 Position":  o.get("linked_position") if o.get("linked_position") is not None else "-",
            "결합 Residue":   o.get("linked_residue") or "-",
        } for o in oligos])
        st.caption("결합 Residue: 3-letter 아미노산 코드 (예: ASN = N-glycosylation 부위)")
        st.dataframe(oligo_df, use_container_width=True, hide_index=True)
    else:
        st.info("이 구조에는 Oligosaccharide / PTM 데이터가 없습니다.")

# ── 🧬 Mutation 정보 ──────────────────────────
with st.expander("🧬 Mutation 정보", expanded=True):
    muts = get_mutations_by_structure(selected_pdb)
    if muts:
        mut_df = pd.DataFrame([{
            "Mutation": m["mutation"],
            "Position": m["position"],
            "Type":     m["mutation_type"],
        } for m in muts])
        st.dataframe(mut_df, use_container_width=True, hide_index=True)
    else:
        st.success("Wild-type (mutation 없음 또는 분석 전)")

# ── 📄 논문 PDF 분석 ───────────────────────────
_ANALYSIS_PROMPT = """당신은 단백질 생화학 및 구조생물학 전문가입니다.
아래 논문에서 PDB ID [{pdb_id}]에 해당하는 단백질 construct의
재조합 단백질 생산 및 구조 결정에 관한 모든 실험 조건을 빠짐없이 추출하십시오.

[추출 항목]

1. 논문 개요
   - 논문 주제 (2-3문장)
   - 주요 결론
   - 구조생물학적 Insight

2. Construct Design
   - 유전자 출처 (human cDNA / synthetic / codon-optimized 등)
   - 사용 도메인 또는 잔기 범위 (ex: residues 696-1022)
   - Construct 제작 시 도입한 돌연변이 (point mutation, deletion, alanine scan 등)
   - 부착 태그 종류, 위치 (N-말단/C-말단), 링커 서열
   - 태그 절단 프로테아제 및 절단 조건
   - 발현 벡터/플라스미드 이름 및 특징

3. 발현 (Expression)
   - 발현 숙주 및 균주 (E. coli BL21(DE3), Rosetta, Sf9, Hi5, HEK293F 등)
   - 배지 종류 및 조성 (LB, TB, 2xYT, ESF921, FreeStyle 293 등)
   - 유도 방법 (IPTG 농도, 유도 시점 OD, 발현 온도, 발현 시간)
   - 발현 수율 (mg per liter of culture 등)
   - 공동 발현 샤페론 또는 보조 단백질 여부

4. 세포 파쇄 및 정제 (Purification)
   - 세포 파쇄 방법 (초음파파쇄, 고압균질기, 동결융해 등)
   - 파쇄 버퍼 전체 조성 (염, pH, 환원제, 프로테아제 억제제 등)
   - 1차 친화 크로마토그래피 종류, 컬럼명, 용출 조건
   - 이온교환 크로마토그래피 조건 (있는 경우)
   - 겔 여과(SEC) 조건 (컬럼명, 버퍼 조성, 유속)
   - 기타 정제 단계
   - 각 단계별 버퍼 조성 (명시된 경우 전부 기재)

5. 최종 버퍼 및 보관 조건
   - 최종 보관 버퍼 전체 조성 (염 종류/농도, pH, 환원제, 글리세롤, 계면활성제 등)
   - 단백질 농도 (사용 농도 또는 보관 농도)
   - 보관 온도 및 방법 (4°C, -80°C, flash freeze 등)

6. 품질 관리 (QC)
   - SDS-PAGE, 웨스턴 블롯 확인 여부
   - SEC-MALS, DLS 등 분자량/균질성 확인
   - 기능 확인 실험 (효소 활성, 결합 실험 등)
   - 순도 수준 (>95% 등)

7. 결정화 / Cryo-EM 조건 (해당하는 경우)
   - 결정화 방법 (vapor diffusion, batch 등)
   - 결정화 시약 및 조성, pH
   - 첨가제 (ligand, inhibitor 공결정 조건)
   - Cryo 조건 (cryoprotectant)

8. 특수 처리 사항
   - 인산화, 당 제거, 리폴딩 여부 및 방법
   - 복합체 형성 방법 (복합체 구조인 경우)
   - 특이 실험 조건 또는 최적화 과정

각 항목에 대해 논문에서 찾은 정보를 구체적 수치와 함께 기재하십시오.
정보가 논문에 명시되지 않은 경우 반드시 "정보 없음"으로 표기하십시오.
번호와 소제목 구조를 유지하여 가독성 있게 작성하십시오."""


def _extract_pdf_text(pdf_path: str) -> str:
    """pypdf로 PDF에서 텍스트를 추출합니다."""
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[Page {i + 1}]\n{text}")
    return "\n\n".join(pages)


def _run_paper_analysis(pdb_id: str, pdf_path: str) -> str:
    """Claude API로 논문 PDF를 분석하여 원문 텍스트를 반환합니다.
    1차: PDF 문서 직접 전송 (이미지/표 포함)
    2차: PDF → 텍스트 추출 후 전송 (fallback)
    """
    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    # Streamlit Cloud Secrets 우선, 없으면 로컬 .env 환경변수 fallback
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        api_key = os.getenv("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)
    prompt_text = _ANALYSIS_PROMPT.format(pdb_id=pdb_id)

    # 1차 시도: PDF 문서 직접 전송
    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }],
        )
    except Exception as e:
        err_str = str(e)
        if "Could not process PDF" in err_str or "invalid_request_error" in err_str:
            # 2차 시도: 텍스트 추출 fallback
            extracted = _extract_pdf_text(pdf_path)
            if not extracted.strip():
                raise ValueError(
                    "PDF에서 텍스트를 추출할 수 없습니다. "
                    "스캔 전용 PDF이거나 암호화된 파일일 수 있습니다."
                )
            msg = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": (
                        f"[논문 텍스트 — PDF에서 추출]\n\n{extracted}\n\n{prompt_text}"
                    ),
                }],
            )
        else:
            raise

    return msg.content[0].text


_STATUS_LABEL = {
    "none":      "미첨부",
    "uploaded":  "첨부완료",
    "analyzing": "분석 중...",
    "completed": "분석완료 ✓",
    "error":     "오류",
}

# expander 전에 상태 미리 조회 → expanded 파라미터에 사용
pa     = get_paper_analysis(selected_pdb) or {}
status = pa.get("status", "none")

with st.expander("📄 논문 PDF 분석", expanded=(status == "completed")):
    st.markdown(f"**상태:** {_STATUS_LABEL.get(status, status)}")

    uploaded_file = st.file_uploader(
        "논문 PDF 업로드",
        type=["pdf"],
        key=f"pdf_{selected_pdb}",
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        os.makedirs(PAPERS_DIR, exist_ok=True)
        pdf_path = os.path.join(PAPERS_DIR, f"{selected_pdb}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        upsert_paper_analysis({
            "structure_id": selected_pdb,
            "pdf_path":     pdf_path,
            "status":       "uploaded",
        })
        pa     = get_paper_analysis(selected_pdb) or {}
        status = pa.get("status", "uploaded")
        st.success("PDF 업로드 완료")

    if status in ("uploaded", "completed", "error"):
        if st.button("🔬 분석 시작", key=f"analyze_{selected_pdb}"):
            pdf_path = pa.get("pdf_path", "")
            if pdf_path and os.path.exists(pdf_path):
                upsert_paper_analysis({
                    "structure_id": selected_pdb,
                    "pdf_path":     pdf_path,
                    "status":       "analyzing",
                })
                with st.spinner("Claude AI가 논문을 분석 중입니다..."):
                    try:
                        raw_text = _run_paper_analysis(selected_pdb, pdf_path)
                        upsert_paper_analysis({
                            "structure_id": selected_pdb,
                            "pdf_path":     pdf_path,
                            "status":       "completed",
                            "raw_text":     raw_text,
                            "analyzed_at":  datetime.now().isoformat(),
                        })
                        pa     = get_paper_analysis(selected_pdb) or {}
                        status = "completed"
                        st.success("분석 완료!")
                        # st.rerun() 제거 — rerun 시 Ag-Grid 선택 초기화로 expander 사라짐
                    except Exception as e:
                        upsert_paper_analysis({
                            "structure_id": selected_pdb,
                            "pdf_path":     pdf_path,
                            "status":       "error",
                        })
                        st.error(f"분석 오류: {e}")
            else:
                st.warning("PDF 파일을 먼저 업로드하세요.")

    if status == "completed" and pa:
        st.markdown(pa.get("raw_text") or "")
        st.caption(f"분석일시: {pa.get('analyzed_at', '')[:19]}")
