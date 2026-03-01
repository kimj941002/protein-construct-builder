# database_schema_v1_json_columns.py
# [아카이브] 정규화 이전 스키마 참고용 스냅샷
# 실제 사용 버전: database.py (schema v2, 8-table normalized)
#
# 구 스키마 특징:
#   - proteins.domains     : JSON 문자열 컬럼 (→ v2에서 protein_domains 테이블로 분리)
#   - proteins.sequence    : TEXT 컬럼 (→ v2에서 sequences/ FASTA 파일로 분리)
#   - pdb_structures.mutations : JSON 문자열 컬럼 (→ v2에서 structure_mutations 테이블로 분리)
#   - partner_proteins.partner_chains : JSON 배열 컬럼 (→ v2에서 partner_protein_chains 테이블로 분리)
#   - ptm_oligosaccharides.chains : TEXT (→ v2에서 chain_id로 컬럼명 변경)

SCHEMA_V1_DDL = """
CREATE TABLE IF NOT EXISTS proteins (
    uniprot_id          TEXT PRIMARY KEY,
    gene_name           TEXT,
    protein_name        TEXT,
    organism            TEXT,
    sequence            TEXT,            -- v2에서 sequences/{id}.txt 파일로 분리
    sequence_length     INTEGER,
    function_desc       TEXT,
    subcellular_location TEXT,
    signal_peptide      TEXT,
    domains             TEXT,            -- JSON: [{"name":..,"start":..,"end":..}]
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pdb_structures (
    structure_id        TEXT PRIMARY KEY,
    uniprot_id          TEXT,
    source              TEXT,
    method              TEXT,
    resolution          REAL,
    mean_plddt          REAL,
    chain_id            TEXT,
    residue_range       TEXT,
    expression_system   TEXT,
    host_cell_line      TEXT,
    crystal_method      TEXT,
    crystal_ph          REAL,
    crystal_temp        REAL,
    crystal_details     TEXT,
    space_group         TEXT,
    complex_type        TEXT,
    mutations           TEXT,            -- JSON: [{"mutation":..,"type":..}]
    doi                 TEXT,
    deposition_date     TEXT,
    FOREIGN KEY (uniprot_id) REFERENCES proteins(uniprot_id)
);

CREATE TABLE IF NOT EXISTS ligands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id TEXT,
    ligand_id    TEXT,
    ligand_name  TEXT,
    formula      TEXT,
    smiles       TEXT,
    ligand_type  TEXT,
    FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
);

CREATE TABLE IF NOT EXISTS partner_proteins (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id              TEXT,
    entity_id                 TEXT,
    partner_uniprot_id        TEXT,
    partner_gene_name         TEXT,
    partner_chain_id          TEXT,
    partner_chains            TEXT,      -- JSON 배열: ["A","B"]
    sequence_length           INTEGER,
    organism                  TEXT,
    partner_residue_range     TEXT,
    partner_expression_system TEXT,
    FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
);

CREATE TABLE IF NOT EXISTS ptm_oligosaccharides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id    TEXT,
    entity_id       TEXT,
    name            TEXT,
    chains          TEXT,               -- 단일 체인 문자 (v2에서 chain_id로 변경)
    linked_chain    TEXT,
    linked_position INTEGER,
    linked_residue  TEXT,
    FOREIGN KEY (structure_id) REFERENCES pdb_structures(structure_id)
);
"""
