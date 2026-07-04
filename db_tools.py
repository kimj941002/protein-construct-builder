# db_tools.py
# Playground LLM 용 **read-only** 앱 DB 조회 툴.
# 사용자가 "앱 DB 로 분석하라"고 하면 Claude 가 SELECT 쿼리로 내부 데이터를 직접 분석한다.
# 안전장치: SELECT/WITH 로 시작하는 단일문만 허용, 파괴적 키워드 차단, READ ONLY 트랜잭션,
#           statement_timeout, 행/셀 길이 제한.
from __future__ import annotations

import re

from sqlalchemy import text

from db_config import get_engine

# 조회 가능한 주요 테이블/뷰 요약 (LLM 에게 스키마 힌트로 제공)
DB_SCHEMA_HINT = """이 앱의 Supabase(Postgres) 주요 테이블/컬럼 (SELECT 전용):
- proteins(uniprot_id PK, gene_name, protein_name, organism, sequence_length, function_desc, subcellular_location)
- pdb_structures(structure_id PK, uniprot_id, source, method, resolution, mean_plddt, chain_id, residue_range, expression_system, space_group, complex_type, doi, deposition_date)
- structure_mutations(structure_id, mutation, position, mutation_type)
- ligands(structure_id, ligand_id, ligand_name, formula, smiles, ligand_type, inchikey)
- papers(paper_id, structure_id, title, doi, authors, analysis_md, model, cost_usd, tags)
- paper_analysis(structure_id, status, structured, pdf_name)
- compounds(chembl_id PK, pref_name, canonical_smiles, inchikey, max_phase, first_approval, molecule_type, clinical_status)
- bioactivities(chembl_id, uniprot_acc, standard_type, standard_value, standard_units, value_nm_normalized, pchembl_value, assay_description)
- clinical_trials(nct_id PK, chembl_id, title, phase, overall_status, conditions, why_stopped, start_date)
- ligand_chembl_map(ligand_id PK, chembl_id)
- compound_activity_summary VIEW(chembl_id, uniprot_acc, pref_name, max_phase, median_pchembl, n_records, best_nM)
주의: pdb_structures 에는 title/제목 컬럼이 없다(논문 제목은 papers.title). 단백질명은 proteins.protein_name.
규칙: 반드시 SELECT 또는 WITH 로 시작. 현재 단백질로 필터하려면 pdb_structures.uniprot_id 또는 bioactivities.uniprot_acc 를 쓰세요.
약물↔PDB 연결: ligands.inchikey ↔ compounds.inchikey, 또는 ligand_chembl_map 로 연결."""

DB_QUERY_TOOL = {
    "name": "query_app_db",
    "description": (
        "앱의 내부 Supabase 데이터베이스에 **읽기 전용 SELECT** 쿼리를 실행해 결과를 받는다. "
        "사용자가 앱 DB(단백질·PDB 구조·논문·화합물·활성·임상시험 등)를 근거로 분석을 요청할 때 사용한다. "
        "SELECT 또는 WITH 로 시작하는 단일 SQL 만 허용된다(파괴적 쿼리 금지). "
        "결과는 최대 50행으로 잘린다.\n\n" + DB_SCHEMA_HINT
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "실행할 단일 SELECT/WITH SQL"},
        },
        "required": ["sql"],
    },
}

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"comment|copy|call|do|merge|vacuum|reindex|refresh|set|reset|"
    r"begin|commit|rollback|savepoint|lock|listen|notify|prepare|execute)\b",
    re.IGNORECASE,
)

_MAX_ROWS = 50
_MAX_CELL = 800


def _is_safe_select(sql: str) -> tuple[bool, str]:
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        return False, "빈 쿼리"
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return False, "SELECT 또는 WITH 로 시작해야 합니다(read-only)."
    if ";" in s:
        return False, "여러 문장(;)은 허용되지 않습니다."
    if _FORBIDDEN.search(s):
        return False, "허용되지 않는(파괴적) 키워드가 포함되어 있습니다."
    return True, s


def run_readonly_sql(sql: str) -> str:
    """SELECT 쿼리 실행 → 결과를 사람이 읽을 수 있는 텍스트(표 형태)로. 실패 시 오류 메시지."""
    ok, s = _is_safe_select(sql)
    if not ok:
        return f"[거부됨] {s}"
    try:
        with get_engine().connect() as conn:
            # read-only + 문장 타임아웃 (안전)
            try:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = 8000")
            except Exception:
                pass
            result = conn.execute(text(s))
            cols = list(result.keys())
            rows = result.fetchmany(_MAX_ROWS + 1)
    except Exception as e:
        return f"[쿼리 오류] {str(e)[:400]}"

    truncated = len(rows) > _MAX_ROWS
    rows = rows[:_MAX_ROWS]
    if not rows:
        return "결과 0행."

    def cell(v):
        sv = "" if v is None else str(v)
        return sv[:_MAX_CELL]

    lines = [" | ".join(cols)]
    lines.append("-" * min(80, len(lines[0])))
    for r in rows:
        lines.append(" | ".join(cell(v) for v in r))
    out = "\n".join(lines)
    if truncated:
        out += f"\n… ({_MAX_ROWS}행 초과 — 잘림)"
    return out
