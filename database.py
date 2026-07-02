# database.py
# Supabase(Postgres) 13-테이블 스키마용 CRUD 함수 (SQLAlchemy Engine 기반).
# UNIFIED_MIGRATION_PLAN.md §3 / §4.
#
# DB 접근 단일 진입점: db_config.get_engine()  (→ Supabase transaction pooler)
# 스키마 정의(DDL)는 supabase_schema.sql 한 파일에서 관리한다.
#
# 테이블:
#   proteins / protein_domains / pdb_structures / structure_mutations /
#   ligands / partner_proteins / partner_protein_chains / ptm_oligosaccharides /
#   klifs_structures / paper_analysis / app_state  (+ papers, chats: 별도 모듈)
from __future__ import annotations

from pathlib import Path

from decimal import Decimal

from sqlalchemy import text, bindparam

from db_config import get_engine


def _jsonable(rows: list) -> list[dict]:
    """Decimal → float 변환 (Reflex State JSON 직렬화 안전). mappings() 결과용."""
    out = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
        out.append(d)
    return out


# ═══════════════════════════════════════════
# 스키마 초기화 (멱등) — supabase_schema.sql 실행
# ═══════════════════════════════════════════
def init_database():
    """
    supabase_schema.sql 을 실행해 테이블을 생성한다 (CREATE ... IF NOT EXISTS, 멱등).
    Supabase 대시보드에서 이미 생성했다면 안전하게 통과한다.
    """
    schema_file = Path(__file__).parent / "supabase_schema.sql"
    if not schema_file.exists():
        print("[WARN] supabase_schema.sql 없음 — 스키마 생성 건너뜀")
        return
    ddl = schema_file.read_text(encoding="utf-8")
    with get_engine().begin() as conn:
        # psycopg2 는 한 번의 execute 로 다중 문장 실행을 허용한다.
        conn.exec_driver_sql(ddl)
    print("[OK] Supabase 스키마 적용 완료")


# ═══════════════════════════════════════════
# 1. proteins CRUD
# ═══════════════════════════════════════════

def insert_protein(data: dict):
    """
    proteins 테이블에 삽입합니다. 같은 uniprot_id 면 갱신(UPSERT)합니다.
    data 키: uniprot_id, gene_name, protein_name, organism,
             sequence_path, sequence_length, function_desc,
             subcellular_location, signal_peptide
    """
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO proteins
                (uniprot_id, gene_name, protein_name, organism,
                 sequence_path, sequence_length, function_desc,
                 subcellular_location, signal_peptide)
            VALUES
                (:uniprot_id, :gene_name, :protein_name, :organism,
                 :sequence_path, :sequence_length, :function_desc,
                 :subcellular_location, :signal_peptide)
            ON CONFLICT (uniprot_id) DO UPDATE SET
                gene_name            = EXCLUDED.gene_name,
                protein_name         = EXCLUDED.protein_name,
                organism             = EXCLUDED.organism,
                sequence_path        = EXCLUDED.sequence_path,
                sequence_length      = EXCLUDED.sequence_length,
                function_desc        = EXCLUDED.function_desc,
                subcellular_location = EXCLUDED.subcellular_location,
                signal_peptide       = EXCLUDED.signal_peptide
        """), data)


def get_protein(uniprot_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM proteins WHERE uniprot_id = :id"),
            {"id": uniprot_id},
        ).mappings().first()
    return dict(row) if row else None


def get_all_proteins() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT * FROM proteins")).mappings().all()
    return [dict(r) for r in rows]


def delete_protein(uniprot_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM proteins WHERE uniprot_id = :id"),
            {"id": uniprot_id},
        )


# ═══════════════════════════════════════════
# app_state — 앱 상태 영구 저장 (마지막 선택 단백질 등)
# ═══════════════════════════════════════════

def save_last_selected_protein(uniprot_id: str):
    """마지막으로 선택/검색한 단백질 ID를 DB에 저장합니다."""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO app_state (key, value)
            VALUES ('last_selected_protein', :v)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """), {"v": uniprot_id})


def load_last_selected_protein() -> str | None:
    """마지막으로 선택/검색한 단백질 ID를 DB에서 읽어옵니다."""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_state WHERE key = 'last_selected_protein'")
            ).first()
        return row[0] if row else None
    except Exception:
        return None


# ═══════════════════════════════════════════
# 2. protein_domains CRUD
# ═══════════════════════════════════════════

def insert_domain(data: dict):
    """data 키: uniprot_id, name, start_pos, end_pos"""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO protein_domains (uniprot_id, name, start_pos, end_pos)
            VALUES (:uniprot_id, :name, :start_pos, :end_pos)
        """), data)


def insert_domains_bulk(uniprot_id: str, domains: list[dict]):
    """domains: [{"name": str, "start": int, "end": int}, ...]"""
    if not domains:
        return
    params = [
        {"uniprot_id": uniprot_id, "name": d.get("name"),
         "start_pos": d.get("start"), "end_pos": d.get("end")}
        for d in domains
    ]
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO protein_domains (uniprot_id, name, start_pos, end_pos)
            VALUES (:uniprot_id, :name, :start_pos, :end_pos)
        """), params)


def get_domains_by_uniprot(uniprot_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM protein_domains WHERE uniprot_id = :id"),
            {"id": uniprot_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_domains_by_uniprot(uniprot_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM protein_domains WHERE uniprot_id = :id"),
            {"id": uniprot_id},
        )


# ═══════════════════════════════════════════
# 3. pdb_structures CRUD
# ═══════════════════════════════════════════

def insert_structure(data: dict):
    """mutations 컬럼 없음 — structure_mutations 테이블 사용. 같은 structure_id 면 갱신."""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO pdb_structures
                (structure_id, uniprot_id, source, method, resolution, mean_plddt,
                 chain_id, residue_range, expression_system, host_cell_line,
                 crystal_method, crystal_ph, crystal_temp, crystal_details, space_group,
                 complex_type, doi, deposition_date)
            VALUES
                (:structure_id, :uniprot_id, :source, :method, :resolution, :mean_plddt,
                 :chain_id, :residue_range, :expression_system, :host_cell_line,
                 :crystal_method, :crystal_ph, :crystal_temp, :crystal_details, :space_group,
                 :complex_type, :doi, :deposition_date)
            ON CONFLICT (structure_id) DO UPDATE SET
                uniprot_id        = EXCLUDED.uniprot_id,
                source            = EXCLUDED.source,
                method            = EXCLUDED.method,
                resolution        = EXCLUDED.resolution,
                mean_plddt        = EXCLUDED.mean_plddt,
                chain_id          = EXCLUDED.chain_id,
                residue_range     = EXCLUDED.residue_range,
                expression_system = EXCLUDED.expression_system,
                host_cell_line    = EXCLUDED.host_cell_line,
                crystal_method    = EXCLUDED.crystal_method,
                crystal_ph        = EXCLUDED.crystal_ph,
                crystal_temp      = EXCLUDED.crystal_temp,
                crystal_details   = EXCLUDED.crystal_details,
                space_group       = EXCLUDED.space_group,
                complex_type      = EXCLUDED.complex_type,
                doi               = EXCLUDED.doi,
                deposition_date   = EXCLUDED.deposition_date
        """), data)


def get_structures_by_uniprot(uniprot_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM pdb_structures WHERE uniprot_id = :id"),
            {"id": uniprot_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_structure(structure_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM pdb_structures WHERE structure_id = :id"),
            {"id": structure_id},
        ).mappings().first()
    return dict(row) if row else None


def delete_structure(structure_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM pdb_structures WHERE structure_id = :id"),
            {"id": structure_id},
        )


# ═══════════════════════════════════════════
# 4. structure_mutations CRUD
# ═══════════════════════════════════════════

def insert_mutations_bulk(structure_id: str, mutations: list[dict]):
    """
    mutations: [{"mutation": "K1110A", "position": 1110, "type": "engineered"}, ...]
    기존 데이터는 먼저 삭제 후 삽입합니다.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM structure_mutations WHERE structure_id = :sid"),
            {"sid": structure_id},
        )
        if mutations:
            params = [
                {"sid": structure_id, "mutation": m.get("mutation"),
                 "position": m.get("position"), "mutation_type": m.get("type")}
                for m in mutations
            ]
            conn.execute(text("""
                INSERT INTO structure_mutations (structure_id, mutation, position, mutation_type)
                VALUES (:sid, :mutation, :position, :mutation_type)
            """), params)


def get_mutations_by_structure(structure_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM structure_mutations WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_mutations_bulk(structure_ids: list[str]) -> dict[str, list[dict]]:
    """
    여러 structure_id의 mutation을 한 번의 쿼리로 조회합니다 (N+1 방지).
    Returns: {structure_id: [mutation dict, ...], ...}  (mutation 없으면 키 없음)
    """
    if not structure_ids:
        return {}
    stmt = text(
        "SELECT * FROM structure_mutations WHERE structure_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt, {"ids": list(structure_ids)}).mappings().all()
    result: dict[str, list[dict]] = {}
    for r in rows:
        result.setdefault(r["structure_id"], []).append(dict(r))
    return result


def delete_mutations_by_structure(structure_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM structure_mutations WHERE structure_id = :sid"),
            {"sid": structure_id},
        )


# ═══════════════════════════════════════════
# 5. ligands CRUD
# ═══════════════════════════════════════════

def insert_ligand(data: dict):
    """data 키: structure_id, ligand_id, ligand_name, formula, smiles, ligand_type"""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO ligands
                (structure_id, ligand_id, ligand_name, formula, smiles, ligand_type)
            VALUES
                (:structure_id, :ligand_id, :ligand_name, :formula, :smiles, :ligand_type)
        """), data)


def get_ligands_by_structure(structure_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM ligands WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_ligands_by_structure(structure_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM ligands WHERE structure_id = :sid"),
            {"sid": structure_id},
        )


# ═══════════════════════════════════════════
# 6. partner_proteins CRUD
# ═══════════════════════════════════════════

def insert_partner_protein(data: dict) -> int:
    """
    data 키: structure_id, entity_id, partner_uniprot_id, partner_gene_name,
             partner_chain_id, sequence_length, organism,
             partner_residue_range, partner_expression_system
    Returns:
        int: 삽입된 행의 id (partner_protein_chains 삽입에 사용)
    """
    with get_engine().begin() as conn:
        row = conn.execute(text("""
            INSERT INTO partner_proteins
                (structure_id, entity_id, partner_uniprot_id, partner_gene_name,
                 partner_chain_id, sequence_length, organism,
                 partner_residue_range, partner_expression_system)
            VALUES
                (:structure_id, :entity_id, :partner_uniprot_id, :partner_gene_name,
                 :partner_chain_id, :sequence_length, :organism,
                 :partner_residue_range, :partner_expression_system)
            RETURNING id
        """), data).first()
    return row[0]


def get_partners_by_structure(structure_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM partner_proteins WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_partners_by_structure(structure_id: str):
    # partner_protein_chains 는 FK ON DELETE CASCADE 로 자동 삭제됨.
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM partner_proteins WHERE structure_id = :sid"),
            {"sid": structure_id},
        )


# ═══════════════════════════════════════════
# 7. partner_protein_chains CRUD
# ═══════════════════════════════════════════

def insert_partner_chains_bulk(partner_id: int, chains: list[str]):
    """chains: ["A", "B", ...] 형태의 체인 ID 목록"""
    if not chains:
        return
    params = [{"partner_id": partner_id, "chain_id": c} for c in chains]
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO partner_protein_chains (partner_id, chain_id)
            VALUES (:partner_id, :chain_id)
        """), params)


def get_chains_by_partner(partner_id: int) -> list[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT chain_id FROM partner_protein_chains WHERE partner_id = :pid"),
            {"pid": partner_id},
        ).all()
    return [r[0] for r in rows]


def get_all_chains_by_structure(structure_id: str) -> dict[int, list[str]]:
    """
    structure_id에 속한 모든 partner의 chain 목록을 한 번에 반환합니다.
    Returns: {partner_id: [chain_id, ...], ...}
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT ppc.partner_id, ppc.chain_id
            FROM partner_protein_chains ppc
            JOIN partner_proteins pp ON ppc.partner_id = pp.id
            WHERE pp.structure_id = :sid
        """), {"sid": structure_id}).all()
    result: dict[int, list[str]] = {}
    for pid, cid in rows:
        result.setdefault(pid, []).append(cid)
    return result


# ═══════════════════════════════════════════
# 8. ptm_oligosaccharides CRUD
# ═══════════════════════════════════════════

def insert_oligosaccharide(data: dict):
    """data 키: structure_id, entity_id, name, chain_id, linked_chain, linked_position, linked_residue"""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO ptm_oligosaccharides
                (structure_id, entity_id, name, chain_id,
                 linked_chain, linked_position, linked_residue)
            VALUES
                (:structure_id, :entity_id, :name, :chain_id,
                 :linked_chain, :linked_position, :linked_residue)
        """), data)


def get_oligosaccharides_by_structure(structure_id: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM ptm_oligosaccharides WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_oligosaccharides_by_structure(structure_id: str):
    with get_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM ptm_oligosaccharides WHERE structure_id = :sid"),
            {"sid": structure_id},
        )


# ═══════════════════════════════════════════
# 9. klifs_structures CRUD
# ═══════════════════════════════════════════

def insert_klifs_structure(data: dict):
    """
    data 키: structure_id, dfg, ac_helix
    DFG 형태와 αC Helix 형태만 저장합니다.
    이미 존재하는 경우 NULL이 아닌 값만 덮어씁니다 (기존 데이터 보호).
    """
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO klifs_structures (structure_id, dfg, ac_helix)
            VALUES (:structure_id, :dfg, :ac_helix)
            ON CONFLICT (structure_id) DO UPDATE SET
                dfg      = COALESCE(EXCLUDED.dfg,      klifs_structures.dfg),
                ac_helix = COALESCE(EXCLUDED.ac_helix, klifs_structures.ac_helix)
        """), data)


def get_klifs_by_structure(structure_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM klifs_structures WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().first()
    return dict(row) if row else None


def get_klifs_bulk(structure_ids: list[str]) -> dict[str, dict]:
    """structure_id → klifs dict 매핑을 한 번에 반환합니다."""
    if not structure_ids:
        return {}
    stmt = text(
        "SELECT * FROM klifs_structures WHERE structure_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt, {"ids": list(structure_ids)}).mappings().all()
    return {r["structure_id"]: dict(r) for r in rows}


# ═══════════════════════════════════════════
# 10. paper_analysis CRUD
# ═══════════════════════════════════════════

def upsert_paper_analysis(data: dict):
    """
    paper_analysis 테이블에 삽입하거나 기존 행을 업데이트합니다.
    data 키: structure_id (필수), pdf_path, status, raw_text, analyzed_at
    """
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_analysis (structure_id, pdf_path, status, raw_text, analyzed_at)
            VALUES (:structure_id, :pdf_path, :status, :raw_text, :analyzed_at)
            ON CONFLICT (structure_id) DO UPDATE SET
                pdf_path    = EXCLUDED.pdf_path,
                status      = EXCLUDED.status,
                raw_text    = EXCLUDED.raw_text,
                analyzed_at = EXCLUDED.analyzed_at
        """), {
            "structure_id": data.get("structure_id"),
            "pdf_path":     data.get("pdf_path"),
            "status":       data.get("status", "none"),
            "raw_text":     data.get("raw_text"),
            "analyzed_at":  data.get("analyzed_at"),
        })


def get_paper_analysis(structure_id: str) -> dict | None:
    # pdf_bytes(대용량)는 제외하고 조회
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT structure_id, status, raw_text, structured, analyzed_at, pdf_name "
                 "FROM paper_analysis WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).mappings().first()
    return dict(row) if row else None


def save_paper_pdf(structure_id: str, pdf_bytes: bytes, pdf_name: str):
    """PDB별 논문 PDF 원본을 paper_analysis 에 저장(누적)."""
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_analysis (structure_id, pdf_bytes, pdf_name, status)
            VALUES (:sid, :b, :n, COALESCE((SELECT status FROM paper_analysis WHERE structure_id=:sid), 'uploaded'))
            ON CONFLICT (structure_id) DO UPDATE SET
                pdf_bytes = EXCLUDED.pdf_bytes,
                pdf_name  = EXCLUDED.pdf_name
        """), {"sid": structure_id, "b": pdf_bytes, "n": pdf_name})


def get_paper_pdf(structure_id: str) -> tuple[bytes | None, str | None]:
    """저장된 PDF 원본(바이트, 파일명) 반환."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT pdf_bytes, pdf_name FROM paper_analysis WHERE structure_id = :sid"),
            {"sid": structure_id},
        ).first()
    if not row or row[0] is None:
        return None, None
    return bytes(row[0]), row[1]


# ── DOI 공유: 같은 논문(DOI)을 공유하는 구조끼리 업로드·분석 결과를 공유 ──
def _doi_group(conn, structure_id: str) -> list[str]:
    """이 구조와 같은 DOI 를 가진 구조 id 목록 (자기 포함). DOI 없으면 [자기]만."""
    row = conn.execute(
        text("SELECT doi FROM pdb_structures WHERE structure_id = :s"),
        {"s": structure_id},
    ).first()
    doi = ((row[0] if row else None) or "").strip()
    if not doi:
        return [structure_id]
    sids = [r[0] for r in conn.execute(
        text("SELECT structure_id FROM pdb_structures WHERE doi = :d"), {"d": doi}
    ).all()]
    return sids or [structure_id]


def get_paper_analysis_shared(structure_id: str) -> dict | None:
    """DOI 공유 조회 — 이 구조 또는 같은 DOI 형제 구조의 논문 분석을 합쳐 반환.

    반환: {structured, pdf_name, has_pdf, status, pdf_owner} (구조화·PDF 는 형제 중 보유분 사용).
    """
    with get_engine().connect() as conn:
        sids = _doi_group(conn, structure_id)
        q_struct = text(
            "SELECT structure_id, structured, status FROM paper_analysis "
            "WHERE structure_id IN :sids AND structured IS NOT NULL "
            "ORDER BY (structure_id = :self) DESC LIMIT 1"
        ).bindparams(bindparam("sids", expanding=True))
        srow = conn.execute(q_struct, {"sids": sids, "self": structure_id}).mappings().first()
        q_pdf = text(
            "SELECT structure_id, pdf_name FROM paper_analysis "
            "WHERE structure_id IN :sids AND pdf_bytes IS NOT NULL "
            "ORDER BY (structure_id = :self) DESC LIMIT 1"
        ).bindparams(bindparam("sids", expanding=True))
        prow = conn.execute(q_pdf, {"sids": sids, "self": structure_id}).mappings().first()
    if not srow and not prow:
        return None
    return {
        "structured": (srow or {}).get("structured"),
        "status": (srow or {}).get("status") or ("uploaded" if prow else "none"),
        "pdf_name": (prow or {}).get("pdf_name"),
        "has_pdf": bool(prow),
        "pdf_owner": (prow or {}).get("structure_id"),
        "struct_owner": (srow or {}).get("structure_id"),
    }


def get_paper_pdf_shared(structure_id: str) -> tuple[bytes | None, str | None]:
    """DOI 공유 PDF 조회 — 형제 구조 중 PDF 보유분의 바이트 반환."""
    with get_engine().connect() as conn:
        sids = _doi_group(conn, structure_id)
        q = text(
            "SELECT pdf_bytes, pdf_name FROM paper_analysis "
            "WHERE structure_id IN :sids AND pdf_bytes IS NOT NULL "
            "ORDER BY (structure_id = :self) DESC LIMIT 1"
        ).bindparams(bindparam("sids", expanding=True))
        row = conn.execute(q, {"sids": sids, "self": structure_id}).first()
    if not row or row[0] is None:
        return None, None
    return bytes(row[0]), row[1]


def upsert_paper_conditions(structure_id: str, conditions: dict, status: str = "completed"):
    """PDB별 논문 구조화 분석 결과(structured JSONB)를 paper_analysis 에 저장."""
    import json
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_analysis (structure_id, status, structured, analyzed_at)
            VALUES (:sid, :st, CAST(:j AS JSONB), now())
            ON CONFLICT (structure_id) DO UPDATE SET
                status      = EXCLUDED.status,
                structured  = EXCLUDED.structured,
                analyzed_at = now()
        """), {"sid": structure_id, "st": status,
               "j": json.dumps(conditions, ensure_ascii=False)})


# ═══════════════════════════════════════════
# cMET MVP v2 — compounds / bioactivities CRUD
# ═══════════════════════════════════════════

def upsert_compound(data: dict):
    """
    compounds 테이블 upsert.
    data 키: chembl_id, pref_name, canonical_smiles, inchikey, max_phase
    """
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO compounds (chembl_id, pref_name, canonical_smiles, inchikey, max_phase)
            VALUES (:chembl_id, :pref_name, :canonical_smiles, :inchikey, :max_phase)
            ON CONFLICT (chembl_id) DO UPDATE SET
                pref_name        = EXCLUDED.pref_name,
                canonical_smiles = EXCLUDED.canonical_smiles,
                inchikey         = EXCLUDED.inchikey,
                max_phase        = EXCLUDED.max_phase
        """), {
            "chembl_id":        data.get("chembl_id"),
            "pref_name":        data.get("pref_name"),
            "canonical_smiles": data.get("canonical_smiles"),
            "inchikey":         data.get("inchikey"),
            "max_phase":        data.get("max_phase"),
        })


def upsert_bioactivity(data: dict):
    """
    bioactivities 테이블 upsert.
    data 키: chembl_id, uniprot_acc, standard_type, standard_value, standard_units,
             value_nM_normalized, pchembl_value, assay_chembl_id, assay_description,
             document_chembl_id
    """
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO bioactivities
                (chembl_id, uniprot_acc, standard_type, standard_value, standard_units,
                 value_nM_normalized, pchembl_value, assay_chembl_id, assay_description,
                 document_chembl_id)
            VALUES
                (:chembl_id, :uniprot_acc, :standard_type, :standard_value, :standard_units,
                 :value_nM_normalized, :pchembl_value, :assay_chembl_id, :assay_description,
                 :document_chembl_id)
            ON CONFLICT (chembl_id, uniprot_acc, assay_chembl_id, standard_type) DO UPDATE SET
                standard_value      = EXCLUDED.standard_value,
                standard_units      = EXCLUDED.standard_units,
                value_nM_normalized = EXCLUDED.value_nM_normalized,
                pchembl_value       = EXCLUDED.pchembl_value,
                assay_description   = EXCLUDED.assay_description,
                document_chembl_id  = EXCLUDED.document_chembl_id
        """), {
            "chembl_id":          data.get("chembl_id"),
            "uniprot_acc":        data.get("uniprot_acc"),
            "standard_type":      data.get("standard_type"),
            "standard_value":     data.get("standard_value"),
            "standard_units":     data.get("standard_units"),
            "value_nM_normalized": data.get("value_nM_normalized"),
            "pchembl_value":      data.get("pchembl_value"),
            "assay_chembl_id":    data.get("assay_chembl_id") or "",
            "assay_description":  data.get("assay_description"),
            "document_chembl_id": data.get("document_chembl_id"),
        })


def upsert_compounds_bulk(records: list[dict]):
    """compounds 다건 upsert — 단일 트랜잭션(executemany). 원격 DB 왕복 최소화."""
    if not records:
        return
    rows = [{
        "chembl_id":        r.get("chembl_id"),
        "pref_name":        r.get("pref_name"),
        "canonical_smiles": r.get("canonical_smiles"),
        "inchikey":         r.get("inchikey"),
        "max_phase":        r.get("max_phase"),
        "first_approval":   r.get("first_approval"),
        "molecule_type":    r.get("molecule_type"),
    } for r in records if r.get("chembl_id")]
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO compounds
                (chembl_id, pref_name, canonical_smiles, inchikey, max_phase,
                 first_approval, molecule_type)
            VALUES
                (:chembl_id, :pref_name, :canonical_smiles, :inchikey, :max_phase,
                 :first_approval, :molecule_type)
            ON CONFLICT (chembl_id) DO UPDATE SET
                pref_name        = COALESCE(EXCLUDED.pref_name, compounds.pref_name),
                canonical_smiles = COALESCE(EXCLUDED.canonical_smiles, compounds.canonical_smiles),
                inchikey         = COALESCE(EXCLUDED.inchikey, compounds.inchikey),
                max_phase        = COALESCE(EXCLUDED.max_phase, compounds.max_phase),
                first_approval   = COALESCE(EXCLUDED.first_approval, compounds.first_approval),
                molecule_type    = COALESCE(EXCLUDED.molecule_type, compounds.molecule_type)
        """), rows)


def upsert_bioactivities_bulk(records: list[dict]):
    """bioactivities 다건 upsert — 단일 트랜잭션(executemany)."""
    if not records:
        return
    rows = [{
        "chembl_id":           r.get("chembl_id"),
        "uniprot_acc":         r.get("uniprot_acc"),
        "standard_type":       r.get("standard_type"),
        "standard_value":      r.get("standard_value"),
        "standard_units":      r.get("standard_units"),
        "value_nM_normalized": r.get("value_nM_normalized"),
        "pchembl_value":       r.get("pchembl_value"),
        "assay_chembl_id":     r.get("assay_chembl_id") or "",
        "assay_description":   r.get("assay_description"),
        "document_chembl_id":  r.get("document_chembl_id"),
    } for r in records if r.get("chembl_id")]
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO bioactivities
                (chembl_id, uniprot_acc, standard_type, standard_value, standard_units,
                 value_nM_normalized, pchembl_value, assay_chembl_id, assay_description,
                 document_chembl_id)
            VALUES
                (:chembl_id, :uniprot_acc, :standard_type, :standard_value, :standard_units,
                 :value_nM_normalized, :pchembl_value, :assay_chembl_id, :assay_description,
                 :document_chembl_id)
            ON CONFLICT (chembl_id, uniprot_acc, assay_chembl_id, standard_type) DO UPDATE SET
                standard_value      = EXCLUDED.standard_value,
                standard_units      = EXCLUDED.standard_units,
                value_nM_normalized = EXCLUDED.value_nM_normalized,
                pchembl_value       = EXCLUDED.pchembl_value,
                assay_description   = EXCLUDED.assay_description,
                document_chembl_id  = EXCLUDED.document_chembl_id
        """), rows)


def get_all_mutations_by_uniprot(uniprot_id: str) -> list[dict]:
    """UniProt 단백질 전체 구조에 걸쳐 고유 변이 목록 반환 (mutations track 용)."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT sm.mutation, sm.position, sm.mutation_type
            FROM structure_mutations sm
            JOIN pdb_structures ps ON sm.structure_id = ps.structure_id
            WHERE ps.uniprot_id = :uid
              AND sm.position IS NOT NULL
            ORDER BY sm.position, sm.mutation
        """), {"uid": uniprot_id}).mappings().all()
    return [dict(r) for r in rows]


def get_drug_table_by_uniprot(uniprot_id: str) -> list[dict]:
    """compound_activity_summary 뷰에서 약물 테이블 데이터 반환."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT chembl_id, pref_name, max_phase,
                   ROUND(median_pchembl::numeric, 2) AS median_pchembl,
                   n_records,
                   ROUND(best_nM::numeric, 1)        AS best_nM
            FROM compound_activity_summary
            WHERE uniprot_acc = :uid
            ORDER BY median_pchembl DESC NULLS LAST
        """), {"uid": uniprot_id}).mappings().all()
    return _jsonable(rows)


def get_drug_table_with_links(uniprot_id: str) -> list[dict]:
    """약물 요약 + 결합 PDB 구조 수(n_pdb) 를 함께 반환 (Synapse식 연결용).

    compound_activity_summary 에 ligand_chembl_map↔ligands 경유 구조 카운트를 붙인다.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT s.chembl_id, s.pref_name, s.max_phase,
                   ROUND(s.median_pchembl::numeric, 2) AS median_pchembl,
                   s.n_records,
                   ROUND(s.best_nM::numeric, 1)        AS best_nm,
                   COALESCE(pc.n_pdb, 0)               AS n_pdb
            FROM compound_activity_summary s
            LEFT JOIN (
                SELECT m.chembl_id, count(DISTINCT l.structure_id) AS n_pdb
                FROM ligand_chembl_map m
                JOIN ligands l ON l.ligand_id = m.ligand_id
                JOIN pdb_structures p ON p.structure_id = l.structure_id
                WHERE m.chembl_id IS NOT NULL AND p.uniprot_id = :uid
                GROUP BY m.chembl_id
            ) pc ON pc.chembl_id = s.chembl_id
            WHERE s.uniprot_acc = :uid
            ORDER BY s.median_pchembl DESC NULLS LAST
        """), {"uid": uniprot_id}).mappings().all()
    return _jsonable(rows)


def get_clinical_drugs_by_uniprot(uniprot_id: str, drugs_only: bool = True) -> list[dict]:
    """이 단백질의 약물 테이블 — 임상단계 + 결합 PDB 중심 (IC50/활성 제외).

    drugs_only=True: 임상단계(max_phase) 있거나 결합 PDB 있는 '진짜 약물'만.
    drugs_only=False: MET 활성 화합물 전체 (연구화합물 포함).
    각 행: chembl_id, drug_name(무명은 ChEMBL ID), max_phase, first_approval,
           molecule_type, n_pdb, top_indication.
    """
    where = "WHERE (c.max_phase IS NOT NULL OR pc.n_pdb > 0)" if drugs_only else "WHERE TRUE"
    with get_engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT c.chembl_id,
                   COALESCE(NULLIF(c.pref_name, ''), c.chembl_id) AS drug_name,
                   (c.pref_name IS NOT NULL AND c.pref_name <> '') AS is_named,
                   c.max_phase, c.first_approval, c.molecule_type,
                   COALESCE(pc.n_pdb, 0) AS n_pdb,
                   ind.top_indication
            FROM compounds c
            JOIN (SELECT DISTINCT chembl_id FROM bioactivities WHERE uniprot_acc = :uid) b
                 ON b.chembl_id = c.chembl_id
            LEFT JOIN (
                SELECT m.chembl_id, count(DISTINCT l.structure_id) AS n_pdb
                FROM ligand_chembl_map m
                JOIN ligands l ON l.ligand_id = m.ligand_id
                JOIN pdb_structures p ON p.structure_id = l.structure_id
                WHERE m.chembl_id IS NOT NULL AND p.uniprot_id = :uid
                GROUP BY m.chembl_id
            ) pc ON pc.chembl_id = c.chembl_id
            LEFT JOIN (
                SELECT DISTINCT ON (chembl_id) chembl_id,
                       mesh_heading AS top_indication
                FROM drug_indications
                ORDER BY chembl_id, max_phase_for_ind DESC NULLS LAST
            ) ind ON ind.chembl_id = c.chembl_id
            {where}
            ORDER BY c.max_phase DESC NULLS LAST, pc.n_pdb DESC NULLS LAST, c.pref_name NULLS LAST
        """), {"uid": uniprot_id}).mappings().all()
    return _jsonable(rows)


def get_drug_indications(chembl_id: str) -> list[dict]:
    """약물의 질환 indication 목록 (최고 단계순)."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT mesh_heading, max_phase_for_ind
            FROM drug_indications WHERE chembl_id = :cid
            ORDER BY max_phase_for_ind DESC NULLS LAST, mesh_heading
        """), {"cid": chembl_id}).mappings().all()
    return _jsonable(rows)


def upsert_drug_indications_bulk(records: list[dict]):
    """drug_indications 다건 upsert (단일 트랜잭션)."""
    rows = [{
        "chembl_id": r.get("chembl_id"),
        "mesh_heading": r.get("mesh_heading"),
        "max_phase_for_ind": r.get("max_phase_for_ind"),
        "efo_term": r.get("efo_term"),
    } for r in records if r.get("chembl_id") and r.get("mesh_heading")]
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO drug_indications (chembl_id, mesh_heading, max_phase_for_ind, efo_term)
            VALUES (:chembl_id, :mesh_heading, :max_phase_for_ind, :efo_term)
            ON CONFLICT (chembl_id, mesh_heading) DO UPDATE SET
                max_phase_for_ind = EXCLUDED.max_phase_for_ind,
                efo_term = EXCLUDED.efo_term
        """), rows)


def get_structures_for_drug(chembl_id: str, uniprot_id: str) -> list[dict]:
    """이 약물(ChEMBL)의 리간드가 결합한 이 단백질의 PDB 구조 목록."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT l.structure_id, m.ligand_id, ps.resolution, ps.method
            FROM ligand_chembl_map m
            JOIN ligands l ON l.ligand_id = m.ligand_id
            JOIN pdb_structures ps ON ps.structure_id = l.structure_id
            WHERE m.chembl_id = :cid AND ps.uniprot_id = :uid
            ORDER BY l.structure_id
        """), {"cid": chembl_id, "uid": uniprot_id}).mappings().all()
    return _jsonable(rows)


def get_drugs_for_structure(structure_id: str) -> list[dict]:
    """이 PDB 구조의 결합 리간드 중 ChEMBL 약물로 매핑된 것 + 활성·임상단계."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT m.ligand_id, m.chembl_id, c.pref_name, c.max_phase,
                   ROUND(s.median_pchembl::numeric, 2) AS median_pchembl
            FROM ligands l
            JOIN ligand_chembl_map m ON m.ligand_id = l.ligand_id
            JOIN compounds c ON c.chembl_id = m.chembl_id
            LEFT JOIN compound_activity_summary s
                   ON s.chembl_id = m.chembl_id
            WHERE l.structure_id = :sid AND m.chembl_id IS NOT NULL
            ORDER BY c.max_phase DESC NULLS LAST
        """), {"sid": structure_id}).mappings().all()
    return _jsonable(rows)


def get_drug_detail(chembl_id: str, uniprot_id: str) -> dict:
    """약물 1개의 상세 — 화합물·임상 메타 + 질환 indications (활성/IC50 제외)."""
    with get_engine().connect() as conn:
        comp = conn.execute(text(
            "SELECT chembl_id, pref_name, max_phase, first_approval, molecule_type "
            "FROM compounds WHERE chembl_id = :cid"
        ), {"cid": chembl_id}).mappings().first()
        inds = conn.execute(text("""
            SELECT mesh_heading, max_phase_for_ind
            FROM drug_indications WHERE chembl_id = :cid
            ORDER BY max_phase_for_ind DESC NULLS LAST, mesh_heading
        """), {"cid": chembl_id}).mappings().all()
    return {
        "compound": _jsonable([comp])[0] if comp else {},
        "indications": _jsonable(inds),
    }


def get_papers_unified(uniprot_id: str) -> list[dict]:
    """이 단백질의 논문을 한 목록으로 통합하되, **같은 DOI 는 한 항목으로 묶는다**.

    같은 논문(DOI)에서 나온 여러 PDB 는 한 줄로 (structure_ids 배열). 구조화·PDF 는
    형제 중 하나라도 있으면 반영. DOI 없는 건 structure_id 단위로.
    각 항목: key, title, doi, doi_url, structure_ids(list), primary_sid,
             has_structured, has_pdf, source, n_pdb.
    """
    with get_engine().connect() as conn:
        prows = conn.execute(text("""
            SELECT p.structure_id, p.title, ps.doi AS doi, p.authors
            FROM papers p JOIN pdb_structures ps ON p.structure_id = ps.structure_id
            WHERE ps.uniprot_id = :uid
        """), {"uid": uniprot_id}).mappings().all()
        arows = conn.execute(text("""
            SELECT pa.structure_id, pa.pdf_name, ps.doi AS doi,
                   (pa.structured IS NOT NULL) AS has_structured,
                   (pa.pdf_bytes IS NOT NULL)  AS has_pdf
            FROM paper_analysis pa JOIN pdb_structures ps ON pa.structure_id = ps.structure_id
            WHERE ps.uniprot_id = :uid
        """), {"uid": uniprot_id}).mappings().all()

    # DOI 있으면 doi 키, 없으면 structure_id 키로 그룹핑
    groups: dict[str, dict] = {}

    def _key(doi: str, sid: str) -> str:
        doi = (doi or "").strip()
        return "doi:" + doi.lower() if doi else "sid:" + sid

    def _ensure(key, doi, sid, title, source):
        if key not in groups:
            groups[key] = {
                "key": key, "title": title or "(제목 없음)",
                "doi": (doi or "").strip(), "authors": "",
                "structure_ids": [], "has_structured": False,
                "has_pdf": False, "source": source,
            }
        g = groups[key]
        if sid not in g["structure_ids"]:
            g["structure_ids"].append(sid)
        return g

    for p in prows:
        g = _ensure(_key(p["doi"], p["structure_id"]), p["doi"], p["structure_id"],
                    p["title"], "full")
        if p["authors"]:
            g["authors"] = p["authors"]
        if (p["title"] or "").strip() and g["title"] in ("(제목 없음)", ""):
            g["title"] = p["title"]
    for a in arows:
        title = a["pdf_name"] or "(파일명 없음)"
        g = _ensure(_key(a["doi"], a["structure_id"]), a["doi"], a["structure_id"],
                    title, "pdb")
        g["has_structured"] = g["has_structured"] or bool(a["has_structured"])
        g["has_pdf"] = g["has_pdf"] or bool(a["has_pdf"])
        if g["title"] in ("(제목 없음)", "") and title != "(파일명 없음)":
            g["title"] = title

    # DOI 그룹은 논문분석 유무와 무관하게 같은 DOI 의 모든 PDB 를 포함시킨다
    # (같은 논문 = 그 논문의 모든 구조. 한 곳에 업로드하면 전부 공유됨).
    with get_engine().connect() as conn:
        dois = [g["doi"] for g in groups.values() if g["doi"]]
        doi_map: dict[str, list[str]] = {}
        if dois:
            q = text("SELECT doi, structure_id FROM pdb_structures "
                     "WHERE uniprot_id = :uid AND doi IN :dois"
                     ).bindparams(bindparam("dois", expanding=True))
            for r in conn.execute(q, {"uid": uniprot_id, "dois": dois}).all():
                doi_map.setdefault((r[0] or "").lower(), []).append(r[1])

    out = list(groups.values())
    for g in out:
        if g["doi"] and g["doi"].lower() in doi_map:
            allsids = sorted(set(g["structure_ids"]) | set(doi_map[g["doi"].lower()]))
            g["structure_ids"] = allsids
        else:
            g["structure_ids"].sort()
        g["primary_sid"] = g["structure_ids"][0] if g["structure_ids"] else ""
        g["n_pdb"] = len(g["structure_ids"])
        g["doi_url"] = ("https://doi.org/" + g["doi"]) if g["doi"] else ""
    out.sort(key=lambda x: (not x["has_structured"], not x["has_pdf"], x["primary_sid"]))
    return out


def get_paper_by_structure(structure_id: str) -> dict | None:
    """papers 테이블에서 이 PDB(또는 같은 DOI 형제)의 전체 분석 논문 1건 (제목·분석본문)."""
    with get_engine().connect() as conn:
        sids = _doi_group(conn, structure_id)
        q = text("""
            SELECT paper_id, title, doi, authors, analysis_md
            FROM papers WHERE structure_id IN :sids
            ORDER BY (structure_id = :self) DESC, created_at DESC LIMIT 1
        """).bindparams(bindparam("sids", expanding=True))
        row = conn.execute(q, {"sids": sids, "self": structure_id}).mappings().first()
    return dict(row) if row else None


def upsert_ligand_inchikeys(records: list[dict]):
    """ligands.inchikey 다건 갱신 (ligand_id 기준, 해당 구조의 리간드에 채움)."""
    rows = [{"ligand_id": r.get("ligand_id"), "inchikey": r.get("inchikey")}
            for r in records if r.get("ligand_id") and r.get("inchikey")]
    if not rows:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            "UPDATE ligands SET inchikey = :inchikey WHERE ligand_id = :ligand_id"
        ), rows)


def match_ligands_to_compounds_by_inchikey(uniprot_id: str) -> int:
    """리간드 InChIKey ↔ compounds InChIKey 구조 동일성 매칭 → ligand_chembl_map 보강.

    UniChem(CCD) 이 못 잡은 것, 그리고 임상 약물이 아닌 ChEMBL 화합물도 구조가 같으면 연동.
    exact InChIKey 우선, 없으면 connectivity block(앞 14자) 매칭.
    Returns 신규/갱신 매핑 수.
    """
    with get_engine().begin() as conn:
        res = conn.execute(text("""
            INSERT INTO ligand_chembl_map (ligand_id, chembl_id, source, checked_at)
            SELECT DISTINCT ON (l.ligand_id) l.ligand_id, c.chembl_id, 'inchikey', now()
            FROM ligands l
            JOIN pdb_structures p ON p.structure_id = l.structure_id
            JOIN compounds c
              ON c.inchikey = l.inchikey
              OR split_part(c.inchikey, '-', 1) = split_part(l.inchikey, '-', 1)
            WHERE p.uniprot_id = :uid
              AND l.inchikey IS NOT NULL
            ORDER BY l.ligand_id,
                     (c.inchikey = l.inchikey) DESC,   -- exact 우선
                     c.max_phase DESC NULLS LAST
            ON CONFLICT (ligand_id) DO UPDATE SET
                chembl_id  = COALESCE(ligand_chembl_map.chembl_id, EXCLUDED.chembl_id),
                source     = CASE WHEN ligand_chembl_map.chembl_id IS NULL
                                  THEN EXCLUDED.source ELSE ligand_chembl_map.source END,
                checked_at = now()
        """), {"uid": uniprot_id})
        return res.rowcount or 0


def get_papers_by_uniprot(uniprot_id: str) -> list[dict]:
    """이 단백질의 PDB 구조에 연결된 논문 + paper_analysis 요약을 통합 반환.

    papers 테이블(전체 분석)과 paper_analysis(PDB별 구조화 분석)를 함께 본다.
    """
    with get_engine().connect() as conn:
        # papers: structure_id 가 이 단백질의 구조인 것
        rows = conn.execute(text("""
            SELECT p.paper_id, p.title, p.doi, p.authors, p.structure_id,
                   p.analyzed_at, p.model
            FROM papers p
            JOIN pdb_structures ps ON p.structure_id = ps.structure_id
            WHERE ps.uniprot_id = :uid
            ORDER BY p.created_at DESC
        """), {"uid": uniprot_id}).mappings().all()
        papers = [dict(r) for r in rows]

        # paper_analysis: 이 단백질 구조 중 구조화 분석(structured)이 있는 것
        arows = conn.execute(text("""
            SELECT pa.structure_id, pa.pdf_name, pa.status, pa.analyzed_at,
                   (pa.structured IS NOT NULL) AS has_structured
            FROM paper_analysis pa
            JOIN pdb_structures ps ON pa.structure_id = ps.structure_id
            WHERE ps.uniprot_id = :uid AND pa.pdf_name IS NOT NULL
            ORDER BY pa.analyzed_at DESC NULLS LAST
        """), {"uid": uniprot_id}).mappings().all()
        analyses = [dict(r) for r in arows]
    return papers, analyses


# ─────────────────────────────────────────────
# 직접 실행 시 스키마 적용 + 테이블 확인
# 터미널: python database.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_database()
    with get_engine().connect() as conn:
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )).all()
    print("   확인된 테이블:", [t[0] for t in tables])
