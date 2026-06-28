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
)
from collect import collect_protein


# ═══════════════════════════════════════════
# State
# ═══════════════════════════════════════════
class State(rx.State):
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

    # Phase 2 — PDB Article Analysis
    uploaded_pdf_path: str = ""
    uploaded_name: str = ""
    analyzing: bool = False
    analyze_status: str = ""
    analyze_result_md: str = ""

    @rx.var
    def structure_count(self) -> int:
        return len(self.structures)

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_structure_ids)

    # ── 내부 헬퍼 (동기) ──
    def _apply_protein(self, uid: str):
        """헤더 + 구조 표를 State 에 적재 (async with self 안에서 호출)."""
        p = get_protein(uid) or {}
        self.selected_uid = uid
        self.gene_name = p.get("gene_name") or ""
        self.organism = p.get("organism") or ""
        self.seq_length = p.get("sequence_length") or 0

        structs = get_structures_by_uniprot(uid)
        mut_map = get_mutations_bulk([s["structure_id"] for s in structs])
        for s in structs:
            muts = mut_map.get(s["structure_id"], [])
            s["mutations_str"] = "; ".join(m["mutation"] for m in muts) if muts else "-"
            s["rcsb_url"] = "https://www.rcsb.org/structure/" + str(s["structure_id"])
        self.structures = structs

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


# ═══════════════════════════════════════════
# AG Grid 컬럼
# ═══════════════════════════════════════════
_COLUMN_DEFS = [
    {"field": "structure_id", "headerName": "PDB ID", "pinned": "left", "filter": True,
     "checkboxSelection": True, "headerCheckboxSelection": True, "minWidth": 120},
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
            row_selection="multiple",
            pagination=True,
            pagination_page_size=20,
            on_selection_changed=State.on_select_structures,
        ),
        width="100%",
        height="600px",
    )


def _article_analysis_panel() -> rx.Component:
    """선택한 PDB 의 논문 PDF 업로드 → 4단계 분석 → Supabase 저장."""
    return rx.cond(
        State.selected_count > 0,
        rx.vstack(
            rx.divider(),
            rx.heading("📄 PDB Article Analysis", size="4"),
            rx.text(
                "선택한 PDB " + State.selected_count.to_string()
                + "개의 논문 PDF 를 업로드해 분석합니다 (수동 업로드).",
                color_scheme="gray",
            ),
            rx.upload(
                rx.vstack(rx.icon("upload"), rx.text("PDF 끌어다 놓기 또는 클릭"), align="center"),
                id="pdf_up",
                accept={"application/pdf": [".pdf"]},
                max_files=1,
                border="1px dashed var(--gray-7)",
                padding="1rem", width="340px", border_radius="8px",
            ),
            rx.button(
                "이 PDF 업로드",
                on_click=State.handle_pdf_upload(rx.upload_files(upload_id="pdf_up")),
            ),
            rx.cond(
                State.uploaded_name != "",
                rx.text("업로드됨: " + State.uploaded_name, color_scheme="green"),
            ),
            rx.button("🔬 분석 시작", on_click=State.run_article_analysis, disabled=State.analyzing),
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
                    padding="1rem", width="100%", max_height="500px", overflow="auto",
                ),
            ),
            spacing="2", align="start", width="100%",
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
        rx.heading("🧬 PCB", size="5", margin_bottom="0.5rem"),
        _nav_link("🧬 Construct Builder", "/"),
        _nav_link("🔬 Paper Analyzer", "/analyzer"),
        _nav_link("🧠 Knowledge Base", "/knowledge"),
        spacing="1", align="start",
        width="220px", height="100vh", padding="1rem",
        background=rx.color("gray", 2),
        border_right=f"1px solid {rx.color('gray', 5)}",
        position="sticky", top="0",
    )


def layout(content: rx.Component) -> rx.Component:
    return rx.hstack(
        sidebar(),
        rx.box(content, padding="1.5rem", width="100%", flex="1"),
        align="start", width="100%", spacing="0",
    )


# ═══════════════════════════════════════════
# 페이지: Construct Builder
# ═══════════════════════════════════════════
def builder_content() -> rx.Component:
    return rx.vstack(
        rx.heading("Construct Builder", size="7"),
        rx.text("단백질을 검색하면 PDB·KLIFS 정보를 수집해 표로 보여줍니다.",
                color_scheme="gray"),
        rx.hstack(
            rx.input(
                value=State.query,
                on_change=State.set_query,
                placeholder="단백질 검색 (예: MET, EGFR, P08581)",
                width="320px",
            ),
            rx.button("🔍 검색", on_click=State.search, disabled=State.collecting),
            spacing="2",
        ),
        rx.cond(
            State.collecting,
            rx.hstack(rx.spinner(), rx.text(State.collect_status), spacing="2"),
        ),
        rx.cond(
            State.not_found,
            rx.callout("단백질을 찾지 못했습니다. 검색어를 확인하세요.",
                       icon="triangle_alert", color_scheme="red"),
        ),
        rx.cond(
            State.selected_uid != "",
            rx.vstack(
                rx.heading(State.gene_name + " (" + State.selected_uid + ")", size="5"),
                rx.text("Organism: " + State.organism),
                rx.text("Sequence length: " + State.seq_length.to_string() + " aa"),
                rx.heading("PDB 구조 " + State.structure_count.to_string() + "개", size="4"),
                _structures_grid(),
                _article_analysis_panel(),
                spacing="2", width="100%", align="start",
            ),
        ),
        spacing="4", align="start", width="100%",
    )


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


def analyzer_page() -> rx.Component:
    return _placeholder("Paper Analyzer", "준비 중 — Phase 2 에서 구현합니다 (PDF 업로드 → 4단계 분석).")


def knowledge_page() -> rx.Component:
    return _placeholder("Knowledge Base", "준비 중 — Phase 3 에서 구현합니다 (주제 기반 + 의미검색).")


app = rx.App()
app.add_page(index, route="/", title="Construct Builder")
app.add_page(analyzer_page, route="/analyzer", title="Paper Analyzer")
app.add_page(knowledge_page, route="/knowledge", title="Knowledge Base")
