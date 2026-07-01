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
    get_clinical_drugs_by_uniprot,
    get_structures_for_drug,
    get_drugs_for_structure,
    get_drug_detail,
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
    drug_detail: dict = {}                   # {drug_name, max_phase, first_approval, molecule_type}
    drug_indications: list[dict] = []        # 질환 indication 목록
    drug_structures: list[dict] = []         # 이 약물이 결합한 PDB 구조

    # 현재 PDB 에 결합한 약물(ChEMBL 매핑) — PDB 세부 패널용
    detail_drugs: list[dict] = []

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
        self.drug_indications = []
        self.drug_structures = []

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
        """약물 행 클릭 → 상세(임상단계·승인·질환·결합 PDB) 로드 (Synapse식 연결)."""
        cid = row.get("chembl_id")
        if not cid:
            return
        self.selected_drug_id = cid
        try:
            det = get_drug_detail(cid, self.selected_uid)
            comp = det.get("compound", {}) or {}
            comp["drug_name"] = comp.get("pref_name") or cid
            comp["phase_label"] = _phase_label(comp.get("max_phase"))
            comp["first_approval_str"] = (str(comp["first_approval"])
                                          if comp.get("first_approval") else "—")
            comp["molecule_type"] = comp.get("molecule_type") or "—"
            self.drug_detail = comp
            inds = det.get("indications", []) or []
            for i in inds:
                i["phase_label"] = _phase_label(i.get("max_phase_for_ind"))
                i["mesh_heading"] = i.get("mesh_heading") or "—"
            self.drug_indications = inds
        except Exception:
            self.drug_detail = {}
            self.drug_indications = []
        try:
            self.drug_structures = get_structures_for_drug(cid, self.selected_uid)
        except Exception:
            self.drug_structures = []

    @rx.event
    def open_structure_from_drug(self, sid: str):
        """약물 상세의 결합 PDB 클릭 → 구조 세부 로드 (축 간 이동)."""
        if sid:
            self._load_detail(sid)

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


# ── Drug Table (임상단계 + 결합 PDB 중심 — IC50/활성 제외) ──
_DRUG_COLUMN_DEFS = [
    {"field": "drug_name",      "headerName": "Drug Name",       "pinned": "left",
     "filter": True, "minWidth": 190,
     "headerTooltip": "ChEMBL 약물명. 정식 명칭이 없는(연구용) 화합물은 ChEMBL ID 로 표시"},
    {"field": "max_phase",      "headerName": "임상단계",         "filter": True, "maxWidth": 100,
     "headerTooltip": "최고 임상 단계 (ChEMBL): 4=승인, 3/2/1=임상, 공백=비임상/전임상"},
    {"field": "top_indication", "headerName": "대표 Indication",  "filter": True, "minWidth": 200,
     "headerTooltip": "최고 개발 단계 질환 (ChEMBL drug_indication). 상세는 행 클릭"},
    {"field": "n_pdb",          "headerName": "결합 PDB",         "filter": "agNumberColumnFilter",
     "maxWidth": 110, "headerTooltip": "이 약물의 리간드가 결합한 이 단백질의 PDB 구조 수 (UniChem 매핑)"},
    {"field": "first_approval", "headerName": "승인연도",         "filter": "agNumberColumnFilter",
     "maxWidth": 110, "headerTooltip": "최초 승인 연도 (ChEMBL first_approval)"},
    {"field": "molecule_type",  "headerName": "Type",            "filter": True, "maxWidth": 130},
    {"field": "chembl_id",      "headerName": "ChEMBL ID",       "filter": True, "minWidth": 130},
]


def _drug_struct_chip(s: dict) -> rx.Component:
    """약물 상세의 결합 PDB 칩 — 클릭 시 구조 세부로 이동(축 간 연결)."""
    return rx.button(
        s["structure_id"],
        on_click=State.open_structure_from_drug(s["structure_id"]),
        variant="soft", size="1", color_scheme="gray",
    )


def _indication_row(i: dict) -> rx.Component:
    return rx.hstack(
        rx.badge(i["phase_label"], color_scheme="jade", variant="soft", size="1"),
        rx.text(i["mesh_heading"], size="2"),
        spacing="2", align="center",
    )


def _drug_detail_panel() -> rx.Component:
    """약물 상세 — 임상단계·승인·질환 indication·결합 PDB (Synapse식 약물 중심 연결)."""
    return rx.cond(
        State.selected_drug_id != "",
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.icon("pill", size=18, color=rx.color("accent", 9)),
                    rx.heading(State.drug_detail["drug_name"], size="4"),
                    rx.badge(State.drug_detail["phase_label"], color_scheme="jade",
                             variant="soft", size="2"),
                    rx.link("ChEMBL ↗",
                            href="https://www.ebi.ac.uk/chembl/compound_report_card/" + State.selected_drug_id,
                            is_external=True, size="2"),
                    spacing="3", align="center", wrap="wrap",
                ),
                # 임상 메타
                rx.hstack(
                    _kv("승인연도", State.drug_detail["first_approval_str"]),
                    _kv("Type", State.drug_detail["molecule_type"]),
                    _kv("결합 PDB", State.drug_structures.length().to_string() + " 개"),
                    wrap="wrap", spacing="4",
                ),
                # 질환 indications (임상 개발 대상)
                rx.cond(
                    State.drug_indications.length() > 0,
                    rx.vstack(
                        rx.text("질환 Indications (임상 개발 대상)", size="2", weight="bold",
                                color=rx.color("gray", 11)),
                        rx.vstack(rx.foreach(State.drug_indications, _indication_row),
                                  spacing="1", align="start", width="100%"),
                        spacing="1", align="start", width="100%",
                    ),
                ),
                # 결합 PDB 구조 (구조 축으로 이동)
                rx.cond(
                    State.drug_structures.length() > 0,
                    rx.vstack(
                        rx.text("결합 PDB 구조 (클릭 → 구조 세부)", size="2", weight="bold",
                                color=rx.color("gray", 11)),
                        rx.hstack(rx.foreach(State.drug_structures, _drug_struct_chip),
                                  wrap="wrap", spacing="2"),
                        spacing="1", align="start", width="100%",
                    ),
                    rx.text("이 약물과 매핑된 PDB 구조가 없습니다 (리간드 미결합 또는 UniChem 미등록).",
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
                rx.text("임상단계·질환·결합 PDB 중심 (활성/IC50 제외). 행 클릭 → 약물 상세. "
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


def _bound_drug_item(d: dict) -> rx.Component:
    """PDB 세부 패널의 결합 약물 한 줄 — 리간드·약물명·임상단계·ChEMBL 링크."""
    return rx.hstack(
        rx.badge(d["ligand_id"], variant="soft", color_scheme="gray", size="1"),
        rx.text(d["pref_name"], size="2", weight="bold"),
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
# 레이아웃 (단일 페이지 — 사이드바 없음)
# ═══════════════════════════════════════════
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
        rx.box(
            rx.box(content, width="100%", max_width="1500px", margin="0 auto"),
            padding="1.5rem", width="100%",
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
