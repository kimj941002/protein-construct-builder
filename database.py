# database.py
# 정규화된 8-테이블 SQLite 스키마 및 CRUD 함수
# 스키마 버전 2 (2026-03-01)
#
# 테이블 구조:
#   1. proteins              — UniProt 단백질 기본 정보 (sequence_path로 파일 참조)
#   2. protein_domains       — 도메인 정보 (proteins.domains JSON 분리)
#   3. pdb_structures        — PDB 구조 정보
#   4. structure_mutations   — Mutation 목록 (pdb_structures.mutations JSON 분리)
#   5. ligands               — 리간드(소분자)
#   6. partner_proteins      — 파트너 단백질
#   7. partner_protein_chains— 파트너 체인 목록 (partner_proteins.partner_chains JSON 분리)
#   8. ptm_oligosaccharides  — 올리고당류 / PTM

import sqlite3
import json
import os
from config import DB_PATH, SEQUENCES_DIR


def get_connection():
    """데이터베이스 연결을 반환합니다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """
    8개 테이블을 생성합니다. 이미 존재하는 테이블은 건드리지 않습니다.
    실행 후 migrate_database()로 기존 데이터를 정규화합니다.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ── 1. proteins ───────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proteins (
            uniprot_id           TEXT PRIMARY KEY,
            gene_name            TEXT,
            protein_name         TEXT,
            organism             TEXT,
            sequence_path        TEXT,
            sequence_length      INTEGER,
            function_desc        TEXT,
            subcellular_location TEXT,
            signal_peptide       TEXT,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── 2. protein_domains ────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS protein_domains (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            uniprot_id TEXT NOT NULL,
            name       TEXT,
            start_pos  INTEGER,
            end_pos    INTEGER,
            FOREIGN KEY (uniprot_id) REFERENCES proteins(uniprot_id)
        )
    """)

    # ── 3. pdb_structures ─────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdb_structures (
            structure_id      TEXT PRIMARY KEY,
            uniprot_id        TEXT NOT NULL,
            source            TEXT,
            method            TEXT,
            resolution        REAL,
            mean_plddt        REAL,
            chain_id          TEXT,
            residue_range     TEXT,
            expression_system TEXT,
            host_cell_line    TEXT,
            crystal_method    TEXT,
            crystal_ph        REAL,
            crystal_temp      REAL,
            crystal_details   TEXT,
            space_group       TEXT,
            complex_type      TEXT,
            doi               TEXT,
            deposition_date   TEXT,
            FOREIGN KEY (uniprot_id) REFERENCES proteins(uniprot_id)
        )
    """)

    # ── 4. structure_mutations ────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS structure_mutations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id  TEXT NOT NULL,
            mutation      TEXT,
            position      INTEGER,
            mutation_type TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 5. ligands ────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ligands (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id TEXT NOT NULL,
            ligand_id    TEXT,
            ligand_name  TEXT,
            formula      TEXT,
            smiles       TEXT,
            ligand_type  TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 6. partner_proteins ───────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partner_proteins (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id              TEXT NOT NULL,
            entity_id                 TEXT,
            partner_uniprot_id        TEXT,
            partner_gene_name         TEXT,
            partner_chain_id          TEXT,
            sequence_length           INTEGER,
            organism                  TEXT,
            partner_residue_range     TEXT,
            partner_expression_system TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 7. partner_protein_chains ─────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partner_protein_chains (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL,
            chain_id   TEXT,
            FOREIGN KEY (partner_id) REFERENCES partner_proteins(id)
        )
    """)

    # ── 8. ptm_oligosaccharides ───────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ptm_oligosaccharides (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id    TEXT NOT NULL,
            entity_id       TEXT,
            name            TEXT,
            chain_id        TEXT,
            linked_chain    TEXT,
            linked_position INTEGER,
            linked_residue  TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 9. klifs_structures ───────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS klifs_structures (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id     TEXT NOT NULL UNIQUE,
            klifs_id         INTEGER,
            kinase_id        INTEGER,
            kinase_name      TEXT,
            family           TEXT,
            dfg              TEXT,
            ac_helix         TEXT,
            qualityscore     REAL,
            missing_residues INTEGER,
            missing_atoms    INTEGER,
            rmsd1            REAL,
            rmsd2            REAL,
            gatekeeper       TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 10. paper_analysis ────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_analysis (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id TEXT NOT NULL UNIQUE,
            pdf_path     TEXT,
            status       TEXT DEFAULT 'none',
            raw_text     TEXT,
            analyzed_at  TIMESTAMP,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    conn.commit()
    conn.close()
    print("[OK] 데이터베이스 초기화 완료:", DB_PATH)

    migrate_database()


def migrate_database():
    """
    기존 DB를 새 정규화 스키마로 마이그레이션합니다.
    - 누락 컬럼/테이블 추가
    - JSON 컬럼 → 정규화 테이블로 데이터 이전
    - sequence TEXT → sequences/ 폴더의 FASTA 파일로 이전
    안전하게 반복 실행 가능합니다 (이미 처리된 데이터는 건너뜀).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ── 기존 테이블 목록 확인 ──────────────────────────────────
    existing_tables = {
        row[0] for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    # ── proteins 컬럼 마이그레이션 ────────────────────────────
    if "proteins" in existing_tables:
        proteins_cols = {row[1] for row in cursor.execute("PRAGMA table_info(proteins)")}

        for col, coltype in {
            "sequence_path":        "TEXT",
            "protein_name":         "TEXT",
            "function_desc":        "TEXT",
            "subcellular_location": "TEXT",
            "signal_peptide":       "TEXT",
        }.items():
            if col not in proteins_cols:
                cursor.execute(f"ALTER TABLE proteins ADD COLUMN {col} {coltype}")
                print(f"[OK] proteins 컬럼 추가: {col}")

    # ── 새 테이블 생성 (없는 경우) ───────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS protein_domains (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            uniprot_id TEXT NOT NULL,
            name       TEXT,
            start_pos  INTEGER,
            end_pos    INTEGER,
            FOREIGN KEY (uniprot_id) REFERENCES proteins(uniprot_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS structure_mutations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id  TEXT NOT NULL,
            mutation      TEXT,
            position      INTEGER,
            mutation_type TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partner_protein_chains (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL,
            chain_id   TEXT,
            FOREIGN KEY (partner_id) REFERENCES partner_proteins(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS klifs_structures (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id     TEXT NOT NULL UNIQUE,
            klifs_id         INTEGER,
            kinase_id        INTEGER,
            kinase_name      TEXT,
            family           TEXT,
            dfg              TEXT,
            ac_helix         TEXT,
            qualityscore     REAL,
            missing_residues INTEGER,
            missing_atoms    INTEGER,
            rmsd1            REAL,
            rmsd2            REAL,
            gatekeeper       TEXT,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_analysis (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            structure_id TEXT NOT NULL UNIQUE,
            pdf_path     TEXT,
            status       TEXT DEFAULT 'none',
            raw_text     TEXT,
            analyzed_at  TIMESTAMP,
            FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
        )
    """)

    # ── 기존 paper_analysis 테이블에 raw_text 컬럼 추가 (구버전 DB 마이그레이션) ──
    if "paper_analysis" in existing_tables:
        pa_cols = {row[1] for row in cursor.execute("PRAGMA table_info(paper_analysis)")}
        if "raw_text" not in pa_cols:
            cursor.execute("ALTER TABLE paper_analysis ADD COLUMN raw_text TEXT")
            print("[OK] paper_analysis 컬럼 추가: raw_text")

    # ── ptm_oligosaccharides: chains → chain_id ───────────────
    if "ptm_oligosaccharides" in existing_tables:
        oligo_cols = {row[1] for row in cursor.execute("PRAGMA table_info(ptm_oligosaccharides)")}
        if "chain_id" not in oligo_cols:
            cursor.execute("ALTER TABLE ptm_oligosaccharides ADD COLUMN chain_id TEXT")
            cursor.execute("""
                UPDATE ptm_oligosaccharides
                SET chain_id = chains
                WHERE chain_id IS NULL AND chains IS NOT NULL
            """)
            print("[OK] ptm_oligosaccharides.chains → chain_id 마이그레이션 완료")

    # ── partner_proteins 누락 컬럼 추가 ──────────────────────
    if "partner_proteins" in existing_tables:
        pp_cols = {row[1] for row in cursor.execute("PRAGMA table_info(partner_proteins)")}
        for col, coltype in {
            "entity_id":                 "TEXT",
            "sequence_length":           "INTEGER",
            "organism":                  "TEXT",
            "partner_residue_range":     "TEXT",
            "partner_expression_system": "TEXT",
        }.items():
            if col not in pp_cols:
                cursor.execute(f"ALTER TABLE partner_proteins ADD COLUMN {col} {coltype}")
                print(f"[OK] partner_proteins 컬럼 추가: {col}")

    # ── klifs_structures: UNIQUE(structure_id) 제약 추가 ─────
    if "klifs_structures" in existing_tables:
        klifs_indices = cursor.execute("PRAGMA index_list(klifs_structures)").fetchall()
        klifs_has_unique = any(row[2] == 1 for row in klifs_indices)
        if not klifs_has_unique:
            cursor.execute("""
                CREATE TABLE klifs_structures_new (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    structure_id     TEXT NOT NULL UNIQUE,
                    klifs_id         INTEGER,
                    kinase_id        INTEGER,
                    kinase_name      TEXT,
                    family           TEXT,
                    dfg              TEXT,
                    ac_helix         TEXT,
                    qualityscore     REAL,
                    missing_residues INTEGER,
                    missing_atoms    INTEGER,
                    rmsd1            REAL,
                    rmsd2            REAL,
                    gatekeeper       TEXT,
                    FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
                )
            """)
            cursor.execute("""
                INSERT OR IGNORE INTO klifs_structures_new
                SELECT * FROM klifs_structures
            """)
            cursor.execute("DROP TABLE klifs_structures")
            cursor.execute("ALTER TABLE klifs_structures_new RENAME TO klifs_structures")
            print("[OK] klifs_structures: UNIQUE(structure_id) 마이그레이션 완료")

    conn.commit()
    conn.close()

    # ── JSON 데이터 → 정규화 테이블 이전 ─────────────────────
    _migrate_json_to_tables()
    # ── sequence TEXT → FASTA 파일 이전 ─────────────────────
    _migrate_sequences_to_files()


def _migrate_json_to_tables():
    """
    기존 JSON 컬럼 데이터를 정규화 테이블로 이전합니다.
    이미 이전된 행은 건너뜁니다.
    """
    conn = get_connection()
    cursor = conn.cursor()
    tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # ── pdb_structures.mutations → structure_mutations ────────
    if "pdb_structures" in tables:
        pdb_cols = {row[1] for row in cursor.execute("PRAGMA table_info(pdb_structures)")}
        if "mutations" in pdb_cols:
            cursor.execute("""
                SELECT structure_id, mutations FROM pdb_structures
                WHERE mutations IS NOT NULL AND mutations != '[]'
            """)
            for sid, muts_json in cursor.fetchall():
                cursor.execute(
                    "SELECT COUNT(*) FROM structure_mutations WHERE structure_id = ?", (sid,)
                )
                if cursor.fetchone()[0] > 0:
                    continue
                try:
                    for m in json.loads(muts_json):
                        cursor.execute("""
                            INSERT INTO structure_mutations
                                (structure_id, mutation, position, mutation_type)
                            VALUES (?, ?, ?, ?)
                        """, (sid, m.get("mutation"), m.get("position"), m.get("type")))
                except Exception:
                    pass

    # ── proteins.domains → protein_domains ───────────────────
    if "proteins" in tables:
        p_cols = {row[1] for row in cursor.execute("PRAGMA table_info(proteins)")}
        if "domains" in p_cols:
            cursor.execute("""
                SELECT uniprot_id, domains FROM proteins
                WHERE domains IS NOT NULL AND domains != '[]'
            """)
            for uid, dom_json in cursor.fetchall():
                cursor.execute(
                    "SELECT COUNT(*) FROM protein_domains WHERE uniprot_id = ?", (uid,)
                )
                if cursor.fetchone()[0] > 0:
                    continue
                try:
                    for d in json.loads(dom_json):
                        cursor.execute("""
                            INSERT INTO protein_domains (uniprot_id, name, start_pos, end_pos)
                            VALUES (?, ?, ?, ?)
                        """, (uid, d.get("name"), d.get("start"), d.get("end")))
                except Exception:
                    pass

    # ── partner_proteins.partner_chains → partner_protein_chains
    if "partner_proteins" in tables:
        pp_cols = {row[1] for row in cursor.execute("PRAGMA table_info(partner_proteins)")}
        if "partner_chains" in pp_cols:
            cursor.execute("""
                SELECT id, partner_chains FROM partner_proteins
                WHERE partner_chains IS NOT NULL AND partner_chains != '[]'
            """)
            for pid, chains_json in cursor.fetchall():
                cursor.execute(
                    "SELECT COUNT(*) FROM partner_protein_chains WHERE partner_id = ?", (pid,)
                )
                if cursor.fetchone()[0] > 0:
                    continue
                try:
                    for chain in json.loads(chains_json):
                        cursor.execute("""
                            INSERT INTO partner_protein_chains (partner_id, chain_id)
                            VALUES (?, ?)
                        """, (pid, chain))
                except Exception:
                    pass

    conn.commit()
    conn.close()
    print("[OK] JSON → 정규화 테이블 마이그레이션 완료")


def _migrate_sequences_to_files():
    """
    기존 proteins.sequence TEXT 데이터를 sequences/ 폴더의 FASTA 파일로 이전하고
    sequence_path 컬럼을 업데이트합니다.
    """
    conn = get_connection()
    cursor = conn.cursor()

    tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "proteins" not in tables:
        conn.close()
        return

    p_cols = {row[1] for row in cursor.execute("PRAGMA table_info(proteins)")}
    if "sequence" not in p_cols:
        conn.close()
        return

    cursor.execute("""
        SELECT uniprot_id, gene_name, organism, sequence, sequence_path
        FROM proteins
        WHERE sequence IS NOT NULL AND sequence != ''
          AND (sequence_path IS NULL OR sequence_path = '')
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    os.makedirs(SEQUENCES_DIR, exist_ok=True)
    migrated = 0

    for uid, gene, organism, sequence, _ in rows:
        filename = f"{uid}.txt"
        filepath = os.path.join(SEQUENCES_DIR, filename)
        rel_path = f"sequences/{filename}"

        header = f">{uid} | {gene or ''} | {organism or ''}"
        fasta_lines = [header]
        for i in range(0, len(sequence), 60):
            fasta_lines.append(sequence[i:i + 60])

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(fasta_lines) + "\n")

        conn2 = get_connection()
        conn2.execute(
            "UPDATE proteins SET sequence_path = ? WHERE uniprot_id = ?",
            (rel_path, uid)
        )
        conn2.commit()
        conn2.close()
        migrated += 1

    print(f"[OK] sequence → FASTA 파일 마이그레이션 완료: {migrated}개")


# ═══════════════════════════════════════════
# 1. proteins CRUD
# ═══════════════════════════════════════════

def insert_protein(data: dict):
    """
    proteins 테이블에 삽입합니다. 같은 uniprot_id면 덮어씁니다.
    data 키: uniprot_id, gene_name, protein_name, organism,
             sequence_path, sequence_length, function_desc,
             subcellular_location, signal_peptide
    """
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO proteins
            (uniprot_id, gene_name, protein_name, organism,
             sequence_path, sequence_length, function_desc,
             subcellular_location, signal_peptide)
        VALUES
            (:uniprot_id, :gene_name, :protein_name, :organism,
             :sequence_path, :sequence_length, :function_desc,
             :subcellular_location, :signal_peptide)
    """, data)
    conn.commit()
    conn.close()


def get_protein(uniprot_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM proteins WHERE uniprot_id = ?", (uniprot_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_proteins() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM proteins").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_protein(uniprot_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM proteins WHERE uniprot_id = ?", (uniprot_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 2. protein_domains CRUD
# ═══════════════════════════════════════════

def insert_domain(data: dict):
    """data 키: uniprot_id, name, start_pos, end_pos"""
    conn = get_connection()
    conn.execute("""
        INSERT INTO protein_domains (uniprot_id, name, start_pos, end_pos)
        VALUES (:uniprot_id, :name, :start_pos, :end_pos)
    """, data)
    conn.commit()
    conn.close()


def insert_domains_bulk(uniprot_id: str, domains: list[dict]):
    """domains: [{"name": str, "start": int, "end": int}, ...]"""
    if not domains:
        return
    conn = get_connection()
    conn.executemany("""
        INSERT INTO protein_domains (uniprot_id, name, start_pos, end_pos)
        VALUES (?, ?, ?, ?)
    """, [(uniprot_id, d.get("name"), d.get("start"), d.get("end")) for d in domains])
    conn.commit()
    conn.close()


def get_domains_by_uniprot(uniprot_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM protein_domains WHERE uniprot_id = ?", (uniprot_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_domains_by_uniprot(uniprot_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM protein_domains WHERE uniprot_id = ?", (uniprot_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 3. pdb_structures CRUD
# ═══════════════════════════════════════════

def insert_structure(data: dict):
    """mutations 컬럼 없음 — structure_mutations 테이블 사용."""
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO pdb_structures
            (structure_id, uniprot_id, source, method, resolution, mean_plddt,
             chain_id, residue_range, expression_system, host_cell_line,
             crystal_method, crystal_ph, crystal_temp, crystal_details, space_group,
             complex_type, doi, deposition_date)
        VALUES
            (:structure_id, :uniprot_id, :source, :method, :resolution, :mean_plddt,
             :chain_id, :residue_range, :expression_system, :host_cell_line,
             :crystal_method, :crystal_ph, :crystal_temp, :crystal_details, :space_group,
             :complex_type, :doi, :deposition_date)
    """, data)
    conn.commit()
    conn.close()


def get_structures_by_uniprot(uniprot_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM pdb_structures WHERE uniprot_id = ?", (uniprot_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_structure(structure_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM pdb_structures WHERE structure_id = ?", (structure_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_structure(structure_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM pdb_structures WHERE structure_id = ?", (structure_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 4. structure_mutations CRUD
# ═══════════════════════════════════════════

def insert_mutations_bulk(structure_id: str, mutations: list[dict]):
    """
    mutations: [{"mutation": "K1110A", "position": 1110, "type": "engineered"}, ...]
    기존 데이터는 먼저 삭제 후 삽입합니다.
    """
    conn = get_connection()
    conn.execute(
        "DELETE FROM structure_mutations WHERE structure_id = ?", (structure_id,)
    )
    if mutations:
        conn.executemany("""
            INSERT INTO structure_mutations (structure_id, mutation, position, mutation_type)
            VALUES (?, ?, ?, ?)
        """, [
            (structure_id, m.get("mutation"), m.get("position"), m.get("type"))
            for m in mutations
        ])
    conn.commit()
    conn.close()


def get_mutations_by_structure(structure_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM structure_mutations WHERE structure_id = ?", (structure_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_mutations_by_structure(structure_id: str):
    conn = get_connection()
    conn.execute(
        "DELETE FROM structure_mutations WHERE structure_id = ?", (structure_id,)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 5. ligands CRUD
# ═══════════════════════════════════════════

def insert_ligand(data: dict):
    """data 키: structure_id, ligand_id, ligand_name, formula, smiles, ligand_type"""
    conn = get_connection()
    conn.execute("""
        INSERT INTO ligands
            (structure_id, ligand_id, ligand_name, formula, smiles, ligand_type)
        VALUES
            (:structure_id, :ligand_id, :ligand_name, :formula, :smiles, :ligand_type)
    """, data)
    conn.commit()
    conn.close()


def get_ligands_by_structure(structure_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ligands WHERE structure_id = ?", (structure_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_ligands_by_structure(structure_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM ligands WHERE structure_id = ?", (structure_id,))
    conn.commit()
    conn.close()


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
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO partner_proteins
            (structure_id, entity_id, partner_uniprot_id, partner_gene_name,
             partner_chain_id, sequence_length, organism,
             partner_residue_range, partner_expression_system)
        VALUES
            (:structure_id, :entity_id, :partner_uniprot_id, :partner_gene_name,
             :partner_chain_id, :sequence_length, :organism,
             :partner_residue_range, :partner_expression_system)
    """, data)
    last_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return last_id


def get_partners_by_structure(structure_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM partner_proteins WHERE structure_id = ?", (structure_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_partners_by_structure(structure_id: str):
    conn = get_connection()
    # partner_protein_chains 먼저 삭제 (FK 참조)
    partner_ids = [
        row[0] for row in conn.execute(
            "SELECT id FROM partner_proteins WHERE structure_id = ?", (structure_id,)
        ).fetchall()
    ]
    if partner_ids:
        placeholders = ",".join("?" * len(partner_ids))
        conn.execute(
            f"DELETE FROM partner_protein_chains WHERE partner_id IN ({placeholders})",
            partner_ids
        )
    conn.execute("DELETE FROM partner_proteins WHERE structure_id = ?", (structure_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 7. partner_protein_chains CRUD
# ═══════════════════════════════════════════

def insert_partner_chains_bulk(partner_id: int, chains: list[str]):
    """chains: ["A", "B", ...] 형태의 체인 ID 목록"""
    if not chains:
        return
    conn = get_connection()
    conn.executemany(
        "INSERT INTO partner_protein_chains (partner_id, chain_id) VALUES (?, ?)",
        [(partner_id, c) for c in chains]
    )
    conn.commit()
    conn.close()


def get_chains_by_partner(partner_id: int) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT chain_id FROM partner_protein_chains WHERE partner_id = ?", (partner_id,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_all_chains_by_structure(structure_id: str) -> dict[int, list[str]]:
    """
    structure_id에 속한 모든 partner의 chain 목록을 한 번에 반환합니다.
    Returns: {partner_id: [chain_id, ...], ...}
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT ppc.partner_id, ppc.chain_id
        FROM partner_protein_chains ppc
        JOIN partner_proteins pp ON ppc.partner_id = pp.id
        WHERE pp.structure_id = ?
    """, (structure_id,)).fetchall()
    conn.close()
    result: dict[int, list[str]] = {}
    for pid, cid in rows:
        result.setdefault(pid, []).append(cid)
    return result


# ═══════════════════════════════════════════
# 8. ptm_oligosaccharides CRUD
# ═══════════════════════════════════════════

def insert_oligosaccharide(data: dict):
    """data 키: structure_id, entity_id, name, chain_id, linked_chain, linked_position, linked_residue"""
    conn = get_connection()
    conn.execute("""
        INSERT INTO ptm_oligosaccharides
            (structure_id, entity_id, name, chain_id,
             linked_chain, linked_position, linked_residue)
        VALUES
            (:structure_id, :entity_id, :name, :chain_id,
             :linked_chain, :linked_position, :linked_residue)
    """, data)
    conn.commit()
    conn.close()


def get_oligosaccharides_by_structure(structure_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ptm_oligosaccharides WHERE structure_id = ?", (structure_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_oligosaccharides_by_structure(structure_id: str):
    conn = get_connection()
    conn.execute(
        "DELETE FROM ptm_oligosaccharides WHERE structure_id = ?", (structure_id,)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════
# 9. klifs_structures CRUD
# ═══════════════════════════════════════════

def insert_klifs_structure(data: dict):
    """
    data 키: structure_id, dfg, ac_helix
    DFG 형태와 αC Helix 형태만 저장합니다.
    이미 존재하는 경우 NULL이 아닌 값만 덮어씁니다 (기존 데이터 보호).
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO klifs_structures (structure_id, dfg, ac_helix)
        VALUES (:structure_id, :dfg, :ac_helix)
        ON CONFLICT(structure_id) DO UPDATE SET
            dfg      = COALESCE(excluded.dfg,      klifs_structures.dfg),
            ac_helix = COALESCE(excluded.ac_helix, klifs_structures.ac_helix)
    """, data)
    conn.commit()
    conn.close()


def get_klifs_by_structure(structure_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM klifs_structures WHERE structure_id = ?", (structure_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_klifs_bulk(structure_ids: list[str]) -> dict[str, dict]:
    """structure_id → klifs dict 매핑을 한 번에 반환합니다."""
    if not structure_ids:
        return {}
    placeholders = ",".join("?" * len(structure_ids))
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM klifs_structures WHERE structure_id IN ({placeholders})",
        structure_ids,
    ).fetchall()
    conn.close()
    return {dict(r)["structure_id"]: dict(r) for r in rows}


# ═══════════════════════════════════════════
# 10. paper_analysis CRUD
# ═══════════════════════════════════════════

def upsert_paper_analysis(data: dict):
    """
    paper_analysis 테이블에 삽입하거나 기존 행을 업데이트합니다.
    data 키: structure_id (필수), pdf_path, status, raw_text, analyzed_at
    INSERT에 전체 컬럼을 포함하여 ON CONFLICT excluded가 올바른 값을 갖도록 합니다.
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO paper_analysis (structure_id, pdf_path, status, raw_text, analyzed_at)
        VALUES (:structure_id, :pdf_path, :status, :raw_text, :analyzed_at)
        ON CONFLICT(structure_id) DO UPDATE SET
            pdf_path    = excluded.pdf_path,
            status      = excluded.status,
            raw_text    = excluded.raw_text,
            analyzed_at = excluded.analyzed_at
    """, {
        "structure_id": data.get("structure_id"),
        "pdf_path":     data.get("pdf_path"),
        "status":       data.get("status", "none"),
        "raw_text":     data.get("raw_text"),
        "analyzed_at":  data.get("analyzed_at"),
    })
    conn.commit()
    conn.close()


def get_paper_analysis(structure_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM paper_analysis WHERE structure_id = ?", (structure_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ─────────────────────────────────────────────
# 직접 실행 시 DB 초기화
# 터미널: python database.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_database()

    conn = get_connection()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    print("   확인된 테이블:", [t[0] for t in tables])
