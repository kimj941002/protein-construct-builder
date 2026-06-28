"""
Protein Construct Builder — Reflex 앱 (Stage 3 핵심 뷰)
======================================================
UNIFIED_MIGRATION_PLAN.md §6. Streamlit 과 같은 Supabase + 같은 서비스 계층 위에서
동작하는 단백질/구조 조회 뷰. DB 접근은 database.py 의 서비스 함수만 호출한다
(rx.Model 로 스키마를 재정의하지 않음 — 서비스 계층이 단일 DB 진입점).
"""
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


class State(rx.State):
    """단백질/구조 조회 상태."""

    proteins: list[dict] = []
    selected_uid: str = ""
    gene_name: str = ""
    organism: str = ""
    seq_length: int = 0
    structures: list[dict] = []
    selected_structure_ids: list[str] = []   # AG Grid 다중선택 결과 (Phase 2 논문분석용)

    @rx.var
    def protein_uids(self) -> list[str]:
        return [str(p.get("uniprot_id", "")) for p in self.proteins]

    @rx.var
    def structure_count(self) -> int:
        return len(self.structures)

    @rx.event
    def load_proteins(self):
        """페이지 진입 시 수집된 단백질 목록 로드 (서비스 계층)."""
        self.proteins = get_all_proteins()

    @rx.event
    def on_select_structures(self, rows: list[dict]):
        """AG Grid 다중선택 → 선택된 structure_id 보관 (Phase 2 PDB 논문분석)."""
        self.selected_structure_ids = [
            r.get("structure_id") for r in rows if r.get("structure_id")
        ]

    @rx.event
    def select_protein(self, uid: str):
        """단백질 선택 → 헤더 + 구조 목록(+ mutation) 로드."""
        if not uid:
            return
        self.selected_uid = uid
        p = get_protein(uid) or {}
        self.gene_name = p.get("gene_name") or ""
        self.organism = p.get("organism") or ""
        self.seq_length = p.get("sequence_length") or 0

        structs = get_structures_by_uniprot(uid)
        mut_map = get_mutations_bulk([s["structure_id"] for s in structs])
        for s in structs:
            muts = mut_map.get(s["structure_id"], [])
            s["mutations_str"] = "; ".join(m["mutation"] for m in muts) if muts else "-"
            s["resolution_str"] = "" if s.get("resolution") is None else str(s["resolution"])
            # href 는 State 에서 미리 문자열로 만든다 (컴포넌트 Var 레이어에서 "str"+item 불가)
            s["rcsb_url"] = "https://www.rcsb.org/structure/" + str(s["structure_id"])
        self.structures = structs


# AG Grid 컬럼 정의 — 기존 Streamlit 구조 표와 동일 구성 (정렬/필터)
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
    """AG Grid 구조 표 (자체 래퍼) — 정렬·필터·다중선택. 테마 클래스+높이는 래퍼 div 에."""
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


def index() -> rx.Component:
    return rx.container(
        rx.vstack(
            rx.heading("🧬 Protein Construct Builder — Reflex", size="7"),
            rx.text(
                "Supabase 위에서 동작하는 단백질/구조 조회 (Stage 3 핵심 뷰). "
                "Streamlit 앱과 같은 데이터·서비스 계층을 공유합니다.",
                color_scheme="gray",
            ),
            rx.select(
                State.protein_uids,
                placeholder="단백질 선택 (UniProt ID)",
                on_change=State.select_protein,
                width="320px",
            ),
            rx.cond(
                State.selected_uid != "",
                rx.vstack(
                    rx.heading(
                        State.gene_name + " (" + State.selected_uid + ")", size="5"
                    ),
                    rx.text("Organism: " + State.organism),
                    rx.text("Sequence length: " + State.seq_length.to_string() + " aa"),
                    rx.heading(
                        "PDB 구조 " + State.structure_count.to_string() + "개", size="4"
                    ),
                    _structures_grid(),
                    spacing="2",
                    width="100%",
                    align="start",
                ),
            ),
            spacing="4",
            align="start",
            width="100%",
        ),
        on_mount=State.load_proteins,
        size="4",
        padding="2rem",
    )


app = rx.App()
app.add_page(index, title="PCB Reflex")
