"""
Protein Construct Builder — 통합 Reflex 앱
==========================================
UNIFIED_MIGRATION_PLAN.md / REFLEX_UNIFIED_PLAN.md.
좌측 사이드바로 Construct Builder / Paper Analyzer / Knowledge Base 를 오간다.
DB 접근은 database.py·collect.py 의 서비스 함수만 호출(스키마 재정의 없음).
"""
import asyncio
import os
import sys

# repo 루트(서비스 계층 모듈 위치)를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reflex as rx

from .ag_grid_wrap import ag_grid

from database import (
    get_all_proteins,
    get_protein,
    get_structures_by_uniprot,
    get_mutations_bulk,
    get_domains_by_uniprot,
    get_structure,
    get_mutations_by_structure,
    get_ligands_by_structure,
    get_partners_by_structure,
    get_all_chains_by_structure,
    get_oligosaccharides_by_structure,
    get_klifs_by_structure,
    get_paper_analysis,
    upsert_paper_conditions,
)
from uniprot_fetcher import load_sequence_from_file
from collect import collect_protein
import knowledge as K

# 도메인 트랙 색상
_DOMAIN_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
                  "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD"]


def _format_sequence(seq: str) -> str:
    """UniProt 형식: 60자/줄 + 위치 번호."""
    if not seq:
        return "(서열 없음)"
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        blocks = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
        lines.append(f"{i + 1:>6}  {blocks}")
    return "\n".join(lines)


def _app_password() -> str:
    """공유 접속 비밀번호 (secrets/env 의 APP_PASSWORD). 미설정이면 게이트 비활성(로컬 개발)."""
    try:
        from db_config import _load_secrets
        return str(_load_secrets().get("APP_PASSWORD", "") or "")
    except Exception:
        import os
        return os.environ.get("APP_PASSWORD", "")


# ═══════════════════════════════════════════
# State
# ═══════════════════════════════════════════
class State(rx.State):
    # 접속 게이트 (공유 비밀번호)
    authenticated: bool = False
    password_input: str = ""
    auth_error: str = ""

    query: str = ""
    collecting: bool = False
    collect_status: str = ""
    not_found: bool = False

    selected_uid: str = ""
    gene_name: str = ""
    organism: str = ""
    seq_length: int = 0
    structures: list[dict] = []
    selected_structure_ids: list[str] = []

    # PDB database — 도메인 / 시퀀스 / 세부 패널 / 구조화 분석
    domains: list[dict] = []
    sequence_fmt: str = ""
    detail_sid: str = ""
    detail: dict = {}
    detail_mutations: list[dict] = []
    detail_ligands: list[dict] = []
    detail_partners: list[dict] = []
    detail_oligos: list[dict] = []
    detail_klifs_str: str = ""
    pdb_conditions: dict = {}
    has_conditions: bool = False
    cond_analyzing: bool = False
    cond_status: str = ""

    # Phase 2 — PDB Article Analysis
    uploaded_pdf_path: str = ""
    uploaded_name: str = ""
    analyzing: bool = False
    analyze_status: str = ""
    analyze_result_md: str = ""

    # Phase 3 — Knowledge Base
    kb_topics: list[dict] = []
    kb_new_topic_name: str = ""
    kb_selected_topic_id: int = 0
    kb_selected_topic_name: str = ""
    kb_links: list[dict] = []
    kb_insights: list[dict] = []
    kb_link_type: str = "structure"
    kb_link_id: str = ""
    kb_search_query: str = ""
    kb_search_results: list[dict] = []
    kb_busy: bool = False
    kb_status: str = ""

    @rx.var
    def structure_count(self) -> int:
        return len(self.structures)

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_structure_ids)

    @rx.var
    def gate_open(self) -> bool:
        """비밀번호 미설정(로컬)이면 통과, 설정됐으면 로그인해야 통과."""
        return self.authenticated or not bool(_app_password())

    @rx.event
    def set_password_input(self, v: str):
        self.password_input = v

    @rx.event
    def do_login(self):
        pw = _app_password()
        if pw and self.password_input == pw:
            self.authenticated = True
            self.auth_error = ""
            self.password_input = ""
        else:
            self.auth_error = "비밀번호가 올바르지 않습니다."

    # ── 내부 헬퍼 (동기) ──
    def _apply_protein(self, uid: str):
        """헤더 + 도메인 + 시퀀스 + 구조 표를 State 에 적재 (async with self 안에서 호출)."""
        p = get_protein(uid) or {}
        self.selected_uid = uid
        self.gene_name = p.get("gene_name") or ""
        self.organism = p.get("organism") or ""
        seq_len = p.get("sequence_length") or 0
        self.seq_length = seq_len

        # 도메인 트랙 (위치% 미리 계산)
        doms = get_domains_by_uniprot(uid)
        out = []
        for i, d in enumerate(doms):
            start = d.get("start_pos") or 0
            end = d.get("end_pos") or seq_len or 1
            denom = seq_len or end or 1
            left = max(0.0, (start - 1) / denom * 100)
            width = max(0.8, (end - start + 1) / denom * 100)
            nm = d.get("name") or "domain"
            out.append({
                "name": nm,
                "range": f"{start}-{end}",
                "label": f"{nm} ({start}-{end})",
                "left": f"{left:.2f}%", "width": f"{width:.2f}%",
                "color": _DOMAIN_COLORS[i % len(_DOMAIN_COLORS)],
            })
        self.domains = out

        # 시퀀스 (UniProt 형식)
        seq = load_sequence_from_file(p.get("sequence_path", "")) if p.get("sequence_path") else ""
        self.sequence_fmt = _format_sequence(seq)

        # 구조 표
        structs = get_structures_by_uniprot(uid)
        mut_map = get_mutations_bulk([s["structure_id"] for s in structs])
        for s in structs:
            muts = mut_map.get(s["structure_id"], [])
            s["mutations_str"] = "; ".join(m["mutation"] for m in muts) if muts else "-"
        self.structures = structs
        # 세부 패널 초기화
        self.detail_sid = ""
        self.detail = {}

    def _load_detail(self, sid: str):
        """클릭한 PDB 의 세부 + 기존 구조화 분석을 적재."""
        self.detail_sid = sid
        self.detail = get_structure(sid) or {}
        self.detail_mutations = get_mutations_by_structure(sid)
        self.detail_ligands = get_ligands_by_structure(sid)
        partners = get_partners_by_structure(sid)
        chains_map = get_all_chains_by_structure(sid)
        for pp in partners:
            pp["chains_str"] = ", ".join(chains_map.get(pp["id"], [])) or (pp.get("partner_chain_id") or "-")
        self.detail_partners = partners
        self.detail_oligos = get_oligosaccharides_by_structure(sid)
        k = get_klifs_by_structure(sid)
        if k and (k.get("dfg") or k.get("ac_helix")):
            self.detail_klifs_str = f"DFG: {k.get('dfg') or '-'} / αC-helix: {k.get('ac_helix') or '-'}"
        else:
            self.detail_klifs_str = ""
        # 기존 구조화 분석
        pa = get_paper_analysis(sid)
        if pa and pa.get("structured"):
            self.pdb_conditions = pa["structured"]
            self.has_conditions = True
        else:
            self.pdb_conditions = {}
            self.has_conditions = False
        self.cond_status = ""

    def _resolve_or_collect(self, q: str) -> str | None:
        """DB 우선(UniProt ID/gene 매칭) → 없으면 신규 수집. uid 반환(실패 None). (스레드 실행)"""
        p = get_protein(q.upper())
        if p:
            return p["uniprot_id"]
        for pr in get_all_proteins():
            if (pr.get("gene_name") or "").upper() == q.upper():
                return pr["uniprot_id"]
        res = collect_protein(q)
        if res.get("error"):
            return None
        return res["uniprot_id"]

    # ── 이벤트 ──
    @rx.event
    def set_query(self, value: str):
        self.query = value

    @rx.event(background=True)
    async def search(self):
        q = self.query.strip()
        if not q:
            return
        async with self:
            self.collecting = True
            self.not_found = False
            self.collect_status = f"'{q}' 조회/수집 중... (새 단백질은 수 분 소요)"
            self.selected_uid = ""
            self.structures = []
            self.selected_structure_ids = []
        uid = await asyncio.to_thread(self._resolve_or_collect, q)
        async with self:
            self.collecting = False
            self.collect_status = ""
            if uid:
                self._apply_protein(uid)
            else:
                self.not_found = True

    @rx.event
    def on_select_structures(self, rows: list[dict]):
        self.selected_structure_ids = [
            r.get("structure_id") for r in rows if r.get("structure_id")
        ]

    @rx.event
    def on_row_clicked(self, row: dict):
        sid = row.get("structure_id")
        if sid:
            self._load_detail(sid)

    # ── PDB별 논문 구조화 분석 (실험 세부조건) ──
    def _do_conditions(self, pdf: str, sid: str) -> dict:
        from paper_pipeline import extract_construct_conditions
        r = extract_construct_conditions(pdf)
        if r.get("error"):
            return r
        upsert_paper_conditions(sid, r["conditions"])
        return {"conditions": r["conditions"]}

    @rx.event(background=True)
    async def run_pdb_conditions(self):
        async with self:
            if not self.uploaded_pdf_path or not self.detail_sid:
                self.cond_status = "PDB 선택 + PDF 업로드가 필요합니다."
                return
            self.cond_analyzing = True
            self.cond_status = "논문 구조화 분석 중... (수십 초~수 분)"
            pdf = self.uploaded_pdf_path
            sid = self.detail_sid
        res = await asyncio.to_thread(self._do_conditions, pdf, sid)
        async with self:
            self.cond_analyzing = False
            if res.get("error"):
                self.cond_status = "오류: " + res["error"]
            else:
                self.cond_status = "분석 완료 — Supabase 저장됨"
                self.pdb_conditions = res["conditions"]
                self.has_conditions = True

    # ── Phase 2: 논문 업로드 + 분석 ──
    @rx.event
    async def handle_pdf_upload(self, files: list[rx.UploadFile]):
        if not files:
            return
        import tempfile
        f = files[0]
        data = await f.read()
        fd, path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        self.uploaded_pdf_path = path
        self.uploaded_name = getattr(f, "name", None) or getattr(f, "filename", "") or "uploaded.pdf"
        self.analyze_result_md = ""
        self.analyze_status = ""

    def _do_analysis(self, pdf: str, targets: list[str]) -> dict:
        """(스레드) 분석 실행 + papers/paper_analysis 저장."""
        from datetime import datetime
        from paper_pipeline import run_full_analysis
        from paper_store import PaperStore
        from database import upsert_paper_analysis

        r = run_full_analysis(pdf, model="claude-sonnet-4-6", lang="ko")
        if r.get("error"):
            return r
        primary = targets[0] if targets else None
        PaperStore().save(
            pdf_path=pdf, analysis_md=r["output_md"], result=r["result"],
            model="claude-sonnet-4-6", lang="ko", cost=r.get("cost", 0.0),
            doi=r.get("doi", ""), structure_id=primary,
        )
        for sid in targets:
            upsert_paper_analysis({
                "structure_id": sid, "status": "completed",
                "raw_text": r["output_md"], "analyzed_at": datetime.now(),
            })
        return {"output_md": r["output_md"]}

    @rx.event(background=True)
    async def run_article_analysis(self):
        async with self:
            if not self.uploaded_pdf_path or not self.selected_structure_ids:
                self.analyze_status = "먼저 PDB 선택 + PDF 업로드가 필요합니다."
                return
            self.analyzing = True
            self.analyze_status = "논문 분석 중... (수 분 소요)"
            pdf = self.uploaded_pdf_path
            targets = list(self.selected_structure_ids)
        res = await asyncio.to_thread(self._do_analysis, pdf, targets)
        async with self:
            self.analyzing = False
            if res.get("error"):
                self.analyze_status = "오류: " + res["error"]
            else:
                self.analyze_status = "분석 완료 — Supabase(papers/paper_analysis) 저장됨"
                self.analyze_result_md = res["output_md"]

    # ── 독립 Paper Analyzer (구조 연결 없이 papers 저장) ──
    def _do_standalone(self, pdf: str) -> dict:
        from paper_pipeline import run_full_analysis
        from paper_store import PaperStore
        r = run_full_analysis(pdf, model="claude-sonnet-4-6", lang="ko")
        if r.get("error"):
            return r
        PaperStore().save(
            pdf_path=pdf, analysis_md=r["output_md"], result=r["result"],
            model="claude-sonnet-4-6", lang="ko", cost=r.get("cost", 0.0),
            doi=r.get("doi", ""), structure_id=None,
        )
        return {"output_md": r["output_md"]}

    @rx.event(background=True)
    async def run_standalone_analysis(self):
        async with self:
            if not self.uploaded_pdf_path:
                self.analyze_status = "PDF 를 먼저 업로드하세요."
                return
            self.analyzing = True
            self.analyze_status = "논문 분석 중... (수 분 소요)"
            pdf = self.uploaded_pdf_path
        res = await asyncio.to_thread(self._do_standalone, pdf)
        async with self:
            self.analyzing = False
            if res.get("error"):
                self.analyze_status = "오류: " + res["error"]
            else:
                self.analyze_status = "분석 완료 — Supabase(papers) 저장됨"
                self.analyze_result_md = res["output_md"]

    # ── Phase 3: Knowledge Base ──
    @rx.event
    def set_kb_new_topic_name(self, v: str):
        self.kb_new_topic_name = v

    @rx.event
    def set_kb_link_type(self, v: str):
        self.kb_link_type = v

    @rx.event
    def set_kb_link_id(self, v: str):
        self.kb_link_id = v

    @rx.event
    def set_kb_search_query(self, v: str):
        self.kb_search_query = v

    @rx.event
    def kb_load_topics(self):
        self.kb_topics = K.list_topics()

    @rx.event
    def kb_create_topic(self):
        name = self.kb_new_topic_name.strip()
        if not name:
            return
        K.create_topic(name)
        self.kb_new_topic_name = ""
        self.kb_topics = K.list_topics()

    @rx.event
    def kb_select_topic(self, topic_id: int, name: str):
        self.kb_selected_topic_id = topic_id
        self.kb_selected_topic_name = name
        self.kb_links = K.get_links(topic_id)
        self.kb_insights = K.list_insights(topic_id)

    @rx.event
    def kb_add_link(self):
        if not self.kb_selected_topic_id or not self.kb_link_id.strip():
            return
        K.add_link(self.kb_selected_topic_id, self.kb_link_type, self.kb_link_id.strip())
        self.kb_link_id = ""
        self.kb_links = K.get_links(self.kb_selected_topic_id)

    @rx.event(background=True)
    async def kb_synthesize(self):
        async with self:
            if not self.kb_selected_topic_id:
                return
            self.kb_busy = True
            self.kb_status = "LLM 종합 인사이트 생성 중... (수십 초)"
            tid = self.kb_selected_topic_id
        res = await asyncio.to_thread(K.synthesize_topic, tid)
        async with self:
            self.kb_busy = False
            if res.get("error"):
                self.kb_status = "오류: " + res["error"]
            else:
                self.kb_status = "종합 완료 — insight 저장·임베딩됨"
                self.kb_insights = K.list_insights(tid)

    @rx.event(background=True)
    async def kb_reindex(self):
        async with self:
            self.kb_busy = True
            self.kb_status = "기존 논문/분석 임베딩(재색인) 중..."
        n = await asyncio.to_thread(K.reindex_papers)
        async with self:
            self.kb_busy = False
            self.kb_status = f"재색인 완료: {n}건 임베딩됨"

    @rx.event(background=True)
    async def kb_search(self):
        q = self.kb_search_query.strip()
        if not q:
            return
        async with self:
            self.kb_busy = True
            self.kb_status = "의미검색 중..."
        results = await asyncio.to_thread(K.semantic_search, q, 8)
        async with self:
            self.kb_busy = False
            self.kb_status = ""
            self.kb_search_results = results


# ═══════════════════════════════════════════
# AG Grid 컬럼
# ═══════════════════════════════════════════
_COLUMN_DEFS = [
    {"field": "structure_id", "headerName": "PDB ID", "pinned": "left", "filter": True,
     "minWidth": 120},
    {"field": "method", "headerName": "Method", "filter": True},
    {"field": "resolution", "headerName": "Res (Å)", "filter": "agNumberColumnFilter", "maxWidth": 110},
    {"field": "complex_type", "headerName": "Complex", "filter": True},
    {"field": "chain_id", "headerName": "Chain", "filter": True, "maxWidth": 100},
    {"field": "residue_range", "headerName": "Residue Range", "filter": True},
    {"field": "mutations_str", "headerName": "Mutations", "filter": True},
    {"field": "expression_system", "headerName": "Organism", "filter": True},
    {"field": "host_cell_line", "headerName": "Expr System", "filter": True},
    {"field": "space_group", "headerName": "Space Group", "filter": True},
    {"field": "deposition_date", "headerName": "Deposit Date", "filter": True},
    {"field": "doi", "headerName": "DOI", "filter": True},
]


def _structures_grid() -> rx.Component:
    return rx.box(
        ag_grid(
            column_defs=_COLUMN_DEFS,
            row_data=State.structures,
            default_col_def={"sortable": True, "resizable": True, "floatingFilter": True},
            row_selection="single",
            pagination=True,
            pagination_page_size=20,
            on_row_clicked=State.on_row_clicked,
        ),
        width="100%",
        height="520px",
    )


# ── Family & Domains (UniProt feature 트랙) ──
def _domain_seg(d: dict) -> rx.Component:
    return rx.tooltip(
        rx.box(position="absolute", left=d["left"], width=d["width"],
               top="0", height="26px", background=d["color"], border_radius="4px"),
        content=d["label"],
    )


def _domain_row(d: dict) -> rx.Component:
    return rx.hstack(
        rx.box(width="12px", height="12px", background=d["color"], border_radius="2px"),
        rx.text(d["name"], weight="bold", size="2"),
        rx.text(d["range"], size="2", color_scheme="gray"),
        spacing="2", align="center",
    )


def _domain_track() -> rx.Component:
    return rx.cond(
        State.domains.length() > 0,
        rx.vstack(
            rx.heading("Family & Domains", size="4"),
            rx.box(
                rx.foreach(State.domains, _domain_seg),
                position="relative", width="100%", height="26px",
                background=rx.color("gray", 4), border_radius="4px",
            ),
            rx.vstack(rx.foreach(State.domains, _domain_row), spacing="1", align="start"),
            spacing="2", align="start", width="100%",
        ),
    )


# ── Sequence (UniProt 형식) ──
def _sequence_view() -> rx.Component:
    return rx.cond(
        State.sequence_fmt != "",
        rx.vstack(
            rx.heading("Sequence", size="4"),
            rx.box(
                rx.text(State.sequence_fmt, white_space="pre", font_family="monospace",
                        font_size="0.78rem"),
                border="1px solid var(--gray-5)", border_radius="8px", padding="0.75rem",
                width="100%", overflow="auto", max_height="240px",
            ),
            spacing="2", align="start", width="100%",
        ),
    )


# ── PDB 세부 패널 (행 클릭 시) ──
def _kv(label: str, value) -> rx.Component:
    return rx.box(
        rx.text(label, size="1", color_scheme="gray"),
        rx.text(value, size="2", weight="bold"),
        min_width="120px",
    )


def _mut_item(m: dict) -> rx.Component:
    return rx.text("• ", m["mutation"], " [", m["mutation_type"], "]", size="2")


def _lig_item(l: dict) -> rx.Component:
    return rx.text("• ", l["ligand_id"], "  ", l["ligand_name"], size="2")


def _partner_item(p: dict) -> rx.Component:
    return rx.text("• ", p["partner_gene_name"], " (", p["partner_uniprot_id"],
                   ")  chains: ", p["chains_str"], size="2")


def _oligo_item(o: dict) -> rx.Component:
    return rx.text("• ", o["name"], "  @chain ", o["linked_chain"], size="2")


def _cond_card(label: str, key: str) -> rx.Component:
    return rx.box(
        rx.heading(label, size="3"),
        rx.text(State.pdb_conditions[key], white_space="pre-wrap", size="2"),
        border="1px solid var(--gray-5)", border_radius="8px", padding="0.75rem", width="100%",
    )


def _pdb_detail_panel() -> rx.Component:
    return rx.cond(
        State.detail_sid != "",
        rx.vstack(
            rx.divider(),
            rx.hstack(
                rx.heading("🔬 " + State.detail_sid, size="4"),
                rx.link("RCSB ↗", href="https://www.rcsb.org/structure/" + State.detail_sid,
                        is_external=True, size="2"),
                spacing="3", align="center",
            ),
            rx.flex(
                _kv("Method", State.detail["method"]),
                _kv("Resolution", State.detail["resolution"]),
                _kv("Complex", State.detail["complex_type"]),
                _kv("Chain", State.detail["chain_id"]),
                _kv("Residue range", State.detail["residue_range"]),
                _kv("Organism", State.detail["expression_system"]),
                _kv("Expr system", State.detail["host_cell_line"]),
                _kv("Space group", State.detail["space_group"]),
                _kv("Crystal pH", State.detail["crystal_ph"]),
                _kv("Deposit", State.detail["deposition_date"]),
                wrap="wrap", spacing="4", width="100%",
            ),
            rx.cond(State.detail_klifs_str != "",
                    rx.text("KLIFS — " + State.detail_klifs_str, size="2", color_scheme="gray")),
            rx.cond(State.detail_mutations.length() > 0,
                    rx.vstack(rx.text("Mutations", weight="bold", size="2"),
                              rx.foreach(State.detail_mutations, _mut_item), spacing="0", align="start")),
            rx.cond(State.detail_ligands.length() > 0,
                    rx.vstack(rx.text("Ligands", weight="bold", size="2"),
                              rx.foreach(State.detail_ligands, _lig_item), spacing="0", align="start")),
            rx.cond(State.detail_partners.length() > 0,
                    rx.vstack(rx.text("Partner proteins", weight="bold", size="2"),
                              rx.foreach(State.detail_partners, _partner_item), spacing="0", align="start")),
            rx.cond(State.detail_oligos.length() > 0,
                    rx.vstack(rx.text("PTM / Oligosaccharides", weight="bold", size="2"),
                              rx.foreach(State.detail_oligos, _oligo_item), spacing="0", align="start")),

            # 논문 구조화 분석
            rx.divider(),
            rx.heading("📄 논문 실험조건 분석", size="4"),
            rx.text("이 PDB 를 발표한 논문 PDF 를 업로드하면 주제·통찰 + 실험 세부조건을 정리합니다.",
                    color_scheme="gray", size="2"),
            rx.upload(
                rx.hstack(rx.icon("upload"), rx.text("PDF 선택/끌어놓기"), align="center"),
                id="pdf_cond", accept={"application/pdf": [".pdf"]}, max_files=1,
                border="1px dashed var(--gray-7)", padding="0.75rem", border_radius="8px", width="320px",
            ),
            rx.hstack(
                rx.button("업로드", on_click=State.handle_pdf_upload(rx.upload_files(upload_id="pdf_cond"))),
                rx.button("🔬 구조화 분석", on_click=State.run_pdb_conditions, disabled=State.cond_analyzing),
                spacing="2",
            ),
            rx.cond(State.uploaded_name != "", rx.text("업로드됨: " + State.uploaded_name,
                                                       color_scheme="green", size="2")),
            rx.cond(State.cond_analyzing, rx.hstack(rx.spinner(), rx.text(State.cond_status), spacing="2")),
            rx.cond((~State.cond_analyzing) & (State.cond_status != ""),
                    rx.text(State.cond_status, size="2")),
            rx.cond(
                State.has_conditions,
                rx.vstack(
                    _cond_card("주제", "topic"),
                    _cond_card("핵심 통찰", "insights"),
                    _cond_card("DNA Cloning", "cloning"),
                    _cond_card("단백질 발현", "expression"),
                    _cond_card("단백질 정제", "purification"),
                    _cond_card("결정화", "crystallization"),
                    _cond_card("활성·분석 어세이", "assay"),
                    spacing="2", width="100%",
                ),
            ),
            spacing="3", align="start", width="100%",
        ),
    )


# ═══════════════════════════════════════════
# 레이아웃 (사이드바)
# ═══════════════════════════════════════════
def _nav_link(label: str, href: str) -> rx.Component:
    return rx.link(
        label, href=href,
        width="100%", padding="0.5rem 0.75rem", border_radius="6px",
        _hover={"background": rx.color("accent", 4)},
    )


def sidebar() -> rx.Component:
    return rx.vstack(
        rx.heading("🧬 Structure research", size="5", margin_bottom="0.5rem"),
        _nav_link("🗄️ PDB database", "/"),
        _nav_link("🤖 Custom LLM", "/analyzer"),
        _nav_link("🧠 Knowledge Base", "/knowledge"),
        spacing="1", align="start",
        width="220px", height="100vh", padding="1rem",
        background=rx.color("gray", 2),
        border_right=f"1px solid {rx.color('gray', 5)}",
        position="sticky", top="0",
    )


def _login_view() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.heading("🔒 Structure research", size="6"),
            rx.text("접속하려면 공유 비밀번호를 입력하세요.", color_scheme="gray"),
            rx.input(
                value=State.password_input, on_change=State.set_password_input,
                placeholder="비밀번호", type="password", width="280px",
            ),
            rx.button("들어가기", on_click=State.do_login, width="280px"),
            rx.cond(State.auth_error != "", rx.text(State.auth_error, color_scheme="red")),
            spacing="3", align="center",
        ),
        height="100vh", width="100%",
    )


def layout(content: rx.Component) -> rx.Component:
    return rx.cond(
        State.gate_open,
        rx.hstack(
            sidebar(),
            rx.box(content, padding="1.5rem", width="100%", flex="1"),
            align="start", width="100%", spacing="0",
        ),
        _login_view(),
    )


# ═══════════════════════════════════════════
# 페이지: Construct Builder
# ═══════════════════════════════════════════
def _center_search() -> rx.Component:
    """단백질 미선택 시 — UniProt 홈처럼 가운데 큰 검색창."""
    return rx.center(
        rx.vstack(
            rx.heading("PDB database", size="8"),
            rx.text("UniProt 단백질을 검색하면 PDB·KLIFS 정보를 수집해 보여줍니다.",
                    color_scheme="gray"),
            rx.hstack(
                rx.input(value=State.query, on_change=State.set_query,
                         placeholder="단백질 검색 (예: MET, EGFR, P08581)",
                         width="440px", size="3"),
                rx.button("🔍 검색", on_click=State.search, disabled=State.collecting, size="3"),
                spacing="2",
            ),
            rx.cond(State.collecting,
                    rx.hstack(rx.spinner(), rx.text(State.collect_status), spacing="2")),
            rx.cond(State.not_found,
                    rx.callout("단백질을 찾지 못했습니다. 검색어를 확인하세요.",
                               icon="triangle_alert", color_scheme="red")),
            spacing="4", align="center",
        ),
        min_height="72vh", width="100%",
    )


def _results_view() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.input(value=State.query, on_change=State.set_query,
                     placeholder="단백질 검색", width="300px"),
            rx.button("검색", on_click=State.search, disabled=State.collecting),
            rx.cond(State.collecting, rx.spinner()),
            spacing="2",
        ),
        rx.heading(State.gene_name + " (" + State.selected_uid + ")", size="6"),
        rx.text("Organism: " + State.organism + " · Length: "
                + State.seq_length.to_string() + " aa", color_scheme="gray"),
        _domain_track(),
        _sequence_view(),
        rx.heading("PDB 구조 " + State.structure_count.to_string() + "개", size="4"),
        rx.text("표에서 PDB 행을 클릭하면 아래에 세부정보가 나타납니다.",
                color_scheme="gray", size="2"),
        _structures_grid(),
        _pdb_detail_panel(),
        spacing="3", align="start", width="100%",
    )


def builder_content() -> rx.Component:
    return rx.cond(State.selected_uid != "", _results_view(), _center_search())


def index() -> rx.Component:
    return layout(builder_content())


def _placeholder(title: str, desc: str) -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading(title, size="7"),
            rx.callout(desc, icon="info"),
            spacing="3", align="start",
        )
    )


def analyzer_content() -> rx.Component:
    return rx.vstack(
        rx.heading("Paper Analyzer", size="7"),
        rx.text("논문 PDF 를 업로드하면 4단계로 분석합니다 (PDB 연결 없이 독립 저장).",
                color_scheme="gray"),
        rx.upload(
            rx.vstack(rx.icon("upload"), rx.text("PDF 끌어다 놓기 또는 클릭"), align="center"),
            id="pdf_up_standalone",
            accept={"application/pdf": [".pdf"]},
            max_files=1,
            border="1px dashed var(--gray-7)",
            padding="1rem", width="340px", border_radius="8px",
        ),
        rx.button(
            "이 PDF 업로드",
            on_click=State.handle_pdf_upload(rx.upload_files(upload_id="pdf_up_standalone")),
        ),
        rx.cond(
            State.uploaded_name != "",
            rx.text("업로드됨: " + State.uploaded_name, color_scheme="green"),
        ),
        rx.button("🔬 분석 시작", on_click=State.run_standalone_analysis, disabled=State.analyzing),
        rx.cond(
            State.analyzing,
            rx.hstack(rx.spinner(), rx.text(State.analyze_status), spacing="2"),
        ),
        rx.cond(
            (~State.analyzing) & (State.analyze_status != ""),
            rx.text(State.analyze_status),
        ),
        rx.cond(
            State.analyze_result_md != "",
            rx.box(
                rx.markdown(State.analyze_result_md),
                border="1px solid var(--gray-5)", border_radius="8px",
                padding="1rem", width="100%", max_height="600px", overflow="auto",
            ),
        ),
        spacing="3", align="start", width="100%",
    )


def analyzer_page() -> rx.Component:
    return layout(analyzer_content())


def _search_result(r: dict) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.badge(r["source_type"]),
            rx.text(r["source_id"], weight="bold"),
            rx.spacer(),
            rx.text(r["score"]),
            width="100%",
        ),
        rx.text(r["chunk_text"], size="2", color_scheme="gray"),
        border="1px solid var(--gray-4)", border_radius="6px",
        padding="0.5rem", width="100%",
    )


def _topic_item(t: dict) -> rx.Component:
    return rx.button(
        t["name"],
        on_click=State.kb_select_topic(t["id"], t["name"]),
        variant="soft", width="100%",
    )


def _link_item(l: dict) -> rx.Component:
    return rx.text("• ", l["entity_type"], ": ", l["entity_id"], size="2")


def _insight_item(ins: dict) -> rx.Component:
    return rx.box(
        rx.heading(ins["title"], size="3"),
        rx.markdown(ins["body"]),
        border="1px solid var(--gray-5)", border_radius="8px",
        padding="0.75rem", width="100%",
    )


def _topic_detail() -> rx.Component:
    return rx.cond(
        State.kb_selected_topic_id != 0,
        rx.vstack(
            rx.heading("📌 " + State.kb_selected_topic_name, size="4"),
            rx.hstack(
                rx.select(
                    ["protein", "structure", "paper"],
                    value=State.kb_link_type, on_change=State.set_kb_link_type,
                ),
                rx.input(
                    value=State.kb_link_id, on_change=State.set_kb_link_id,
                    placeholder="ID (예: P08581 / 3CCN)", width="220px",
                ),
                rx.button("연결 추가", on_click=State.kb_add_link),
            ),
            rx.text("연결된 항목:", weight="bold"),
            rx.vstack(rx.foreach(State.kb_links, _link_item), spacing="1", align="start"),
            rx.button("🧠 종합 인사이트 생성", on_click=State.kb_synthesize, disabled=State.kb_busy),
            rx.heading("인사이트", size="5"),
            rx.vstack(rx.foreach(State.kb_insights, _insight_item), spacing="2", width="100%"),
            spacing="2", align="start", flex="1",
        ),
    )


def knowledge_content() -> rx.Component:
    return rx.vstack(
        rx.heading("Knowledge Base", size="7"),
        rx.text("주제로 단백질·구조·논문을 묶고, LLM 종합 인사이트와 의미검색으로 지식을 누적합니다.",
                color_scheme="gray"),
        rx.heading("🔎 의미검색", size="4"),
        rx.hstack(
            rx.input(value=State.kb_search_query, on_change=State.set_kb_search_query,
                     placeholder="예: MET DFG-out 저해제", width="360px"),
            rx.button("검색", on_click=State.kb_search),
            rx.button("재색인", on_click=State.kb_reindex, variant="outline"),
        ),
        rx.cond(State.kb_busy, rx.hstack(rx.spinner(), rx.text(State.kb_status), spacing="2")),
        rx.cond((~State.kb_busy) & (State.kb_status != ""),
                rx.text(State.kb_status, color_scheme="gray")),
        rx.vstack(rx.foreach(State.kb_search_results, _search_result), width="100%", spacing="1"),
        rx.divider(),
        rx.heading("🧠 주제(Topic)", size="4"),
        rx.hstack(
            rx.input(value=State.kb_new_topic_name, on_change=State.set_kb_new_topic_name,
                     placeholder="새 주제 이름", width="280px"),
            rx.button("주제 생성", on_click=State.kb_create_topic),
        ),
        rx.hstack(
            rx.vstack(rx.foreach(State.kb_topics, _topic_item),
                      width="240px", spacing="1", align="start"),
            _topic_detail(),
            align="start", spacing="4", width="100%",
        ),
        spacing="3", align="start", width="100%",
        on_mount=State.kb_load_topics,
    )


def knowledge_page() -> rx.Component:
    return layout(knowledge_content())


app = rx.App()
app.add_page(index, route="/", title="PDB database")
app.add_page(analyzer_page, route="/analyzer", title="Custom LLM")
app.add_page(knowledge_page, route="/knowledge", title="Knowledge Base")
