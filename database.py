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

from sqlalchemy import text, bindparam

from db_config import get_engine


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
