"""
Protein Construct Builder — cMET 중심 구조 탐색 앱 (Reflex 0.9.6)
===================================================================
단일 페이지: PDB database (/) — 단백질 검색 → KLIFS 구조 표 → 변이 트랙 → 약물 테이블
DB 접근은 database.py·collect.py 의 서비스 함수만 호출.
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
    get_proteins_overview,
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
    get_klifs_bulk,
    get_paper_analysis,
    get_paper_analysis_shared,
    upsert_paper_conditions,
    get_paper_pdf,
    get_paper_storage_path,
    save_paper_storage,
    get_paper_pdf_shared,
    get_all_mutations_by_uniprot,
    get_clinical_drugs_by_uniprot,
    get_structures_for_drug,
    get_drugs_for_structure,
    get_drug_detail,
    get_papers_unified,
    get_paper_by_structure,
)
from uniprot_fetcher import load_sequence_from_file
from collect import collect_protein
import playground_db as pgdb
import playground_llm as pgllm


def _warm_db():
    """백엔드 시작 시 DB 풀 연결을 미리 데워 첫 조회(수집목록)의 콜드스타트 지연을 없앤다."""
    try:
        from db_config import get_engine
        from sqlalchemy import text as _t
        with get_engine().connect() as _c:
            _c.execute(_t("SELECT 1"))
    except Exception:
        pass


import threading as _threading
_threading.Thread(target=_warm_db, daemon=True).start()

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


def _inhibitor_type(dfg, ac) -> str:
    """KLIFS DFG/αC 입체구조로부터 저해제 타입을 **추정**하는 휴리스틱.

    DFG/αC 값의 출처는 KLIFS(klifs.net) — 표준 키나아제 입체구조 분류 DB.
    이 함수가 내는 Type 은 리간드 실험분류가 아니라 **수용체 입체구조 기반 추정**이다.
    근거·검증은 DATA_PROVENANCE.md §2 참조.
    """
    d = (dfg or "").lower()
    a = (ac or "").lower()
    if d == "out":
        return "Type II"
    if d == "in" and a == "out":
        return "Type I½"
    if d == "in" and a == "in":
        return "Type I"
    return "-"


def _phase_label(max_phase) -> str:
    """ChEMBL max_phase → 임상 단계 라벨 (Synapse식 비임상/임상 구분)."""
    if max_phase is None:
        return "비임상"
    try:
        p = float(max_phase)
    except (TypeError, ValueError):
        return "비임상"
    if p >= 4:
        return "승인 (Phase 4)"
    if p >= 1:
        # 0.5 → Early Phase 1
        return f"Phase {int(p)}" if p == int(p) else f"Phase {p}"
    if p > 0:
        return "Early Phase 1"
    return "비임상"


def _status_color(status_var):
    """진행상황 → 색상 (Var 대응, rx.match)."""
    return rx.match(status_var,
                    ("승인 완료", "green"), ("진행중", "blue"), ("중단", "red"), "gray")


def _ct_color(status_var):
    """CT.gov overall_status → 색상 (Var 대응)."""
    return rx.match(status_var,
                    ("RECRUITING", "green"), ("ACTIVE_NOT_RECRUITING", "green"),
                    ("ENROLLING_BY_INVITATION", "green"), ("NOT_YET_RECRUITING", "green"),
                    ("COMPLETED", "blue"),
                    ("TERMINATED", "red"), ("WITHDRAWN", "red"), ("SUSPENDED", "amber"),
                    "gray")


def _materialize_pdf(sid: str, data: bytes | None = None) -> None:
    """PDF 바이트를 업로드 디렉토리에 {sid}.pdf 로 써서 /_upload 로 서빙되게 한다.
    (Reflex 내장 /_upload 경로는 로컬·Reflex Cloud 모두에서 백엔드로 라우팅됨)."""
    import pathlib
    updir = pathlib.Path(rx.get_upload_dir())
    fpath = updir / f"{sid}.pdf"
    if data is None:
        if fpath.exists():
            return
        data, _ = get_paper_pdf_shared(sid)   # DOI 공유: 형제 구조의 PDF 도 서빙
        if not data:
            return
    updir.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(data)


# ═══════════════════════════════════════════
# State
# ═══════════════════════════════════════════
class State(rx.State):
    query: str = ""
    collected_proteins: list[dict] = []   # 수집 완료된 단백질(검색창 최근 목록)
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
    # 업로드 진행 표시
    uploading: bool = False
    has_pdf: bool = False   # 현재 PDB 에 저장된 PDF 있는지
    _pending_storage_path: str = ""   # 진행 중 Storage 업로드 경로
    has_storage_pdf: bool = False     # 현재 PDF 가 Storage 저장분인지
    # papers 테이블에만 있는 전체 분석 논문(과거 유실분 등) — PDF 없이도 구조화 가능
    has_full_paper: bool = False
    full_paper_title: str = ""
    _full_paper_text: str = ""   # papers.analysis_md (구조화 추출 소스)

    # 활성 탭 (축 간 이동 시 제어 — 유기적 연결)
    active_tab: str = "pdb"

    # protein-level 개요 트랙 (변이 + 눈금자). 도메인·커버리지는 그리드 행 안으로 이동.
    mutation_track_items: list[dict] = []   # {left, color, mutation, position, label}
    ruler_ticks: list[dict] = []            # {left, label}

    # 약물 테이블 (임상단계 + 결합 PDB 중심 — IC50/활성 제외)
    drug_rows: list[dict] = []
    drugs_only: bool = True                  # True=약물(임상/결합), False=활성 화합물 전체
    chembl_busy: bool = False
    chembl_status: str = ""

    # 약물 상세 (Synapse식 연결 — 약물 클릭 시)
    selected_drug_id: str = ""
    drug_detail: dict = {}                   # {drug_name, max_phase, clinical_status, molecule_type}
    drug_trials: list[dict] = []             # ClinicalTrials.gov 임상시험
    drug_structures: list[dict] = []         # 결합 PDB 구조 (+inhibitor type)
    drug_summary_ai: str = ""                # LLM 임상 요약 (온디맨드)
    summary_busy: bool = False

    # 현재 PDB 에 결합한 약물(ChEMBL 매핑) — PDB 세부 패널용
    detail_drugs: list[dict] = []

    # 논문 (단백질 단위 통합 — papers + paper_analysis 병합 단일 목록)
    paper_list: list[dict] = []

    # ── 문헌 Playground (Claude 연구공간) ──
    playgrounds: list[dict] = []        # 현재 단백질의 전체 Playground(트리, parent_id 포함)
    active_pg_id: int = 0               # 열려 있는 Playground id (0=목록 화면)
    pg_name: str = ""                   # 열린 Playground 이름
    pg_parent_id: int = 0              # 열린 Playground 의 부모(0=최상위) — 브레드크럼용
    pg_messages: list[dict] = []        # raw 대화
    pg_digest: str = ""                 # 정리본
    pg_digest_updated: str = ""
    pg_papers: list[dict] = []          # 연결 문헌
    pg_input: str = ""                  # 채팅 입력
    pg_chatting: bool = False           # 답변 생성 중
    pg_summarizing: bool = False        # 정리본 생성 중
    pg_show_raw: bool = False           # raw 대화 펼침 여부
    pg_status: str = ""                 # 상태 메시지
    # 새 주제 / 이름수정 다이얼로그
    new_pg_name: str = ""
    new_pg_parent: int = 0             # 0=최상위, >0=하위 생성 대상 부모
    rename_pg_value: str = ""
    # 문헌 참조 추가(제목/DOI) 입력
    pg_paper_title: str = ""
    pg_paper_doi: str = ""
    # Playground 문헌 PDF 업로드(Storage 직접업로드)
    _pending_pg_paper_path: str = ""
    _pending_pg_paper_title: str = ""
    pg_uploading: bool = False

    @rx.var
    def drug_count(self) -> int:
        return len(self.drug_rows)

    @rx.var
    def drug_image_url(self) -> str:
        """선택 약물의 2D 구조 이미지 (ChEMBL depiction SVG)."""
        if not self.selected_drug_id:
            return ""
        return (f"https://www.ebi.ac.uk/chembl/api/data/image/"
                f"{self.selected_drug_id}?format=svg")

    @rx.var
    def paper_count(self) -> int:
        return len(self.paper_list)

    @rx.var
    def pg_count(self) -> int:
        return len(self.playgrounds)

    @rx.var
    def pg_tree(self) -> list[dict]:
        """Playground 를 트리 DFS 순서로 평탄화 + depth(들여쓰기용). 각 항목에 indent_px 추가."""
        by_parent: dict[int, list[dict]] = {}
        for pg in self.playgrounds:
            by_parent.setdefault(pg.get("parent_id", 0) or 0, []).append(pg)
        out: list[dict] = []

        def walk(parent_id: int, depth: int):
            for pg in by_parent.get(parent_id, []):
                d = dict(pg)
                d["depth"] = depth
                d["indent_px"] = f"{depth * 16}px"
                d["is_active"] = pg["id"] == self.active_pg_id
                out.append(d)
                walk(pg["id"], depth + 1)

        walk(0, 0)
        return out

    @rx.var
    def pg_has_digest(self) -> bool:
        return bool(self.pg_digest.strip())

    @rx.var
    def pg_msg_count(self) -> int:
        return len(self.pg_messages)

    @rx.var
    def mutation_count(self) -> int:
        return len(self.mutation_track_items)

    @rx.var
    def structure_count(self) -> int:
        return len(self.structures)

    @rx.var
    def selected_count(self) -> int:
        return len(self.selected_structure_ids)

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

        denom = seq_len or 1390
        # 도메인 세그먼트(배경 컨텍스트) — 모든 구조 행에 공통 첨부 (그리드 커버리지 셀 배경)
        dom_segs = [{"left": d["left"], "width": d["width"],
                     "color": d["color"], "label": d["label"]} for d in out]

        # 구조 표 (+ KLIFS: DFG/αC/추정 타입 + 행 내 UniProt 커버리지 막대)
        import re as _re
        structs = get_structures_by_uniprot(uid)
        sids = [s["structure_id"] for s in structs]
        mut_map = get_mutations_bulk(sids)
        klifs_map = get_klifs_bulk(sids)
        for s in structs:
            muts = mut_map.get(s["structure_id"], [])
            s["mutations_str"] = "; ".join(m["mutation"] for m in muts) if muts else "-"
            k = klifs_map.get(s["structure_id"]) or {}
            s["dfg"] = k.get("dfg") or "-"
            s["ac_helix"] = k.get("ac_helix") or "-"
            s["inhibitor_type"] = _inhibitor_type(k.get("dfg"), k.get("ac_helix"))
            # 행 내 커버리지 막대 좌표 (residue_range → %)
            s["dom_segs"] = dom_segs
            s["cov_left"] = ""
            s["cov_width"] = ""
            rng = (s.get("residue_range") or "").strip()
            if rng and rng not in ("-", "None"):
                nums = _re.findall(r"\d+", rng)
                if len(nums) >= 2:
                    start, end = int(nums[-2]), int(nums[-1])
                    if end >= start:
                        cstart = min(max(start, 1), denom)
                        cend = min(max(end, 1), denom)
                        cl = (cstart - 1) / denom * 100
                        cw = max(0.4, (cend - cstart + 1) / denom * 100)
                        if cl + cw > 100:
                            cw = 100 - cl
                        s["cov_left"] = f"{cl:.2f}%"
                        s["cov_width"] = f"{cw:.2f}%"
        self.structures = structs

        # 변이 트랙 — protein 전체 고유 변이 위치
        all_muts = get_all_mutations_by_uniprot(uid)
        seen_keys: set = set()
        track_items = []
        for m in all_muts:
            pos = m.get("position")
            if not pos:
                continue
            key = (pos, m.get("mutation", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            cpos = min(max(pos, 1), denom)   # 범위 밖 좌표 클램프
            left = (cpos - 1) / denom * 100
            track_items.append({
                "left": f"{left:.2f}%",
                "color": "#6b7cff",   # 단일 색상 (category 데이터 미구현)
                "mutation": m.get("mutation", ""),
                "position": str(pos),
                "label": f"{m.get('mutation', '')} @ {pos}",
            })
        self.mutation_track_items = track_items

        # 눈금: 적응형 간격 (단백질 길이에 맞춰 ~7개 구획)
        ticks = []
        step = max(50, round(denom / 7 / 50) * 50)
        pos = 1
        while pos <= denom:
            ticks.append({"left": f"{(pos - 1) / denom * 100:.2f}%", "label": str(pos)})
            pos += step
        self.ruler_ticks = ticks

        # 약물 테이블 (임상단계 + 결합 PDB 중심 — 데이터 없으면 빈 리스트)
        try:
            self.drug_rows = get_clinical_drugs_by_uniprot(uid, self.drugs_only)
        except Exception:
            self.drug_rows = []
        # 약물 상세 초기화
        self.selected_drug_id = ""
        self.drug_detail = {}
        self.drug_trials = []
        self.drug_structures = []
        self.drug_summary_ai = ""

        # 논문 (papers + paper_analysis 병합 단일 목록)
        try:
            self.paper_list = get_papers_unified(uid)
        except Exception:
            self.paper_list = []

        # 문헌 Playground 목록 + 열린 Playground 초기화
        try:
            self.playgrounds = pgdb.list_playgrounds(uid)
        except Exception:
            self.playgrounds = []
        self._reset_playground_view()

        # 세부 패널 초기화
        self.detail_sid = ""
        self.detail = {}

    def _load_detail(self, sid: str):
        """클릭한 PDB 의 세부 + 기존 구조화 분석을 적재."""
        self.detail_sid = sid
        self.detail = get_structure(sid) or {}
        self.detail_mutations = get_mutations_by_structure(sid)
        ligs = get_ligands_by_structure(sid)
        for l in ligs:  # 리간드 2D 구조 이미지 URL (RCSB CCD depiction)
            lid = (l.get("ligand_id") or "").strip()
            l["ligand_id"] = lid
            l["ligand_name"] = l.get("ligand_name") or ""
            l["img_url"] = (f"https://cdn.rcsb.org/images/ccd/labeled/{lid[0].upper()}/{lid}.svg"
                            if lid else "")
            l["rcsb_url"] = (f"https://www.rcsb.org/ligand/{lid}" if lid else "")
        self.detail_ligands = ligs
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
        # 기존 구조화 분석 (DOI 공유 — 같은 논문 형제 구조의 분석도 반영)
        pa = get_paper_analysis_shared(sid)
        if pa and pa.get("structured"):
            self.pdb_conditions = pa["structured"]
            self.has_conditions = True
        else:
            self.pdb_conditions = {}
            self.has_conditions = False
        # papers 테이블에만 있는 전체 분석 논문(과거 유실분): PDF 없이도 구조화 가능하게
        fp = get_paper_by_structure(sid)
        if fp and (fp.get("analysis_md") or "").strip():
            self.has_full_paper = True
            self.full_paper_title = fp.get("title") or "(제목 없음)"
            self._full_paper_text = fp.get("analysis_md") or ""
        else:
            self.has_full_paper = False
            self.full_paper_title = ""
            self._full_paper_text = ""
        # 업로드 상태 초기화 + 이 PDB(또는 같은 DOI 형제)에 저장된 PDF 반영
        stored_name = (pa or {}).get("pdf_name") or ""
        self.uploaded_name = stored_name
        self.has_pdf = bool(stored_name)
        # ★렉 방지: Storage 서명 URL 생성(네트워크)은 여기서 안 함 — '열기' 클릭 시 on-demand.
        #  Storage 여부만 표시해 열기 방식을 결정.
        self.has_storage_pdf = bool((pa or {}).get("pdf_storage_path"))
        self.uploaded_pdf_path = ""
        self.uploading = False
        self.cond_status = ""
        # 이 PDB 에 결합한 약물(ChEMBL 매핑) — 구조↔약물 연결
        try:
            drugs = get_drugs_for_structure(sid)
            for d in drugs:
                d["pref_name"] = d.get("pref_name") or d.get("chembl_id") or ""
                d["phase_label"] = _phase_label(d.get("max_phase"))
                d["median_pchembl"] = d.get("median_pchembl")
                d["chembl_url"] = ("https://www.ebi.ac.uk/chembl/compound_report_card/"
                                   + (d.get("chembl_id") or ""))
            self.detail_drugs = drugs
        except Exception:
            self.detail_drugs = []

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

    # Playground 입력 setter (Reflex 명시적 setter 필요)
    @rx.event
    def set_new_pg_name(self, value: str):
        self.new_pg_name = value

    @rx.event
    def set_new_pg_parent(self, value: int):
        self.new_pg_parent = int(value or 0)

    @rx.event
    def set_pg_input(self, value: str):
        self.pg_input = value

    @rx.event
    def set_rename_pg_value(self, value: str):
        self.rename_pg_value = value

    @rx.event
    def set_pg_paper_title(self, value: str):
        self.pg_paper_title = value

    @rx.event
    def set_pg_paper_doi(self, value: str):
        self.pg_paper_doi = value

    @rx.event
    def load_collected(self):
        """수집 완료된 단백질 목록 로드 (검색창 최근 목록)."""
        try:
            self.collected_proteins = get_proteins_overview()
        except Exception:
            self.collected_proteins = []

    @rx.event(background=True)
    async def open_protein(self, uid: str):
        """수집된 단백질 카드 클릭 → 바로 로드 (재수집 없이)."""
        async with self:
            self.collecting = True
            self.not_found = False
            self.collect_status = "로딩 중..."
            self.selected_uid = ""
            self.query = ""
        need_chembl = False
        async with self:
            self._apply_protein(uid)
            self.collecting = False
            self.collect_status = ""
            need_chembl = not self.drug_rows
        if need_chembl:
            return State.fetch_chembl

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
        need_chembl = False
        async with self:
            self.collecting = False
            self.collect_status = ""
            if uid:
                self._apply_protein(uid)
                need_chembl = not self.drug_rows   # 활성 데이터 없으면 자동 수집
            else:
                self.not_found = True
        if need_chembl:
            return State.fetch_chembl

    def _do_chembl(self, uid: str) -> dict:
        """(스레드) ChEMBL 활성 수집 + UniChem 리간드→약물 매핑. 구조↔약물 연결까지 채운다."""
        try:
            from chembl_fetcher import run as chembl_run
            res = chembl_run(uid)
            if res.get("error"):
                return res
            try:
                from unichem_fetcher import run as unichem_run
                res["unichem"] = unichem_run(uid)
            except Exception as ue:
                res["unichem_error"] = str(ue)[:120]
            try:
                from ct_fetcher import run as ct_run
                res["ct"] = ct_run(uid)   # 임상시험 + 진행상황
            except Exception as ce:
                res["ct_error"] = str(ce)[:120]
            return res
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:160]}"}

    @rx.event(background=True)
    async def fetch_chembl(self):
        """검색한 단백질의 ChEMBL 활성 + 리간드→약물 매핑을 백그라운드 수집 → 약물 테이블 갱신."""
        async with self:
            uid = self.selected_uid
            if not uid or self.chembl_busy:
                return
            self.chembl_busy = True
            self.chembl_status = "ChEMBL 활성 + 리간드-약물 매핑 수집 중... (수 분 소요)"
        res = await asyncio.to_thread(self._do_chembl, uid)
        async with self:
            self.chembl_busy = False
            if res.get("error"):
                self.chembl_status = "수집 실패: " + res["error"]
            else:
                try:
                    self.drug_rows = get_clinical_drugs_by_uniprot(uid, self.drugs_only)
                except Exception:
                    self.drug_rows = []
                n_c = res.get("compounds", 0)
                n_b = res.get("bioactivities", 0)
                uni = res.get("unichem") or {}
                n_ind = res.get("indications", 0)
                if n_c == 0 and n_b == 0:
                    self.chembl_status = "이 단백질의 ChEMBL 데이터가 없습니다 (비키나아제·미등록 가능)."
                else:
                    self.chembl_status = (f"수집 완료 — 화합물 {n_c} · 임상약물 {len(self.drug_rows)}"
                                          + (f" · PDB결합 {uni.get('mapped', 0)}" if uni else "")
                                          + (f" · 질환 {n_ind}" if n_ind else ""))

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

    @rx.event
    def on_drug_clicked(self, row: dict):
        """약물 행 클릭 → 상세(임상단계·진행상황·임상시험·결합 PDB+type) 로드."""
        self._load_drug(row.get("chembl_id"))

    def _load_drug(self, cid: str | None):
        if not cid:
            return
        self.selected_drug_id = cid
        self.drug_summary_ai = ""
        try:
            det = get_drug_detail(cid, self.selected_uid)
            comp = det.get("compound", {}) or {}
            comp["drug_name"] = comp.get("pref_name") or cid
            comp["phase_label"] = _phase_label(comp.get("max_phase"))
            comp["clinical_status"] = comp.get("clinical_status") or "-"
            comp["molecule_type"] = comp.get("molecule_type") or "—"
            self.drug_detail = comp
            trials = det.get("trials", []) or []
            for t in trials:
                t["title"] = (t.get("title") or "")[:110]
                t["phase"] = t.get("phase") or "—"
                t["overall_status"] = t.get("overall_status") or "—"
                t["conditions"] = t.get("conditions") or ""
                t["why_stopped"] = t.get("why_stopped") or ""
                t["nct_url"] = ("https://clinicaltrials.gov/study/" + (t.get("nct_id") or ""))
            self.drug_trials = trials
        except Exception:
            self.drug_detail = {}
            self.drug_trials = []
        try:
            structs = get_structures_for_drug(cid, self.selected_uid)
            for s in structs:
                s["inhibitor_type"] = _inhibitor_type(s.get("dfg"), s.get("ac_helix"))
            self.drug_structures = structs
        except Exception:
            self.drug_structures = []

    def _do_trial_summary(self, cid: str) -> str:
        from ct_fetcher import summarize_trials_llm
        from database import get_drug_trials
        name = self.drug_detail.get("drug_name") or cid
        return summarize_trials_llm(name, get_drug_trials(cid))

    @rx.event(background=True)
    async def summarize_trials(self):
        """(온디맨드) LLM 으로 임상 진행 서사 요약."""
        async with self:
            cid = self.selected_drug_id
            if not cid or self.summary_busy:
                return
            self.summary_busy = True
            self.drug_summary_ai = ""
        text_out = await asyncio.to_thread(self._do_trial_summary, cid)
        async with self:
            self.summary_busy = False
            self.drug_summary_ai = text_out

    @rx.event
    def set_active_tab(self, tab: str):
        self.active_tab = tab

    @rx.event
    def open_drug_from_structure(self, chembl_id: str):
        """PDB 세부의 결합 약물 클릭 → 약물 탭으로 전환 + 약물 상세 로드 (역방향 연결)."""
        if chembl_id:
            self._load_drug(chembl_id)
            self.active_tab = "bio"

    @rx.event
    def open_structure_from_drug(self, sid: str):
        """약물 상세의 결합 PDB 클릭 → PDB 구조 탭으로 전환 + 구조 세부 로드 (축 간 이동)."""
        if sid:
            self._load_detail(sid)
            self.active_tab = "pdb"

    @rx.event
    def toggle_drugs_only(self, val: bool):
        """약물만 / 활성 화합물 전체 토글."""
        self.drugs_only = val
        if self.selected_uid:
            try:
                self.drug_rows = get_clinical_drugs_by_uniprot(self.selected_uid, val)
            except Exception:
                self.drug_rows = []

    # ── PDB별 논문 구조화 분석 (실험 세부조건) ──
    def _do_conditions(self, pdf: str, sid: str) -> dict:
        from paper_pipeline import extract_construct_conditions
        # 임시 경로 없으면 DB 에 저장된 PDF 바이트로 임시파일 생성 (DOI 공유 포함)
        if not pdf:
            data, _ = get_paper_pdf_shared(sid)
            if not data:
                return {"error": "저장된 PDF가 없습니다."}
            import tempfile
            fd, pdf = tempfile.mkstemp(suffix=".pdf")
            with os.fdopen(fd, "wb") as o:
                o.write(data)
        r = extract_construct_conditions(pdf)
        if r.get("error"):
            return r
        upsert_paper_conditions(sid, r["conditions"])
        return {"conditions": r["conditions"]}

    @rx.event(background=True)
    async def run_pdb_conditions(self):
        async with self:
            if not self.detail_sid or (not self.uploaded_pdf_path and not self.has_pdf):
                self.cond_status = "논문 PDF 를 먼저 업로드하세요."
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

    def _do_conditions_from_text(self, text: str, sid: str) -> dict:
        from paper_pipeline import extract_conditions_from_text
        r = extract_conditions_from_text(text)
        if r.get("error"):
            return r
        upsert_paper_conditions(sid, r["conditions"])
        return {"conditions": r["conditions"]}

    @rx.event(background=True)
    async def run_conditions_from_paper(self):
        """PDF 없이 papers 의 기존 전체 분석본에서 구조화 추출 (11HQ 등 유실분 복구)."""
        async with self:
            if not self.detail_sid or not self._full_paper_text.strip():
                self.cond_status = "기존 분석본이 없습니다."
                return
            self.cond_analyzing = True
            self.cond_status = "기존 분석본에서 구조화 추출 중... (수십 초)"
            txt = self._full_paper_text
            sid = self.detail_sid
        res = await asyncio.to_thread(self._do_conditions_from_text, txt, sid)
        async with self:
            self.cond_analyzing = False
            if res.get("error"):
                self.cond_status = "오류: " + res["error"]
            else:
                self.cond_status = "구조화 완료 (기존 분석본 기반) — Supabase 저장됨"
                self.pdb_conditions = res["conditions"]
                self.has_conditions = True

    # ── Phase 2: 논문 업로드 (Supabase Storage 직접 업로드 — Cloud 크기한계 우회) ──
    @rx.event
    def start_direct_upload(self, value):
        """파일 선택 시: 서명 업로드 URL 생성 후, 브라우저가 Storage 로 직접 PUT (call_script)."""
        # value 는 파일 input 의 값(가짜 경로) — 파일명 추출
        raw = value if isinstance(value, str) else ""
        fname = raw.replace("\\", "/").split("/")[-1] or "uploaded.pdf"
        sid = self.detail_sid
        if not sid:
            self.cond_status = "먼저 PDB 행을 선택한 뒤 PDF 를 올려주세요."
            return
        if fname and not fname.lower().endswith(".pdf"):
            self.cond_status = "PDF 파일만 업로드할 수 있습니다."
            return
        try:
            from supabase_storage import create_signed_upload, storage_path_for
            path = storage_path_for(sid, fname)
            sig = create_signed_upload(path)
            signed_url = sig.get("signed_url")
            if not signed_url:
                raise RuntimeError("서명 URL 생성 실패")
        except Exception as ex:
            self.cond_status = ("업로드 준비 실패: " + str(ex)[:150] +
                                " (secrets 에 SUPABASE_SERVICE_KEY 필요)")
            return
        self.uploading = True
        self.uploaded_name = fname
        self._pending_storage_path = path
        self.analyze_result_md = ""
        self.analyze_status = ""
        self.cond_status = "Storage 로 업로드 중... (대용량도 가능)"
        # 브라우저 → Supabase Storage 직접 PUT (Reflex 백엔드 안 거침 → 크기 무제한)
        js = (
            "(async () => {"
            "  const inp = document.getElementById('pdf_direct_input');"
            "  if (!inp || !inp.files || !inp.files[0]) return {error:'파일 없음'};"
            "  try {"
            "    const r = await fetch(" + repr(signed_url) + ", {"
            "      method:'PUT', headers:{'x-upsert':'true'}, body: inp.files[0]"
            "    });"
            "    if (r.ok) return {ok:true};"
            "    const t = await r.text();"
            "    return {error:'HTTP '+r.status+' '+(t||'').slice(0,120)};"
            "  } catch(e){ return {error:String(e)}; }"
            "})()"
        )
        return rx.call_script(js, callback=State.on_direct_upload_result)

    @rx.event
    def on_direct_upload_result(self, result):
        """Storage 업로드 콜백 → 실제 저장 검증 후 DB 에 경로 저장."""
        self.uploading = False
        self.has_pdf = False
        # JS PUT 이 에러를 보고했으면 실패
        if isinstance(result, dict) and result.get("error"):
            self.cond_status = "업로드 실패: " + str(result.get("error"))
            return
        # ★실제로 Storage 에 파일이 올라갔는지 서버에서 검증 (이름만 남는 문제 방지)
        try:
            from supabase_storage import object_exists
            if not object_exists(self._pending_storage_path):
                self.uploaded_name = ""
                self.cond_status = "업로드 실패 — Storage 에 파일이 확인되지 않습니다. 다시 시도하세요."
                return
            from database import save_paper_storage
            save_paper_storage(self.detail_sid, self._pending_storage_path, self.uploaded_name)
        except Exception as ex:
            self.cond_status = "저장/검증 실패: " + str(ex)[:150]
            return
        self.has_pdf = True
        self.has_storage_pdf = True
        self.pdb_conditions = {}
        self.has_conditions = False
        self.uploaded_pdf_path = ""
        self.cond_status = "새 PDF 업로드 완료 (Storage) — '구조화 분석'을 눌러 새로 분석하세요."

    @rx.event
    def open_pdf(self):
        """논문 PDF 새 창 열기 — 클릭 시에만 URL 생성(행 클릭마다 네트워크 렉 방지)."""
        sid = self.detail_sid
        if not sid:
            return
        try:
            sp = get_paper_storage_path(sid)
        except Exception:
            sp = None
        if sp:
            try:
                from supabase_storage import create_signed_download
                url = create_signed_download(sp)
                if url:
                    return rx.redirect(url, is_external=True)
            except Exception:
                pass
            self.cond_status = "PDF 링크 생성 실패"
            return
        # 레거시 pdf_bytes → /_upload 서빙파일 적재 후 열기
        try:
            _materialize_pdf(sid)
        except Exception:
            pass
        return rx.redirect(rx.get_upload_url(sid + ".pdf"), is_external=True)

    @rx.event
    def delete_paper_event(self, sids):
        """업로드 논문 삭제(PDF·구조화·Storage·papers) → 목록 갱신. [요청: 기존 논문 삭제]"""
        from database import delete_paper
        try:
            for sid in (sids or []):
                delete_paper(sid)
        except Exception:
            pass
        # 목록 갱신 + 현재 세부 패널 반영
        try:
            self.paper_list = get_papers_unified(self.selected_uid)
        except Exception:
            pass
        if self.detail_sid and self.detail_sid in (sids or []):
            self.has_pdf = False
            self.has_storage_pdf = False
            self.uploaded_name = ""
            self.pdb_conditions = {}
            self.has_conditions = False

    # ══════════════════════════════════════════════
    # 문헌 Playground (Claude 연구공간)
    # ══════════════════════════════════════════════
    def _reset_playground_view(self):
        """열린 Playground 상태 초기화(목록 화면으로)."""
        self.active_pg_id = 0
        self.pg_name = ""
        self.pg_parent_id = 0
        self.pg_messages = []
        self.pg_digest = ""
        self.pg_digest_updated = ""
        self.pg_papers = []
        self.pg_input = ""
        self.pg_show_raw = False
        self.pg_status = ""

    def _reload_playgrounds(self):
        try:
            self.playgrounds = pgdb.list_playgrounds(self.selected_uid)
        except Exception:
            self.playgrounds = []

    @rx.event
    def create_playground_event(self):
        """새 주제(또는 하위 주제) 생성. new_pg_name/new_pg_parent 사용."""
        name = (self.new_pg_name or "").strip()
        if not name or not self.selected_uid:
            return
        parent = self.new_pg_parent or None
        try:
            pid = pgdb.create_playground(self.selected_uid, name, parent_id=parent)
        except Exception as e:
            self.pg_status = f"생성 실패: {e}"
            return
        self.new_pg_name = ""
        self.new_pg_parent = 0
        self._reload_playgrounds()
        if pid:
            self._open_playground(pid)

    def _open_playground(self, pid: int):
        g = None
        try:
            g = pgdb.get_playground(pid)
        except Exception:
            g = None
        if not g:
            return
        self.active_pg_id = pid
        self.pg_name = g["name"]
        self.pg_parent_id = g.get("parent_id", 0) or 0
        self.pg_digest = g.get("digest", "") or ""
        self.pg_digest_updated = g.get("digest_updated_at", "") or ""
        try:
            self.pg_messages = pgdb.get_messages(pid)
        except Exception:
            self.pg_messages = []
        try:
            self.pg_papers = pgdb.list_playground_papers(pid)
        except Exception:
            self.pg_papers = []
        # 정리본 있으면 raw 접힘, 없으면 펼침
        self.pg_show_raw = not bool(self.pg_digest.strip())
        self.pg_input = ""
        self.pg_status = ""

    @rx.event
    def open_playground(self, pid: int):
        self._open_playground(pid)

    @rx.event
    def close_playground(self):
        self._reset_playground_view()

    @rx.event
    def toggle_pg_raw(self):
        self.pg_show_raw = not self.pg_show_raw

    @rx.event
    def rename_playground_event(self):
        name = (self.rename_pg_value or "").strip()
        if not name or not self.active_pg_id:
            return
        try:
            pgdb.rename_playground(self.active_pg_id, name)
        except Exception:
            return
        self.pg_name = name
        self.rename_pg_value = ""
        self._reload_playgrounds()

    @rx.event
    def delete_playground_event(self, pid: int):
        try:
            pgdb.delete_playground(pid)
        except Exception:
            pass
        if pid == self.active_pg_id:
            self._reset_playground_view()
        self._reload_playgrounds()

    @rx.event
    def delete_pg_message_event(self, mid: int):
        try:
            pgdb.delete_message(mid)
        except Exception:
            pass
        if self.active_pg_id:
            try:
                self.pg_messages = pgdb.get_messages(self.active_pg_id)
            except Exception:
                pass

    def _do_pg_chat(self, pid: int) -> str:
        """(스레드) 연결 문헌 컨텍스트 조립 + Claude 답변 생성."""
        try:
            history = pgdb.get_messages(pid)
            ctx = self._pg_context(pid)
            return pgllm.run_chat(history, extra_context=ctx,
                                  uniprot_id=self.selected_uid)
        except Exception as e:
            return f"(오류: 답변 생성 실패 — {e})"

    def _pg_context(self, pid: int) -> str:
        """연결 문헌 + 매칭된 PDB/약물 팩트를 텍스트 컨텍스트로 조립 (P5)."""
        lines: list[str] = []
        try:
            papers = pgdb.list_playground_papers(pid)
        except Exception:
            papers = []
        for p in papers:
            tag = f"- 문헌: {p['title']}"
            if p["doi"]:
                tag += f" (DOI: {p['doi']})"
            if p.get("matched_structure_id"):
                tag += f" → DB 매칭 PDB: {p['matched_structure_id']}"
                fa = get_paper_by_structure(p["matched_structure_id"])
                if fa and fa.get("analysis_md"):
                    tag += "\n  [분석요약] " + str(fa["analysis_md"])[:1500]
            lines.append(tag)
        return "\n".join(lines)

    @rx.event(background=True)
    async def send_pg_message(self):
        """사용자 메시지 → raw 누적 → Claude 답변 → raw 누적 → 화면 갱신."""
        async with self:
            pid = self.active_pg_id
            text_in = (self.pg_input or "").strip()
            if not pid or not text_in or self.pg_chatting:
                return
            self.pg_chatting = True
            self.pg_input = ""
            self.pg_status = "답변 생성 중…"
            try:
                pgdb.add_message(pid, "user", text_in)
                self.pg_messages = pgdb.get_messages(pid)
                self.pg_show_raw = True
            except Exception:
                pass
        answer = await asyncio.to_thread(self._do_pg_chat, pid)
        async with self:
            try:
                pgdb.add_message(pid, "assistant", answer)
                self.pg_messages = pgdb.get_messages(pid)
            except Exception:
                pass
            self.pg_chatting = False
            self.pg_status = ""
            self._reload_playgrounds()

    @rx.event(background=True)
    async def summarize_playground(self):
        """수동 '정리' 버튼 → 대화 전체를 체계적 정리본(digest)으로 재생성(Sonnet)."""
        async with self:
            pid = self.active_pg_id
            if not pid or self.pg_summarizing:
                return
            if not self.pg_messages:
                self.pg_status = "정리할 대화가 없습니다."
                return
            self.pg_summarizing = True
            self.pg_status = "정리본 생성 중…"
        digest = ""
        try:
            history = await asyncio.to_thread(pgdb.get_messages, pid)
            digest = await asyncio.to_thread(pgllm.generate_digest, history)
        except Exception as e:
            digest = ""
            err = str(e)
        async with self:
            if digest.strip():
                try:
                    pgdb.update_digest(pid, digest)
                except Exception:
                    pass
                self.pg_digest = digest
                g = pgdb.get_playground(pid)
                self.pg_digest_updated = g.get("digest_updated_at", "") if g else ""
                self.pg_show_raw = False
                self.pg_status = "정리 완료"
                self._reload_playgrounds()
            else:
                self.pg_status = "정리본 생성 실패"
            self.pg_summarizing = False

    @rx.event
    def add_pg_paper_ref(self):
        """제목/DOI 로 문헌 참조 추가 + DB 매칭 시도."""
        pid = self.active_pg_id
        title = (self.pg_paper_title or "").strip()
        doi = (self.pg_paper_doi or "").strip()
        if not pid or not (title or doi):
            return
        try:
            paper_id = pgdb.add_playground_paper(pid, title or doi, doi=doi)
            m = pgdb.match_paper_to_db(self.selected_uid, title=title, doi=doi)
            if m and paper_id:
                pgdb.set_paper_match(paper_id, m["structure_id"])
            self.pg_papers = pgdb.list_playground_papers(pid)
            self.pg_status = ("DB 논문과 매칭됨: " + m["structure_id"]) if m else "문헌 추가됨(매칭 없음)"
        except Exception as e:
            self.pg_status = f"문헌 추가 실패: {e}"
        self.pg_paper_title = ""
        self.pg_paper_doi = ""

    @rx.event
    def start_pg_paper_upload(self, value):
        """Playground 문헌 PDF 를 Storage 로 직접 업로드(브라우저 PUT)."""
        pid = self.active_pg_id
        if not pid:
            self.pg_status = "먼저 주제를 선택하세요."
            return
        raw = value if isinstance(value, str) else ""
        fname = raw.replace("\\", "/").split("/")[-1] or "uploaded.pdf"
        if fname and not fname.lower().endswith(".pdf"):
            self.pg_status = "PDF 파일만 업로드할 수 있습니다."
            return
        try:
            from supabase_storage import create_signed_upload, storage_path_for
            path = storage_path_for(f"playground/{pid}", fname)
            sig = create_signed_upload(path)
            signed_url = sig.get("signed_url")
            if not signed_url:
                raise RuntimeError("서명 URL 생성 실패")
        except Exception as ex:
            self.pg_status = "업로드 준비 실패: " + str(ex)[:120]
            return
        self.pg_uploading = True
        self._pending_pg_paper_path = path
        self._pending_pg_paper_title = fname
        self.pg_status = "문헌 업로드 중…"
        js = (
            "(async () => {"
            "  const inp = document.getElementById('pg_paper_input');"
            "  if (!inp || !inp.files || !inp.files[0]) return {error:'파일 없음'};"
            "  try {"
            "    const r = await fetch(" + repr(signed_url) + ", {"
            "      method:'PUT', headers:{'x-upsert':'true'}, body: inp.files[0]"
            "    });"
            "    if (r.ok) return {ok:true};"
            "    const t = await r.text();"
            "    return {error:'HTTP '+r.status+' '+(t||'').slice(0,120)};"
            "  } catch(e){ return {error:String(e)}; }"
            "})()"
        )
        return rx.call_script(js, callback=State.on_pg_paper_upload_result)

    @rx.event
    def on_pg_paper_upload_result(self, result):
        """업로드 검증 → playground_papers 저장 + 파일명↔DB 논문 매칭."""
        self.pg_uploading = False
        pid = self.active_pg_id
        if isinstance(result, dict) and result.get("error"):
            self.pg_status = "업로드 실패: " + str(result.get("error"))
            return
        try:
            from supabase_storage import object_exists
            if not self._pending_pg_paper_path or not object_exists(self._pending_pg_paper_path):
                self.pg_status = "업로드 실패 — Storage 에서 파일이 확인되지 않습니다."
                return
            title = self._pending_pg_paper_title or "업로드 문헌.pdf"
            paper_id = pgdb.add_playground_paper(pid, title, storage_path=self._pending_pg_paper_path)
            # 파일명으로 DB 논문 매칭 시도(요구4: 업로드 문헌명 ↔ DB 논문명 대조)
            m = pgdb.match_paper_to_db(self.selected_uid, title=title)
            if m and paper_id:
                pgdb.set_paper_match(paper_id, m["structure_id"])
            self.pg_papers = pgdb.list_playground_papers(pid)
            self.pg_status = ("업로드 완료 — DB 논문 매칭: " + m["structure_id"]) if m \
                else "업로드 완료 (DB 매칭 없음 — DOI 로 참조 추가하면 매칭 정확도↑)"
        except Exception as ex:
            self.pg_status = "저장 실패: " + str(ex)[:120]
        finally:
            self._pending_pg_paper_path = ""
            self._pending_pg_paper_title = ""

    @rx.event
    def delete_pg_paper_event(self, paper_id: int):
        try:
            pgdb.delete_playground_paper(paper_id)
        except Exception:
            pass
        if self.active_pg_id:
            try:
                self.pg_papers = pgdb.list_playground_papers(self.active_pg_id)
            except Exception:
                pass

    @rx.event
    def open_matched_structure(self, sid: str):
        """연결 문헌의 매칭 PDB 클릭 → PDB 구조 탭으로 이동 (유기적 연결)."""
        if sid:
            self._load_detail(sid)
            self.active_tab = "pdb"

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



# ═══════════════════════════════════════════
# AG Grid 컬럼
# ═══════════════════════════════════════════
# 커버리지 컬럼: PDBe-KB 스타일 — 도메인(배경) + 구조 잔기범위 막대를 행 안에 직접 그림.
# cellRenderer 는 raw JS 함수(pdbCovRenderer, ag_grid_wrap 에 주입)를 참조해야 하므로
# column_defs 전체를 JS 배열 리터럴로 주입한다(아래 _COLUMN_DEFS_JS).
_COLUMN_DEFS = [
    {"field": "structure_id", "headerName": "PDB ID", "pinned": "left", "filter": True,
     "minWidth": 110, "maxWidth": 120},
    {"headerName": "UniProt Coverage", "colId": "coverage", "cellRenderer": "__PDB_COV__",
     "sortable": False, "filter": False, "minWidth": 240, "flex": 1,
     "headerTooltip": "도메인(반투명 배경) + 이 구조가 커버하는 UniProt 잔기범위(막대). 막대 hover 시 범위 표시."},
    {"field": "residue_range", "headerName": "Range", "filter": True, "maxWidth": 130},
    {"field": "method", "headerName": "Method", "filter": True, "maxWidth": 110},
    {"field": "resolution", "headerName": "Res (Å)", "filter": "agNumberColumnFilter", "maxWidth": 100},
    {"field": "complex_type", "headerName": "Complex", "filter": True},
    {"field": "inhibitor_type", "headerName": "Inhibitor type", "filter": True, "maxWidth": 130,
     "headerTooltip": "KLIFS DFG/αC 입체구조 기반 추정 (실험 분류 아님). 출처·근거: DATA_PROVENANCE.md §2"},
    {"field": "dfg", "headerName": "DFG", "filter": True, "maxWidth": 85,
     "headerTooltip": "KLIFS (klifs.net) DFG motif in/out"},
    {"field": "ac_helix", "headerName": "αC-helix", "filter": True, "maxWidth": 95,
     "headerTooltip": "KLIFS (klifs.net) αC-helix in/out"},
    {"field": "chain_id", "headerName": "Chain", "filter": True, "maxWidth": 90},
    {"field": "mutations_str", "headerName": "Mutations", "filter": True},
    {"field": "expression_system", "headerName": "Organism", "filter": True},
    {"field": "host_cell_line", "headerName": "Expr System", "filter": True},
    {"field": "space_group", "headerName": "Space Group", "filter": True},
    {"field": "deposition_date", "headerName": "Deposit Date", "filter": True},
    {"field": "doi", "headerName": "DOI", "filter": True},
]

# JS 배열 리터럴로 직렬화 후 placeholder 를 함수 참조로 치환 → raw JS Var 주입
import json as _json
_COLUMN_DEFS_JS = rx.Var(
    _json.dumps(_COLUMN_DEFS, ensure_ascii=False).replace('"__PDB_COV__"', "pdbCovRenderer")
)


def _structures_grid() -> rx.Component:
    return rx.box(
        ag_grid(
            column_defs=_COLUMN_DEFS_JS,
            row_data=State.structures,
            default_col_def={"sortable": True, "resizable": True, "floatingFilter": True},
            row_selection="single",
            pagination=True,
            pagination_page_size=20,
            on_row_clicked=State.on_row_clicked,
        ),
        width="100%",
        height="520px",
        custom_attrs={"data-ag-theme-mode": "dark"},  # AG Grid v33 다크 테마
    )


# ═══════════════════════════════════════════
# Feature Viewer — 공유 좌표축(1D ruler) 위에 도메인·변이·PDB 트랙 정렬
# (참조 이미지의 BACE1 Feature Viewer 레이아웃)
# ═══════════════════════════════════════════
# 트랙 세그먼트 빌더 -------------------------------------------------
def _mut_pin(item: dict) -> rx.Component:
    return rx.tooltip(
        rx.box(
            position="absolute", left=item["left"], top="0",
            width="2px", height="26px", background=item["color"], border_radius="1px",
        ),
        content=item["label"],
    )


def _ruler_tick(t: dict) -> rx.Component:
    return rx.box(
        rx.box(position="absolute", left=t["left"], top="0",
               width="1px", height="6px", background=rx.color("gray", 7)),
        rx.text(t["label"], position="absolute", left=t["left"], top="7px",
                font_size="0.6rem", color=rx.color("gray", 9), transform="translateX(-50%)"),
        position="absolute", left=t["left"], top="0",
    )


def _track_bar(children, height: str, bg=None) -> rx.Component:
    """relative 컨테이너 — 자식은 absolute % 위치."""
    return rx.box(
        rx.cond(
            bg is not None,
            rx.box(position="absolute", left="0", top="0", width="100%",
                   height=height, background=bg, border_radius="3px"),
        ),
        children,
        position="relative", width="100%", height=height,
    )


def _domain_legend() -> rx.Component:
    """도메인 색상 범례 — 그리드 커버리지 셀의 배경 색상을 해독."""
    def _row(d: dict) -> rx.Component:
        return rx.hstack(
            rx.box(width="10px", height="10px", background=d["color"],
                   opacity="0.55", border_radius="2px"),
            rx.text(d["name"], size="1", weight="medium"),
            rx.text(d["range"], size="1", color_scheme="gray"),
            spacing="1", align="center",
        )
    return rx.cond(
        State.domains.length() > 0,
        rx.hstack(
            rx.text("Domains:", size="1", weight="bold", color=rx.color("gray", 10)),
            rx.foreach(State.domains, _row),
            wrap="wrap", spacing="3", width="100%", align="center",
        ),
    )


def _protein_overview() -> rx.Component:
    """그리드 위 단백질 단위 개요 — 눈금자 + 변이 트랙 + 도메인 범례.

    도메인·구조 커버리지는 이제 그리드 각 행 안(UniProt Coverage 컬럼)에 표시되므로,
    여기서는 행 단위로 못 보여주는 protein-level 정보(변이 집계)만 둔다.
    """
    return rx.box(
        rx.vstack(
            _domain_legend(),
            rx.cond(
                State.mutation_count > 0,
                rx.vstack(
                    rx.box(
                        rx.foreach(State.ruler_ticks, _ruler_tick),
                        position="relative", width="100%", height="20px",
                        min_width="0", overflow="visible",
                    ),
                    _track_bar(rx.foreach(State.mutation_track_items, _mut_pin), "26px",
                               bg=rx.color("gray", 3)),
                    rx.text("Mutations · " + State.mutation_count.to_string() + " sites "
                            "(전체 구조 합산, UniProt 좌표)",
                            size="1", color=rx.color("gray", 9)),
                    spacing="1", width="100%", align="start",
                ),
            ),
            spacing="3", width="100%", align="start",
        ),
        border="1px solid var(--gray-5)", border_radius="16px",
        padding="1rem 1.25rem", width="100%", background=rx.color("gray", 2),
    )


# ── Drug Table (임상단계·진행상황 + 결합 PDB 중심) ──
_DRUG_COLUMN_DEFS = [
    {"field": "drug_name",       "headerName": "Drug Name",       "pinned": "left",
     "filter": True, "minWidth": 190,
     "headerTooltip": "ChEMBL 약물명. 정식 명칭이 없는(연구용) 화합물은 ChEMBL ID 로 표시"},
    {"field": "max_phase",       "headerName": "임상단계",         "filter": True, "maxWidth": 100,
     "headerTooltip": "최고 임상 단계 (ChEMBL): 4=승인, 3/2/1=임상, 공백=비임상"},
    {"field": "clinical_status", "headerName": "진행상황",         "filter": True, "minWidth": 110,
     "headerTooltip": "ClinicalTrials.gov 기반: 진행중 / 중단 / 승인 완료"},
    {"field": "n_pdb",           "headerName": "결합 PDB",         "filter": "agNumberColumnFilter",
     "maxWidth": 110, "headerTooltip": "이 약물이 결합한 PDB 구조 수. 행 클릭 시 어떤 PDB 인지 표시"},
    {"field": "molecule_type",   "headerName": "Type",            "filter": True, "maxWidth": 130},
    {"field": "chembl_id",       "headerName": "ChEMBL ID",       "filter": True, "minWidth": 130},
]


def _drug_struct_chip(s: dict) -> rx.Component:
    """결합 PDB 칩 — PDB ID + KLIFS 기반 inhibitor type. 클릭 시 구조 세부로 이동."""
    return rx.tooltip(
        rx.button(
            rx.hstack(
                rx.text(s["structure_id"], size="1", weight="bold"),
                rx.cond(s["inhibitor_type"] != "-",
                        rx.badge(s["inhibitor_type"], color_scheme="iris",
                                 variant="soft", size="1")),
                spacing="1", align="center",
            ),
            on_click=State.open_structure_from_drug(s["structure_id"]),
            variant="soft", size="1", color_scheme="gray",
        ),
        content="Inhibitor type 는 KLIFS DFG/αC 입체구조 기반 추정",
    )


def _trial_row(t: dict) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.link(t["nct_id"], href=t["nct_url"],
                    is_external=True, size="1", weight="bold"),
            rx.badge(t["phase"], variant="soft", color_scheme="gray", size="1"),
            rx.badge(t["overall_status"], variant="soft",
                     color_scheme=_ct_color(t["overall_status"]), size="1"),
            rx.text(t["conditions"], size="1", color=rx.color("gray", 10)),
            wrap="wrap", spacing="2", align="center", width="100%",
        ),
        rx.cond(t["why_stopped"] != "",
                rx.text("⛔ ", t["why_stopped"], size="1", color=rx.color("amber", 10))),
        width="100%",
    )


def _drug_detail_panel() -> rx.Component:
    """약물 상세 — 임상단계·진행상황·임상시험(CT.gov)·결합 PDB(+type)·AI 요약."""
    return rx.cond(
        State.selected_drug_id != "",
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.icon("pill", size=18, color=rx.color("accent", 9)),
                    rx.heading(State.drug_detail["drug_name"], size="4"),
                    rx.badge(State.drug_detail["phase_label"], color_scheme="jade",
                             variant="soft", size="2"),
                    rx.badge(State.drug_detail["clinical_status"],
                             color_scheme=_status_color(State.drug_detail["clinical_status"]),
                             size="2"),
                    rx.link("ChEMBL ↗",
                            href="https://www.ebi.ac.uk/chembl/compound_report_card/" + State.selected_drug_id,
                            is_external=True, size="2"),
                    spacing="3", align="center", wrap="wrap",
                ),
                # 약물 2D 구조 + 메타
                rx.hstack(
                    _struct_img(State.drug_image_url, "160px"),
                    rx.vstack(
                        _kv("Type", State.drug_detail["molecule_type"]),
                        _kv("결합 PDB", State.drug_structures.length().to_string() + " 개"),
                        _kv("임상시험", State.drug_trials.length().to_string() + " 건"),
                        spacing="2", align="start",
                    ),
                    spacing="4", align="center", wrap="wrap",
                ),
                # 결합 PDB 구조 (요청 2·3: 어떤 PDB + inhibitor type)
                rx.cond(
                    State.drug_structures.length() > 0,
                    rx.vstack(
                        rx.text("결합 PDB 구조 (PDB ID + inhibitor type · 클릭 → 구조 세부)",
                                size="2", weight="bold", color=rx.color("gray", 11)),
                        rx.hstack(rx.foreach(State.drug_structures, _drug_struct_chip),
                                  wrap="wrap", spacing="2"),
                        spacing="1", align="start", width="100%",
                    ),
                    rx.text("이 약물과 매핑된 PDB 구조가 없습니다.",
                            size="1", color=rx.color("gray", 9)),
                ),
                # 임상시험 (ClinicalTrials.gov) + AI 요약 (요청 5)
                rx.cond(
                    State.drug_trials.length() > 0,
                    rx.vstack(
                        rx.hstack(
                            rx.text("임상시험 (ClinicalTrials.gov)", size="2", weight="bold",
                                    color=rx.color("gray", 11)),
                            rx.spacer(),
                            rx.button(
                                rx.cond(State.summary_busy, rx.spinner(),
                                        rx.icon("sparkles", size=12)),
                                "AI 임상 요약",
                                on_click=State.summarize_trials, disabled=State.summary_busy,
                                size="1", variant="soft",
                            ),
                            width="100%", align="center",
                        ),
                        rx.cond(
                            State.drug_summary_ai != "",
                            rx.box(rx.text(State.drug_summary_ai, size="2",
                                           white_space="pre-wrap"),
                                   border="1px solid var(--accent-6)", border_radius="10px",
                                   padding="0.7rem 0.9rem", width="100%",
                                   background=rx.color("accent", 2)),
                        ),
                        rx.vstack(rx.foreach(State.drug_trials, _trial_row),
                                  spacing="2", width="100%", align="start"),
                        spacing="2", align="start", width="100%",
                    ),
                    rx.text("등록된 임상시험이 없습니다 (ClinicalTrials.gov 미검색 또는 없음).",
                            size="1", color=rx.color("gray", 9)),
                ),
                spacing="3", align="start", width="100%",
            ),
            border="1px solid var(--gray-5)", border_radius="16px",
            padding="1.1rem 1.25rem", width="100%", background=rx.color("gray", 2),
            margin_top="0.5rem",
        ),
    )


def _drug_table() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.button(
                rx.cond(State.chembl_busy, rx.spinner(), rx.icon("download", size=14)),
                "ChEMBL 약물·임상·PDB결합 가져오기/갱신",
                on_click=State.fetch_chembl, disabled=State.chembl_busy, size="2",
                variant="soft",
            ),
            rx.spacer(),
            rx.text("약물만", size="1", color=rx.color("gray", 10)),
            rx.switch(checked=State.drugs_only, on_change=State.toggle_drugs_only, size="1"),
            rx.cond(State.chembl_status != "",
                    rx.text(State.chembl_status, size="1", color=rx.color("gray", 10))),
            align="center", spacing="3", width="100%", wrap="wrap",
        ),
        rx.cond(
            State.drug_rows.length() > 0,
            rx.vstack(
                rx.text("임상단계·진행상황·결합 PDB 중심. 행 클릭 → 약물 상세(임상시험·결합 PDB+type). "
                        "'약물만' 끄면 활성 화합물 전체 표시.",
                        size="1", color=rx.color("gray", 9)),
                rx.box(
                    ag_grid(
                        column_defs=_DRUG_COLUMN_DEFS,
                        row_data=State.drug_rows,
                        default_col_def={"sortable": True, "resizable": True, "floatingFilter": True},
                        row_selection="single",
                        pagination=True,
                        pagination_page_size=20,
                        on_row_clicked=State.on_drug_clicked,
                    ),
                    width="100%",
                    height="400px",
                    custom_attrs={"data-ag-theme-mode": "dark"},
                ),
                _drug_detail_panel(),
                spacing="2", width="100%", align="start",
            ),
            rx.cond(
                ~State.chembl_busy,
                rx.callout(
                    "아직 ChEMBL 약물 데이터가 없습니다. 단백질 검색 시 자동 수집되며, "
                    "위 버튼으로 직접 갱신할 수도 있습니다.",
                    icon="info", color_scheme="gray",
                ),
            ),
        ),
        spacing="3", width="100%", align="start",
    )


# ── Papers (단백질 단위 통합 논문 목록 — DOI 로 묶어 1편당 1항목) ──
def _paper_pdb_badge(sid) -> rx.Component:
    return rx.button(sid, on_click=State.open_structure_from_drug(sid),
                     variant="soft", size="1", color_scheme="gray")


def _paper_card(p: dict) -> rx.Component:
    """통합 논문 카드 — 같은 DOI 의 여러 PDB 를 함께 표시. 배지 클릭 시 구조 세부로 이동."""
    return rx.box(
        rx.hstack(
            rx.icon("file-text", size=16, color=rx.color("accent", 9)),
            rx.text(p["title"], weight="bold", size="2"),
            rx.spacer(),
            rx.cond(p["n_pdb"].to(int) > 1,
                    rx.badge(p["n_pdb"].to_string() + " PDB", variant="soft",
                             color_scheme="gray", size="1")),
            rx.cond(p["has_structured"],
                    rx.badge("구조화 분석됨", color_scheme="green", size="1"),
                    rx.cond(p["has_pdf"],
                            rx.badge("PDF 업로드됨", color_scheme="gray", size="1"),
                            rx.badge("전체 분석", color_scheme="blue", variant="soft", size="1"))),
            rx.alert_dialog.root(
                rx.alert_dialog.trigger(
                    rx.icon_button("trash-2", size="1", variant="ghost",
                                   color_scheme="red", title="이 논문 삭제"),
                ),
                rx.alert_dialog.content(
                    rx.alert_dialog.title("논문 삭제"),
                    rx.alert_dialog.description(
                        "이 논문의 업로드 PDF·구조화 분석·연결을 삭제합니다. "
                        "같은 DOI 로 묶인 PDB 전체에서 제거됩니다.",
                        size="2",
                    ),
                    rx.flex(
                        rx.alert_dialog.cancel(
                            rx.button("취소", variant="soft", color_scheme="gray")),
                        rx.alert_dialog.action(
                            rx.button("삭제", color_scheme="red",
                                      on_click=State.delete_paper_event(p["structure_ids"].to(list)))),
                        spacing="3", justify="end", margin_top="1rem",
                    ),
                ),
            ),
            width="100%", align="center", spacing="2", wrap="wrap",
        ),
        rx.hstack(
            rx.text("PDB", size="1", weight="bold", color=rx.color("gray", 10)),
            rx.foreach(p["structure_ids"].to(list), _paper_pdb_badge),
            wrap="wrap", spacing="1", align="center", width="100%",
        ),
        rx.cond(p["authors"] != "",
                rx.text(p["authors"], size="1", color=rx.color("gray", 9))),
        rx.cond(p["doi"] != "",
                rx.link(p["doi"], href=p["doi_url"], is_external=True, size="1")),
        border="1px solid var(--gray-5)", border_radius="12px",
        padding="0.7rem 0.9rem", width="100%",
    )


def _uploaded_papers_view() -> rx.Component:
    """기존 업로드 논문 목록(삭제 가능) — PDB 세부에서 업로드된 것."""
    return rx.cond(
        State.paper_count > 0,
        rx.vstack(
            rx.text("같은 논문(DOI)의 여러 PDB 는 한 항목으로 묶입니다. 한 곳에 업로드·분석하면 "
                    "같은 논문의 모든 PDB 에 반영됩니다. PDB 배지 클릭 → 구조 세부.",
                    size="1", color=rx.color("gray", 9)),
            rx.foreach(State.paper_list, _paper_card),
            spacing="2", width="100%", align="start",
        ),
        rx.callout(
            "이 단백질에 연결된 논문이 아직 없습니다. 'PDB 구조' 탭에서 PDB 를 선택하고 논문 PDF 를 업로드하세요.",
            icon="info", color_scheme="gray",
        ),
    )


# ══════════════════════════════════════════════════════════════════
# 문헌 Playground UI (Claude 연구공간)
# ══════════════════════════════════════════════════════════════════
def _new_pg_dialog(trigger: rx.Component) -> rx.Component:
    """주제/하위주제 생성 다이얼로그. trigger 버튼이 new_pg_parent 를 먼저 세팅한다."""
    return rx.dialog.root(
        rx.dialog.trigger(trigger),
        rx.dialog.content(
            rx.dialog.title("연구 주제(Playground) 생성"),
            rx.dialog.description(
                "이 단백질을 주제로 한 연구 공간을 만듭니다. 대화가 raw 로 누적되고 "
                "'정리' 버튼으로 정리본이 생성됩니다.", size="1", color=rx.color("gray", 10)),
            rx.input(placeholder="예: cMET 저해제 내성 변이 SAR",
                     value=State.new_pg_name, on_change=State.set_new_pg_name,
                     margin_top="0.7rem"),
            rx.flex(
                rx.dialog.close(rx.button("취소", variant="soft", color_scheme="gray",
                                          on_click=State.set_new_pg_name(""))),
                rx.dialog.close(rx.button("생성", on_click=State.create_playground_event)),
                spacing="3", justify="end", margin_top="1rem",
            ),
            max_width="420px",
        ),
    )


def _pg_tree_row(pg: dict) -> rx.Component:
    """Playground 트리 한 줄 — 들여쓰기(depth), 열기, ＋하위, 삭제."""
    return rx.hstack(
        rx.box(width=pg["indent_px"], flex_shrink="0"),
        rx.button(
            rx.icon(rx.cond(pg["has_digest"], "book-check", "message-square"),
                    size=13, flex_shrink="0"),
            rx.text(pg["name"], size="1", flex_grow="1", min_width="0",
                    style={"whiteSpace": "nowrap", "overflow": "hidden",
                           "textOverflow": "ellipsis", "textAlign": "left"}),
            rx.cond(pg["n_messages"].to(int) > 0,
                    rx.badge(pg["n_messages"].to_string(), size="1",
                             variant="soft", flex_shrink="0")),
            variant=rx.cond(pg["is_active"], "solid", "soft"),
            color_scheme=rx.cond(pg["is_active"], "iris", "gray"),
            on_click=State.open_playground(pg["id"]),
            size="1", justify="start", flex_grow="1", flex_shrink="1",
            min_width="0", overflow="hidden",
        ),
        _new_pg_dialog(
            rx.icon_button("plus", size="1", variant="ghost", color_scheme="gray",
                           title="하위 주제 추가",
                           on_click=State.set_new_pg_parent(pg["id"])),
        ),
        rx.alert_dialog.root(
            rx.alert_dialog.trigger(
                rx.icon_button("trash-2", size="1", variant="ghost",
                               color_scheme="red", title="주제 삭제")),
            rx.alert_dialog.content(
                rx.alert_dialog.title("주제 삭제"),
                rx.alert_dialog.description(
                    "이 주제와 하위 주제·대화·연결 문헌이 모두 삭제됩니다.", size="2"),
                rx.flex(
                    rx.alert_dialog.cancel(rx.button("취소", variant="soft", color_scheme="gray")),
                    rx.alert_dialog.action(rx.button("삭제", color_scheme="red",
                                                     on_click=State.delete_playground_event(pg["id"]))),
                    spacing="3", justify="end", margin_top="1rem"),
            ),
        ),
        width="100%", spacing="1", align="center",
    )


def _pg_left_panel() -> rx.Component:
    return rx.vstack(
        _new_pg_dialog(
            rx.button(rx.icon("plus", size=14), "주제 생성", size="1",
                      width="100%", on_click=State.set_new_pg_parent(0)),
        ),
        rx.cond(
            State.pg_count > 0,
            rx.vstack(rx.foreach(State.pg_tree, _pg_tree_row),
                      spacing="1", width="100%", align="start"),
            rx.text("아직 주제가 없습니다. '주제 생성'으로 시작하세요.",
                    size="1", color=rx.color("gray", 9)),
        ),
        spacing="2", width="260px", flex_shrink="0", align="start",
    )


def _pg_message(m: dict) -> rx.Component:
    is_user = m["role"] == "user"
    return rx.box(
        rx.hstack(
            rx.badge(rx.cond(is_user, "나", "조수"),
                     color_scheme=rx.cond(is_user, "gray", "iris"), size="1"),
            rx.spacer(),
            rx.icon_button("trash-2", size="1", variant="ghost", color_scheme="gray",
                           title="메시지 삭제",
                           on_click=State.delete_pg_message_event(m["id"])),
            width="100%", align="center",
        ),
        rx.markdown(m["content"]),
        background=rx.cond(is_user, rx.color("gray", 3), rx.color("iris", 2)),
        border="1px solid var(--gray-4)", border_radius="10px",
        padding="0.4rem 0.7rem", width="100%",
    )


def _pg_paper_ref(p: dict) -> rx.Component:
    return rx.hstack(
        rx.icon("file-text", size=13, color=rx.color("gray", 10)),
        rx.text(p["title"], size="1", no_of_lines=1, flex_grow="1"),
        rx.cond(p["matched"],
                rx.button(p["matched_structure_id"], size="1", variant="soft",
                          color_scheme="green",
                          on_click=State.open_matched_structure(p["matched_structure_id"]),
                          title="매칭된 PDB 구조로 이동"),
                rx.badge("매칭 없음", size="1", color_scheme="gray", variant="surface")),
        rx.icon_button("x", size="1", variant="ghost", color_scheme="gray",
                       on_click=State.delete_pg_paper_event(p["id"])),
        width="100%", spacing="2", align="center",
    )


def _pg_papers_panel() -> rx.Component:
    return rx.vstack(
        rx.text("연결 문헌 (DB 논문과 매칭 시 PDB·약물 연결)", size="1", weight="bold",
                color=rx.color("gray", 11)),
        rx.cond(State.pg_papers.length() > 0,
                rx.vstack(rx.foreach(State.pg_papers, _pg_paper_ref),
                          spacing="1", width="100%")),
        rx.hstack(
            rx.input(placeholder="문헌 제목", value=State.pg_paper_title,
                     on_change=State.set_pg_paper_title, size="1", flex_grow="1"),
            rx.input(placeholder="DOI(선택)", value=State.pg_paper_doi,
                     on_change=State.set_pg_paper_doi, size="1", width="140px"),
            rx.button("참조 추가", size="1", variant="soft",
                      on_click=State.add_pg_paper_ref),
            rx.el.label(
                rx.hstack(
                    rx.cond(State.pg_uploading, rx.spinner(size="1"), rx.icon("file-up", size=13)),
                    rx.text("PDF 업로드", size="1"),
                    spacing="1", align="center",
                ),
                rx.el.input(type="file", id="pg_paper_input", accept="application/pdf",
                            on_change=State.start_pg_paper_upload,
                            style={"display": "none"}),
                style={"display": "inline-flex", "alignItems": "center",
                       "border": "1px solid var(--gray-6)", "borderRadius": "6px",
                       "padding": "0.25rem 0.6rem", "cursor": "pointer"},
            ),
            width="100%", spacing="2", align="center", wrap="wrap",
        ),
        spacing="2", width="100%", align="start",
        border="1px solid var(--gray-4)", border_radius="10px", padding="0.7rem",
    )


def _pg_active_panel() -> rx.Component:
    """열린 Playground — 정리본(메인) + 접힌 raw 대화 + 입력창 + 연결문헌."""
    return rx.vstack(
        # 헤더: 이름·브레드크럼·정리/이름수정
        rx.hstack(
            rx.icon_button("arrow-left", size="1", variant="ghost",
                           on_click=State.close_playground, title="목록으로"),
            rx.icon("book-open", size=16, color=rx.color("iris", 10)),
            rx.heading(State.pg_name, size="4"),
            rx.dialog.root(
                rx.dialog.trigger(
                    rx.icon_button("pencil", size="1", variant="ghost", color_scheme="gray",
                                   title="이름 수정",
                                   on_click=State.set_rename_pg_value(State.pg_name))),
                rx.dialog.content(
                    rx.dialog.title("주제 이름 수정"),
                    rx.input(value=State.rename_pg_value,
                             on_change=State.set_rename_pg_value, margin_top="0.5rem"),
                    rx.flex(
                        rx.dialog.close(rx.button("취소", variant="soft", color_scheme="gray")),
                        rx.dialog.close(rx.button("저장", on_click=State.rename_playground_event)),
                        spacing="3", justify="end", margin_top="1rem"),
                    max_width="380px",
                ),
            ),
            rx.spacer(),
            rx.button(
                rx.cond(State.pg_summarizing, rx.spinner(), rx.icon("sparkles", size=14)),
                "정리", size="1", variant="soft", color_scheme="iris",
                on_click=State.summarize_playground,
                disabled=State.pg_summarizing | (State.pg_msg_count == 0),
                title="대화 전체를 체계적 정리본으로 재생성(Sonnet)"),
            _new_pg_dialog(
                rx.button(rx.icon("git-branch-plus", size=14), "하위 주제", size="1",
                          variant="soft", color_scheme="gray",
                          on_click=State.set_new_pg_parent(State.active_pg_id))),
            width="100%", align="center", spacing="2", wrap="wrap",
        ),
        rx.cond(State.pg_status != "",
                rx.text(State.pg_status, size="1", color=rx.color("iris", 10))),

        # 정리본(digest) — 메인 뷰
        rx.cond(
            State.pg_has_digest,
            rx.box(
                rx.hstack(
                    rx.icon("book-check", size=14, color=rx.color("green", 10)),
                    rx.text("정리본", size="2", weight="bold"),
                    rx.cond(State.pg_digest_updated != "",
                            rx.text(State.pg_digest_updated, size="1", color=rx.color("gray", 9))),
                    align="center", spacing="2",
                ),
                rx.markdown(State.pg_digest),
                background=rx.color("green", 2), border="1px solid var(--green-4)",
                border_radius="10px", padding="0.7rem 0.9rem", width="100%",
            ),
            rx.callout("아직 정리본이 없습니다. 대화 후 '정리' 버튼을 누르면 지금까지 다룬 내용을 "
                       "체계적으로 정리합니다.", icon="info", color_scheme="gray", size="1"),
        ),

        # 연결 문헌 패널
        _pg_papers_panel(),

        # raw 대화 (접힘/펼침)
        rx.hstack(
            rx.button(
                rx.icon(rx.cond(State.pg_show_raw, "chevron-down", "chevron-right"), size=14),
                rx.cond(State.pg_show_raw, "대화 접기", "대화 펼치기 (raw)"),
                rx.badge(State.pg_msg_count.to_string(), size="1", variant="soft"),
                size="1", variant="ghost", color_scheme="gray",
                on_click=State.toggle_pg_raw),
            width="100%", align="center",
        ),
        rx.cond(
            State.pg_show_raw,
            rx.vstack(rx.foreach(State.pg_messages, _pg_message),
                      spacing="2", width="100%"),
        ),

        # 입력창
        rx.hstack(
            rx.text_area(
                placeholder="질문을 입력하세요 (Shift+Enter 줄바꿈). 앱 DB·문헌·인터넷을 활용해 분석을 요청할 수 있습니다.",
                value=State.pg_input, on_change=State.set_pg_input,
                flex_grow="1", rows="3"),
            rx.button(
                rx.cond(State.pg_chatting, rx.spinner(), rx.icon("send", size=14)),
                "전송", on_click=State.send_pg_message,
                disabled=State.pg_chatting, size="2"),
            width="100%", spacing="2", align="end",
        ),
        spacing="3", width="100%", align="start", flex_grow="1",
    )


def _playground_section() -> rx.Component:
    return rx.hstack(
        _pg_left_panel(),
        rx.box(
            rx.cond(
                State.active_pg_id > 0,
                _pg_active_panel(),
                rx.center(
                    rx.vstack(
                        rx.icon("book-open", size=40, color=rx.color("gray", 7)),
                        rx.text("연구 주제(Playground)를 선택하거나 새로 만드세요.",
                                size="2", color=rx.color("gray", 10)),
                        rx.text("Claude 와 대화하며 이 단백질을 연구하고, 대화를 정리본으로 축적합니다.",
                                size="1", color=rx.color("gray", 9)),
                        spacing="2", align="center",
                    ),
                    min_height="300px", width="100%",
                ),
            ),
            flex_grow="1", border="1px solid var(--gray-4)", border_radius="12px",
            padding="0.9rem", min_height="360px",
        ),
        width="100%", align="start", spacing="3",
    )


def _papers_view() -> rx.Component:
    """문헌 탭 — Playground(연구공간)가 메인 + 업로드 논문 목록(접힘)."""
    return rx.vstack(
        _playground_section(),
        rx.divider(margin_y="0.5rem"),
        rx.accordion.root(
            rx.accordion.item(
                header=rx.hstack(
                    rx.icon("files", size=15),
                    rx.text("업로드된 논문 목록", size="2", weight="medium"),
                    rx.badge(State.paper_count.to_string(), size="1"),
                    spacing="2", align="center"),
                content=rx.box(_uploaded_papers_view(), padding_top="0.7rem"),
                value="uploaded",
            ),
            collapsible=True, type="single", width="100%", variant="ghost",
        ),
        spacing="3", width="100%", align="start",
    )


# ── Sequence (UniProt 형식) ──
def _sequence_view() -> rx.Component:
    return rx.cond(
        State.sequence_fmt != "",
        rx.box(
            rx.text(State.sequence_fmt, white_space="pre", font_family="monospace",
                    font_size="0.78rem"),
            border="1px solid var(--gray-5)", border_radius="16px", padding="0.9rem",
            width="100%", overflow="auto", max_height="240px",
        ),
        rx.text("서열 없음", color_scheme="gray", size="2"),
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


def _struct_img(url, size: str = "150px") -> rx.Component:
    """2D 구조 이미지 — 흰 배경 카드(어두운 테마에서 흑색 결합선 가시성 확보)."""
    return rx.box(
        rx.image(src=url, width="100%", height="100%",
                 style={"objectFit": "contain"}, loading="lazy"),
        width=size, height=size, background="white", border_radius="8px",
        padding="4px", flex_shrink="0",
    )


def _lig_item(l: dict) -> rx.Component:
    """리간드 — 2D 구조 + CCD/이름 (요청: 셀 클릭 시 리간드 구조 2D)."""
    return rx.hstack(
        rx.cond(l["img_url"] != "", _struct_img(l["img_url"], "130px")),
        rx.vstack(
            rx.hstack(
                rx.badge(l["ligand_id"], variant="soft", color_scheme="gray", size="1"),
                rx.link("RCSB ↗", href=l["rcsb_url"], is_external=True, size="1"),
                spacing="2", align="center",
            ),
            rx.text(l["ligand_name"], size="1", color=rx.color("gray", 11)),
            spacing="1", align="start",
        ),
        spacing="3", align="center",
    )


def _bound_drug_item(d: dict) -> rx.Component:
    """PDB 세부 패널의 결합 약물 한 줄 — 리간드·약물명(클릭→약물탭)·임상단계·ChEMBL 링크."""
    return rx.hstack(
        rx.badge(d["ligand_id"], variant="soft", color_scheme="gray", size="1"),
        rx.button(d["pref_name"], on_click=State.open_drug_from_structure(d["chembl_id"]),
                  variant="ghost", size="1", weight="bold"),
        rx.badge(d["phase_label"], color_scheme="jade", variant="soft", size="1"),
        rx.link("↗", href=d["chembl_url"], is_external=True, size="1"),
        spacing="2", align="center", wrap="wrap",
    )


def _partner_item(p: dict) -> rx.Component:
    return rx.text("• ", p["partner_gene_name"], " (", p["partner_uniprot_id"],
                   ")  chains: ", p["chains_str"], size="2")


def _oligo_item(o: dict) -> rx.Component:
    return rx.text("• ", o["name"], "  @chain ", o["linked_chain"], size="2")


def _cond_card(label: str, key: str) -> rx.Component:
    return rx.box(
        rx.heading(label, size="3"),
        rx.text(State.pdb_conditions[key], white_space="pre-wrap", size="2"),
        border="1px solid var(--gray-5)", border_radius="24px", padding="0.9rem 1rem", width="100%",
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
            # 결합 리간드 중 알려진 약물(ChEMBL) — 구조→약물 연결
            rx.cond(
                State.detail_drugs.length() > 0,
                rx.box(
                    rx.text("💊 결합 약물 (ChEMBL)", weight="bold", size="2",
                            color=rx.color("accent", 11)),
                    rx.vstack(rx.foreach(State.detail_drugs, _bound_drug_item),
                              spacing="1", align="start", width="100%"),
                    border="1px solid var(--accent-6)", border_radius="12px",
                    padding="0.7rem 0.9rem", width="100%", margin_top="0.3rem",
                ),
            ),
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
            # Supabase Storage 직접 업로드 (브라우저→Storage, Reflex Cloud 크기한계 우회)
            # 클릭 → 파일 선택 → start_direct_upload 가 서명URL 생성 → JS 가 직접 PUT.
            rx.el.label(
                rx.cond(
                    State.uploaded_name != "",
                    rx.vstack(
                        rx.icon("file-text", size=24),
                        rx.text("📄 " + State.uploaded_name, size="2", weight="bold"),
                        rx.text("다른 PDF 로 교체하려면 클릭", size="1", color_scheme="gray"),
                        align="center", spacing="1",
                    ),
                    rx.vstack(
                        rx.icon("file-up", size=26),
                        rx.text("여기를 클릭해 논문 PDF 선택 (대용량 가능)", size="2"),
                        align="center", spacing="1",
                    ),
                ),
                rx.el.input(
                    type="file", id="pdf_direct_input", accept="application/pdf",
                    on_change=State.start_direct_upload,
                    style={"display": "none"},
                ),
                display="flex", align_items="center", justify_content="center",
                border="2px dashed var(--accent-8)", padding="1.1rem",
                border_radius="24px", width="360px", cursor="pointer",
            ),
            rx.cond(State.uploading,
                    rx.hstack(rx.spinner(),
                              rx.text("Storage 로 업로드 중...", size="2"),
                              spacing="2")),
            rx.cond(
                State.has_pdf,
                rx.button("🔗 이 PDB 의 논문 PDF 새 창에서 열기", on_click=State.open_pdf,
                          variant="ghost", size="2", weight="bold"),
            ),
            # papers 에만 있는 전체 분석 논문(PDF 유실 등) — PDF 없이 구조화 가능
            rx.cond(
                State.has_full_paper & (~State.has_pdf),
                rx.box(
                    rx.hstack(
                        rx.icon("file-check", size=16, color=rx.color("jade", 9)),
                        rx.text("기존 전체 분석 논문 있음:", size="1", weight="bold",
                                color=rx.color("gray", 11)),
                        rx.text(State.full_paper_title, size="1", color=rx.color("gray", 11)),
                        wrap="wrap", spacing="2", align="center",
                    ),
                    rx.text("PDF 는 없지만 저장된 분석본으로 구조화 분석을 할 수 있습니다.",
                            size="1", color=rx.color("gray", 9)),
                    rx.button("🔬 기존 분석본에서 구조화", on_click=State.run_conditions_from_paper,
                              disabled=State.cond_analyzing, size="2", variant="soft"),
                    border="1px solid var(--jade-6)", border_radius="12px",
                    padding="0.7rem 0.9rem", width="100%",
                ),
            ),
            rx.button("🔬 구조화 분석", on_click=State.run_pdb_conditions,
                      disabled=State.cond_analyzing | (~State.has_pdf)),
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
# 레이아웃 (단일 페이지 — 사이드바 없음)
# ═══════════════════════════════════════════
def layout(content: rx.Component) -> rx.Component:
    """게이트 없이 바로 콘텐츠 렌더 (비밀번호 제거)."""
    return rx.box(
        rx.box(content, width="100%", max_width="1500px", margin="0 auto"),
        padding="1.5rem", width="100%",
    )


# ═══════════════════════════════════════════
# 페이지: Construct Builder
# ═══════════════════════════════════════════
def _collected_card(p: dict) -> rx.Component:
    """수집 완료 단백질 카드 — 클릭 시 바로 로드."""
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.heading(p["gene_name"], size="4", weight="bold",
                           color=rx.color("gray", 12)),
                rx.spacer(),
                rx.badge(p["n_pdb"].to_string() + " PDB", variant="soft",
                         color_scheme="iris", size="1"),
                width="100%", align="center",
            ),
            rx.badge(p["uniprot_id"], variant="soft", color_scheme="gray", size="1", radius="full"),
            rx.text(p["organism"], size="1", color=rx.color("gray", 10)),
            rx.text(p["sequence_length"].to_string() + " aa", size="1", color=rx.color("gray", 9)),
            spacing="1", align="start", width="100%",
        ),
        on_click=State.open_protein(p["uniprot_id"]),
        border="1px solid var(--gray-5)", border_radius="14px",
        padding="0.9rem 1rem", width="200px", cursor="pointer",
        background=rx.color("gray", 2),
        _hover={"border_color": rx.color("accent", 8), "background": rx.color("gray", 3)},
        transition="all 0.12s",
    )


def _center_search() -> rx.Component:
    """단백질 미선택 시 — 가운데 큰 검색창 + 수집 완료 단백질 목록."""
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
            # 수집 완료된 단백질 — 다시 확인하기 편하게 카드 목록
            rx.cond(
                State.collected_proteins.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.icon("history", size=15, color=rx.color("gray", 10)),
                        rx.text("수집 완료 — 클릭해서 바로 열기", size="2",
                                weight="bold", color=rx.color("gray", 11)),
                        spacing="2", align="center",
                    ),
                    rx.flex(
                        rx.foreach(State.collected_proteins, _collected_card),
                        wrap="wrap", spacing="3", justify="center",
                    ),
                    spacing="3", align="center", width="100%", margin_top="1.5rem",
                ),
            ),
            spacing="4", align="center", width="100%",
            on_mount=State.load_collected,
        ),
        min_height="72vh", width="100%",
    )


def _top_bar() -> rx.Component:
    """상단 고정 — 검색 + 단백질 식별 정보 (참조 이미지의 Protein/Gene/Amino acids 바)."""
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.heading(State.gene_name, size="6", weight="bold",
                           color=rx.color("gray", 12)),
                rx.badge(State.selected_uid, variant="soft", color_scheme="gray",
                         size="2", radius="full"),
                rx.text("·", color=rx.color("gray", 8)),
                rx.text(State.organism, size="2", weight="medium", color=rx.color("gray", 11)),
                rx.text("·", color=rx.color("gray", 8)),
                rx.text(State.seq_length.to_string() + " aa", size="2",
                        weight="medium", color=rx.color("gray", 11)),
                rx.spacer(),
                rx.input(value=State.query, on_change=State.set_query,
                         placeholder="다른 단백질 검색", width="220px", size="2"),
                rx.button("검색", on_click=State.search, disabled=State.collecting, size="2"),
                rx.cond(State.collecting, rx.spinner()),
                width="100%", align="center", spacing="3", wrap="wrap",
            ),
            spacing="1", width="100%",
        ),
        position="sticky", top="0", z_index="10",
        background=rx.color("gray", 1),
        border_bottom=f"1px solid {rx.color('gray', 5)}",
        padding="0.9rem 0.25rem", width="100%",
    )


def _structures_tab() -> rx.Component:
    return rx.vstack(
        _protein_overview(),
        rx.text("각 행의 'UniProt Coverage' 열은 도메인(반투명 배경) 위에 그 구조가 커버하는 "
                "잔기범위(막대)를 표시합니다. 행을 클릭하면 아래에 세부정보 + 논문 분석이 나타납니다.",
                color_scheme="gray", size="2"),
        _structures_grid(),
        _pdb_detail_panel(),
        spacing="3", width="100%", align="start",
    )


def _results_view() -> rx.Component:
    return rx.vstack(
        _top_bar(),
        # ── 데이터 축별 탭 (도메인·커버리지는 PDB 구조 탭의 그리드 행 안에 통합) ──
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger(
                    rx.hstack(rx.text("PDB 구조"),
                              rx.badge(State.structure_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="pdb"),
                rx.tabs.trigger(
                    rx.hstack(rx.text("약물"),
                              rx.badge(State.drug_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="bio"),
                rx.tabs.trigger(
                    rx.hstack(rx.text("문헌"),
                              rx.badge(State.paper_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="papers"),
                rx.tabs.trigger("Sequence", value="seq"),
            ),
            rx.tabs.content(_structures_tab(), value="pdb", padding_top="1rem"),
            rx.tabs.content(_drug_table(), value="bio", padding_top="1rem"),
            rx.tabs.content(_papers_view(), value="papers", padding_top="1rem"),
            rx.tabs.content(_sequence_view(), value="seq", padding_top="1rem"),
            value=State.active_tab, on_change=State.set_active_tab, width="100%",
        ),
        spacing="4", align="start", width="100%",
    )


def builder_content() -> rx.Component:
    return rx.cond(State.selected_uid != "", _results_view(), _center_search())


def index() -> rx.Component:
    return layout(builder_content())


app = rx.App(
    # theme 은 rxconfig.py 의 RadixThemesPlugin 에서 설정(appearance="dark").
    stylesheets=["styles.css"],
    # ★다크 강제: 배포(prod) 빌드는 next-themes 가 localStorage["theme"] 또는 시스템
    #  설정을 따라가 라이트로 뒤집히는 문제가 있었다(집=다크OS 정상, 회사=라이트OS 흰 박스).
    #  하이드레이션 전에 localStorage 를 dark 로 고정해 항상 다크로 resolve 되게 한다.
    head_components=[
        rx.script(
            "try{window.localStorage.setItem('theme','dark');"
            "document.documentElement.classList.add('dark');}catch(e){}"
        ),
    ],
)
app.add_page(index, route="/", title="PDB database")
