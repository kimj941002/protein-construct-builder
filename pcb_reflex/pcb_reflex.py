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
    upsert_paper_conditions,
    save_paper_pdf,
    get_paper_pdf,
    get_all_mutations_by_uniprot,
    get_drug_table_by_uniprot,
    get_papers_by_uniprot,
)
from uniprot_fetcher import load_sequence_from_file
from collect import collect_protein

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


def _materialize_pdf(sid: str, data: bytes | None = None) -> None:
    """PDF 바이트를 업로드 디렉토리에 {sid}.pdf 로 써서 /_upload 로 서빙되게 한다.
    (Reflex 내장 /_upload 경로는 로컬·Reflex Cloud 모두에서 백엔드로 라우팅됨)."""
    import pathlib
    updir = pathlib.Path(rx.get_upload_dir())
    fpath = updir / f"{sid}.pdf"
    if data is None:
        if fpath.exists():
            return
        data, _ = get_paper_pdf(sid)
        if not data:
            return
    updir.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(data)


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
    # 업로드 진행 표시
    uploading: bool = False
    upload_progress: int = 0
    has_pdf: bool = False   # 현재 PDB 에 저장된 PDF 있는지

    # Feature Viewer (protein-level, 공유 좌표축 위 정렬 트랙)
    mutation_track_items: list[dict] = []   # {left, color, mutation, position, label}
    ruler_ticks: list[dict] = []            # {left, label}
    pdb_coverage_items: list[dict] = []     # {left, width, label} — 구조별 잔기 범위

    # 약물 테이블 (ChEMBL bioactivities summary)
    drug_rows: list[dict] = []

    # 논문 (단백질 단위 통합)
    paper_rows: list[dict] = []             # papers 테이블
    analysis_rows: list[dict] = []          # paper_analysis (PDB별 구조화)

    @rx.var
    def drug_count(self) -> int:
        return len(self.drug_rows)

    @rx.var
    def paper_count(self) -> int:
        return len(self.paper_rows) + len(self.analysis_rows)

    @rx.var
    def mutation_count(self) -> int:
        return len(self.mutation_track_items)

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

        # 구조 표 (+ KLIFS: DFG/αC/추정 타입)
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
        self.structures = structs

        # 변이 트랙 — protein 전체 고유 변이 위치
        all_muts = get_all_mutations_by_uniprot(uid)
        denom = seq_len or 1390
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

        # PDB coverage 트랙 — 각 구조의 residue_range 를 같은 축에 막대로
        cov = []
        for s in structs:
            rng = (s.get("residue_range") or "").strip()
            start = end = None
            if rng and rng not in ("-", "None"):
                # "1-1390" / "P08581:1-1390" / "1–1390" 형태 방어적 파싱
                import re as _re
                nums = _re.findall(r"\d+", rng)
                if len(nums) >= 2:
                    start, end = int(nums[-2]), int(nums[-1])
            if start is None or end is None or end < start:
                continue
            # 좌표 클램프 — 범위를 벗어난(저자번호 오염 등) 막대가 트랙 밖으로 튀지 않게
            cstart = min(max(start, 1), denom)
            cend = min(max(end, 1), denom)
            left = (cstart - 1) / denom * 100
            width = max(0.4, (cend - cstart + 1) / denom * 100)
            if left + width > 100:
                width = 100 - left
            cov.append({
                "left": f"{left:.2f}%", "width": f"{width:.2f}%",
                "label": f"{s['structure_id']}: {start}-{end}",
            })
        self.pdb_coverage_items = cov

        # 약물 테이블 (compound_activity_summary — 데이터 없으면 빈 리스트)
        try:
            self.drug_rows = get_drug_table_by_uniprot(uid)
        except Exception:
            self.drug_rows = []

        # 논문 (단백질 단위 통합) — foreach 내 문자열 연결 방지 위해 Python 측에서 정규화
        try:
            papers, analyses = get_papers_by_uniprot(uid)
            for pp in papers:
                pp["title"] = pp.get("title") or "(제목 없음)"
                pp["authors"] = pp.get("authors") or ""
                pp["doi"] = pp.get("doi") or ""
                pp["structure_id"] = pp.get("structure_id") or ""
                pp["doi_url"] = ("https://doi.org/" + pp["doi"]) if pp["doi"] else ""
            for aa in analyses:
                aa["pdf_name"] = aa.get("pdf_name") or "(파일명 없음)"
                aa["structure_id"] = aa.get("structure_id") or ""
                aa["has_structured"] = bool(aa.get("has_structured"))
            self.paper_rows = papers
            self.analysis_rows = analyses
        except Exception:
            self.paper_rows = []
            self.analysis_rows = []

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
        # 업로드 상태 초기화 + 이 PDB 에 저장된 PDF 반영 (PDB 바꾸면 이전 파일명 사라짐)
        stored_name = (pa or {}).get("pdf_name") or ""
        self.uploaded_name = stored_name
        self.has_pdf = bool(stored_name)
        if self.has_pdf:
            try:
                _materialize_pdf(sid)  # 업로드 디렉토리에 없으면 1회만 적재
            except Exception:
                pass
        self.uploaded_pdf_path = ""
        self.uploading = False
        self.upload_progress = 0
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
        # 임시 경로 없으면 DB 에 저장된 PDF 바이트로 임시파일 생성
        if not pdf:
            data, _ = get_paper_pdf(sid)
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

    # ── Phase 2: 논문 업로드 + 분석 ──
    @rx.event
    def on_upload_progress(self, prog: dict):
        """업로드 전송 진행률(0~100%) 표시."""
        p = prog.get("progress", 0) or 0
        self.upload_progress = round(p * 100)
        self.uploading = self.upload_progress < 100

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
        name = getattr(f, "name", None) or getattr(f, "filename", "") or "uploaded.pdf"
        self.uploaded_name = name
        self.analyze_result_md = ""
        self.analyze_status = ""

        if not self.detail_sid:
            self.cond_status = "먼저 PDB 행을 선택한 뒤 PDF 를 올려주세요."
            self.uploading = False
            self.upload_progress = 0
            return
        if not data:
            self.cond_status = "PDF 내용을 읽지 못했습니다(빈 파일)."
            self.uploading = False
            self.upload_progress = 0
            return

        # 1) DB 에 원본 저장(누적, 덮어쓰기) — 이게 성공하면 업로드 성공으로 간주
        try:
            save_paper_pdf(self.detail_sid, data, name)
        except Exception as ex:
            self.has_pdf = False
            self.cond_status = f"PDF 저장 실패: {type(ex).__name__}: {str(ex)[:160]}"
            self.uploading = False
            self.upload_progress = 0
            return
        self.has_pdf = True
        # 새 PDF 로 교체 → 이전 구조화 분석 결과 비우고 재분석 유도
        self.pdb_conditions = {}
        self.has_conditions = False

        # 2) 새 창 열람용 서빙 파일 적재(실패해도 비치명적 — 열 때 DB 에서 재생성)
        try:
            _materialize_pdf(self.detail_sid, data)
        except Exception:
            pass

        self.cond_status = "새 PDF 업로드 완료 — '구조화 분석'을 눌러 새로 분석하세요."
        self.uploading = False
        self.upload_progress = 100

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
_COLUMN_DEFS = [
    {"field": "structure_id", "headerName": "PDB ID", "pinned": "left", "filter": True,
     "minWidth": 120},
    {"field": "method", "headerName": "Method", "filter": True},
    {"field": "resolution", "headerName": "Res (Å)", "filter": "agNumberColumnFilter", "maxWidth": 110},
    {"field": "complex_type", "headerName": "Complex", "filter": True},
    {"field": "inhibitor_type", "headerName": "Inhibitor type", "filter": True, "maxWidth": 130,
     "headerTooltip": "KLIFS DFG/αC 입체구조 기반 추정 (실험 분류 아님). 출처·근거: DATA_PROVENANCE.md §2"},
    {"field": "dfg", "headerName": "DFG", "filter": True, "maxWidth": 90,
     "headerTooltip": "KLIFS (klifs.net) DFG motif in/out"},
    {"field": "ac_helix", "headerName": "αC-helix", "filter": True, "maxWidth": 100,
     "headerTooltip": "KLIFS (klifs.net) αC-helix in/out"},
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
        custom_attrs={"data-ag-theme-mode": "dark"},  # AG Grid v33 다크 테마
    )


# ═══════════════════════════════════════════
# Feature Viewer — 공유 좌표축(1D ruler) 위에 도메인·변이·PDB 트랙 정렬
# (참조 이미지의 BACE1 Feature Viewer 레이아웃)
# ═══════════════════════════════════════════
_FV_LABEL_W = "132px"   # 좌측 트랙 라벨 열 너비


def _fv_row(label: str, bar: rx.Component, sub: str = "") -> rx.Component:
    """라벨 열 + 트랙 막대 한 줄."""
    return rx.hstack(
        rx.vstack(
            rx.text(label, size="2", weight="bold", color=rx.color("gray", 12)),
            rx.cond(sub != "", rx.text(sub, size="1", color=rx.color("gray", 9))),
            spacing="0", align="start", width=_FV_LABEL_W, flex_shrink="0",
        ),
        rx.box(bar, flex="1", position="relative", min_width="0"),
        width="100%", align="center", spacing="2",
    )


# 트랙 세그먼트 빌더 -------------------------------------------------
def _domain_seg(d: dict) -> rx.Component:
    return rx.tooltip(
        rx.box(position="absolute", left=d["left"], width=d["width"],
               top="0", height="22px", background=d["color"], border_radius="3px"),
        content=d["label"],
    )


def _mut_pin(item: dict) -> rx.Component:
    return rx.tooltip(
        rx.box(
            position="absolute", left=item["left"], top="0",
            width="2px", height="26px", background=item["color"], border_radius="1px",
        ),
        content=item["label"],
    )


def _cov_seg(c: dict) -> rx.Component:
    return rx.tooltip(
        rx.box(
            position="absolute", left=c["left"], width=c["width"],
            top="0", height="22px", background=rx.color("accent", 9),
            opacity="0.22", border_radius="2px",
        ),
        content=c["label"],
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


def _feature_viewer() -> rx.Component:
    """단백질 1D 좌표축 위에 Domains / Mutations / PDB Structures 트랙을 정렬."""
    return rx.box(
        rx.vstack(
            # 눈금자 (라벨 열 비움)
            rx.hstack(
                rx.box(width=_FV_LABEL_W, flex_shrink="0"),
                rx.box(
                    rx.foreach(State.ruler_ticks, _ruler_tick),
                    flex="1", position="relative", height="20px",
                    min_width="0", overflow="visible",
                ),
                width="100%", spacing="2",
            ),
            # Domains
            _fv_row(
                "Domains",
                _track_bar(rx.foreach(State.domains, _domain_seg), "22px",
                           bg=rx.color("gray", 3)),
            ),
            # Mutations
            _fv_row(
                "Mutations",
                _track_bar(rx.foreach(State.mutation_track_items, _mut_pin), "26px",
                           bg=rx.color("gray", 3)),
                sub=State.mutation_count.to_string() + " sites",
            ),
            # PDB Structures coverage
            _fv_row(
                "PDB Structures",
                _track_bar(rx.foreach(State.pdb_coverage_items, _cov_seg), "22px",
                           bg=rx.color("gray", 3)),
                sub=State.structure_count.to_string() + " 구조",
            ),
            spacing="3", width="100%", align="start",
        ),
        border="1px solid var(--gray-5)", border_radius="16px",
        padding="1.1rem 1.25rem", width="100%",
        background=rx.color("gray", 2),
    )


def _domain_legend() -> rx.Component:
    """도메인 색상 범례 (Feature Viewer 아래 보조)."""
    def _row(d: dict) -> rx.Component:
        return rx.hstack(
            rx.box(width="10px", height="10px", background=d["color"], border_radius="2px"),
            rx.text(d["name"], size="1", weight="medium"),
            rx.text(d["range"], size="1", color_scheme="gray"),
            spacing="1", align="center",
        )
    return rx.cond(
        State.domains.length() > 0,
        rx.hstack(rx.foreach(State.domains, _row), wrap="wrap", spacing="3",
                  width="100%", padding_top="0.25rem"),
    )


# ── Drug / Bioactivity Table (ChEMBL summary) ──
_DRUG_COLUMN_DEFS = [
    {"field": "chembl_id",      "headerName": "ChEMBL ID",      "filter": True, "minWidth": 140},
    {"field": "pref_name",      "headerName": "Drug Name",       "filter": True, "minWidth": 160},
    {"field": "max_phase",      "headerName": "Phase",           "filter": True, "maxWidth": 90,
     "headerTooltip": "최고 임상 단계 (ChEMBL max_phase)"},
    {"field": "median_pchembl", "headerName": "Median pChEMBL",  "filter": "agNumberColumnFilter",
     "maxWidth": 150, "headerTooltip": "여러 어세이의 중앙값 pChEMBL (−log₁₀ 몰 농도)"},
    {"field": "best_nM",        "headerName": "Best (nM)",       "filter": "agNumberColumnFilter",
     "maxWidth": 120, "headerTooltip": "농도 단위 어세이 중 최저 IC50/Ki (nM)"},
    {"field": "n_records",      "headerName": "# Assays",        "filter": "agNumberColumnFilter",
     "maxWidth": 110},
]


def _drug_table() -> rx.Component:
    return rx.cond(
        State.drug_rows.length() > 0,
        rx.box(
            ag_grid(
                column_defs=_DRUG_COLUMN_DEFS,
                row_data=State.drug_rows,
                default_col_def={"sortable": True, "resizable": True, "floatingFilter": True},
                pagination=True,
                pagination_page_size=20,
            ),
            width="100%",
            height="400px",
            custom_attrs={"data-ag-theme-mode": "dark"},
        ),
        rx.callout(
            "ChEMBL 데이터 없음 — 터미널에서 python chembl_fetcher.py 를 실행하세요.",
            icon="info",
            color_scheme="gray",
        ),
    )


# ── Papers (단백질 단위 통합 논문 목록) ──
def _paper_card(p: dict) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("file-text", size=16, color=rx.color("accent", 9)),
            rx.text(p["title"], weight="bold", size="2"),
            rx.spacer(),
            rx.cond(p["structure_id"] != "",
                    rx.badge(p["structure_id"], variant="soft", color_scheme="gray", size="1")),
            width="100%", align="center", spacing="2",
        ),
        rx.cond(p["authors"] != "",
                rx.text(p["authors"], size="1", color=rx.color("gray", 9))),
        rx.cond(p["doi"] != "",
                rx.link(p["doi"], href=p["doi_url"], is_external=True, size="1")),
        border="1px solid var(--gray-5)", border_radius="12px",
        padding="0.7rem 0.9rem", width="100%",
    )


def _analysis_card(a: dict) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.icon("microscope", size=16, color=rx.color("accent", 9)),
            rx.badge(a["structure_id"], variant="soft", color_scheme="gray", size="1"),
            rx.text(a["pdf_name"], size="2"),
            rx.spacer(),
            rx.cond(a["has_structured"],
                    rx.badge("구조화 분석됨", color_scheme="green", size="1"),
                    rx.badge("PDF만", color_scheme="gray", size="1")),
            width="100%", align="center", spacing="2",
        ),
        border="1px solid var(--gray-5)", border_radius="12px",
        padding="0.6rem 0.9rem", width="100%",
    )


def _papers_view() -> rx.Component:
    return rx.cond(
        State.paper_count > 0,
        rx.vstack(
            rx.cond(
                State.paper_rows.length() > 0,
                rx.vstack(
                    rx.text("전체 분석 논문", size="2", weight="bold", color=rx.color("gray", 11)),
                    rx.foreach(State.paper_rows, _paper_card),
                    spacing="2", width="100%", align="start",
                ),
            ),
            rx.cond(
                State.analysis_rows.length() > 0,
                rx.vstack(
                    rx.text("PDB별 업로드 논문", size="2", weight="bold", color=rx.color("gray", 11)),
                    rx.foreach(State.analysis_rows, _analysis_card),
                    spacing="2", width="100%", align="start",
                ),
            ),
            rx.text("PDB 구조 탭에서 행을 선택하면 해당 PDB 논문을 업로드·분석할 수 있습니다.",
                    size="1", color=rx.color("gray", 9), padding_top="0.5rem"),
            spacing="4", width="100%", align="start",
        ),
        rx.callout(
            "이 단백질에 연결된 논문이 아직 없습니다. 'PDB 구조' 탭에서 PDB 를 선택하고 논문 PDF 를 업로드하세요.",
            icon="info", color_scheme="gray",
        ),
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
                rx.cond(
                    State.uploaded_name != "",
                    rx.vstack(
                        rx.icon("file-text", size=24),
                        rx.text("📄 " + State.uploaded_name, size="2", weight="bold"),
                        rx.text("다른 PDF 로 교체하려면 클릭/드롭", size="1", color_scheme="gray"),
                        align="center", spacing="1",
                    ),
                    rx.vstack(
                        rx.icon("file-up", size=26),
                        rx.text("여기를 클릭해 논문 PDF 선택 (선택 즉시 업로드)", size="2"),
                        align="center", spacing="1",
                    ),
                ),
                id="pdf_cond", accept={"application/pdf": [".pdf"]}, max_files=1,
                on_drop=State.handle_pdf_upload(
                    rx.upload_files(upload_id="pdf_cond",
                                    on_upload_progress=State.on_upload_progress)
                ),
                border="2px dashed var(--accent-8)", padding="1.1rem",
                border_radius="24px", width="360px", cursor="pointer",
            ),
            rx.cond(State.uploading,
                    rx.hstack(rx.spinner(),
                              rx.text("업로드 중... " + State.upload_progress.to_string() + "%"),
                              spacing="2")),
            rx.cond(
                State.has_pdf,
                rx.link("🔗 이 PDB 의 논문 PDF 새 창에서 열기",
                        href=rx.get_upload_url(State.detail_sid + ".pdf"),
                        is_external=True, size="2", weight="bold"),
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
        rx.text("표에서 PDB 행을 클릭하면 아래에 세부정보 + 논문 분석이 나타납니다.",
                color_scheme="gray", size="2"),
        _structures_grid(),
        _pdb_detail_panel(),
        spacing="2", width="100%", align="start",
    )


def _results_view() -> rx.Component:
    return rx.vstack(
        _top_bar(),
        # ── 공유 좌표축 Feature Viewer (항상 표시 = 유기적 통합의 척추) ──
        _feature_viewer(),
        _domain_legend(),
        # ── 데이터 축별 탭 (Feature Viewer 아래, 같은 프레임) ──
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger(
                    rx.hstack(rx.text("PDB 구조"),
                              rx.badge(State.structure_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="pdb"),
                rx.tabs.trigger(
                    rx.hstack(rx.text("활성·약물"),
                              rx.badge(State.drug_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="bio"),
                rx.tabs.trigger(
                    rx.hstack(rx.text("논문"),
                              rx.badge(State.paper_count.to_string(), size="1"),
                              spacing="1", align="center"),
                    value="papers"),
                rx.tabs.trigger("Sequence", value="seq"),
            ),
            rx.tabs.content(_structures_tab(), value="pdb", padding_top="1rem"),
            rx.tabs.content(_drug_table(), value="bio", padding_top="1rem"),
            rx.tabs.content(_papers_view(), value="papers", padding_top="1rem"),
            rx.tabs.content(_sequence_view(), value="seq", padding_top="1rem"),
            default_value="pdb", width="100%",
        ),
        spacing="4", align="start", width="100%",
    )


def builder_content() -> rx.Component:
    return rx.cond(State.selected_uid != "", _results_view(), _center_search())


def index() -> rx.Component:
    return layout(builder_content())


app = rx.App(
    theme=rx.theme(
        appearance="dark",
        accent_color="gray",     # 무채색(중성) — CSS 로 톤 보정
        gray_color="slate",
        radius="large",
        scaling="100%",
    ),
    stylesheets=["styles.css"],
)
app.add_page(index, route="/", title="PDB database")
