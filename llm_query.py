# llm_query.py
# Claude API tool use를 통해 단백질 DB에 자연어로 질의하는 모듈.
from __future__ import annotations
# Claude가 run_sql 도구로 필요한 쿼리를 직접 작성·실행하고,
# 결과를 바탕으로 구조생물학적 맥락에서 답변을 생성합니다.

import json
import os
import sqlite3

import anthropic

from config import DB_PATH

# ─────────────────────────────────────────────
# DB 스키마 설명 (시스템 프롬프트용)
# ─────────────────────────────────────────────
_SCHEMA = """
SQLite 데이터베이스 스키마 (단백질 구조 데이터):

1. proteins(uniprot_id TEXT PK, gene_name, protein_name, organism, sequence_length INTEGER,
           function_desc, subcellular_location, signal_peptide)

2. protein_domains(id PK, uniprot_id FK→proteins, name, start_pos INTEGER, end_pos INTEGER)

3. pdb_structures(structure_id TEXT PK, uniprot_id FK→proteins,
       source,                  -- 'PDB' 또는 'AlphaFold'
       method,                  -- 실험법: X-ray, cryo-EM, NMR 등
       resolution REAL,         -- 해상도(Å); AlphaFold는 NULL
       mean_plddt REAL,         -- AlphaFold pLDDT; PDB는 NULL
       chain_id,                -- 타겟 체인 ID
       residue_range,           -- 구조에 포함된 잔기 범위 (예: '25-1380')
       expression_system,       -- 단백질 유래 생물 (UI: Organism)
       host_cell_line,          -- 발현 숙주 (UI: Expr System; 예: E.coli, HEK293)
       crystal_method, crystal_ph REAL, crystal_temp REAL, crystal_details,
       space_group,             -- 결정 공간군
       complex_type,            -- 'apo'|'ligand'|'protein'|'mixed'
       doi,                     -- 논문 DOI
       deposition_date)         -- RCSB 등재일 (YYYY-MM-DD)

4. structure_mutations(id PK, structure_id FK→pdb_structures,
       mutation,                -- HGVS 표기 (예: A123V)
       position INTEGER,
       mutation_type)           -- 'engineered'|'natural variant' 등

5. ligands(id PK, structure_id FK→pdb_structures,
       ligand_id,               -- CCD 코드 (예: ATP, STU)
       ligand_name, formula, smiles, ligand_type)

6. partner_proteins(id PK, structure_id FK→pdb_structures,
       partner_uniprot_id, partner_gene_name, partner_chain_id,
       sequence_length INTEGER, organism, partner_residue_range,
       partner_expression_system)

7. partner_protein_chains(id PK, partner_id FK→partner_proteins, chain_id)

8. ptm_oligosaccharides(id PK, structure_id FK→pdb_structures,
       name, chain_id, linked_chain, linked_position INTEGER, linked_residue)

9. klifs_structures(id PK, structure_id FK→pdb_structures UNIQUE,
       dfg,        -- 키나아제 DFG 형태: 'DFGin'|'DFGout'|'DFGinter'|NULL(비키나아제)
       ac_helix)   -- αC-helix 형태: 'αCin'|'αCout'|NULL

10. paper_analysis(id PK, structure_id FK→pdb_structures UNIQUE,
       status,     -- 'none'|'uploaded'|'analyzing'|'completed'|'error'
       raw_text,   -- Claude가 분석한 논문 전문 요약
       analyzed_at TIMESTAMP)

주요 주의사항:
- klifs_structures 행이 있어도 dfg/ac_helix가 NULL이면 비키나아제 (KLIFS 미등록).
- expression_system = 단백질 유래 종 (인간: 'Homo sapiens')
- host_cell_line = 단백질 발현 숙주 (박테리아: 'Escherichia coli' 등)
- pdb_structures.source = 'PDB'인 경우만 실험 구조; AlphaFold는 예측 구조.
"""

_SYSTEM_PROMPT = f"""당신은 단백질 구조 데이터베이스 전문 AI 어시스턴트입니다.
사용자 질문에 답하기 위해 run_sql 도구로 SQLite 데이터베이스를 조회하세요.
필요하면 여러 번 조회해도 됩니다. SELECT(또는 WITH) 쿼리만 허용됩니다.
조회 결과를 바탕으로 구조생물학적으로 의미 있는 한국어 답변을 작성하세요.
데이터에 없는 내용은 추측하지 말고 "DB에 해당 데이터가 없습니다"라고 명시하세요.

{_SCHEMA}"""

_TOOLS = [
    {
        "name": "run_sql",
        "description": (
            "SQLite DB에서 SELECT(또는 WITH) 쿼리를 실행합니다. "
            "결과는 최대 500행으로 제한됩니다. "
            "대용량 결과가 예상되면 LIMIT절을 사용하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "실행할 SQLite SELECT 쿼리 (SELECT 또는 WITH로 시작해야 함)",
                }
            },
            "required": ["query"],
        },
    }
]


# ─────────────────────────────────────────────
# SQL 실행
# ─────────────────────────────────────────────
def execute_sql(sql: str) -> tuple[list[dict], str | None]:
    """
    SELECT / WITH 쿼리만 허용하여 실행합니다.
    Returns:
        (rows: list[dict], error: str | None)
    """
    import re
    stripped = sql.strip()
    # 주석 제거 후 첫 키워드 검사 (Claude가 -- 주석을 SQL 앞에 붙이는 경우 대응)
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", "", stripped, flags=re.DOTALL).strip()
    upper = cleaned.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return [], "보안 정책: SELECT 또는 WITH 쿼리만 허용됩니다."
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(stripped).fetchall()]
        conn.close()
        return rows, None
    except Exception as exc:
        return [], str(exc)


def _format_rows_for_llm(rows: list[dict]) -> str:
    """Claude에게 전달할 결과 문자열 (최대 500행)."""
    MAX_ROWS = 500
    truncated = len(rows) > MAX_ROWS
    display = rows[:MAX_ROWS]
    text = json.dumps(display, ensure_ascii=False, default=str)
    if truncated:
        text += f"\n[결과가 {len(rows)}행으로 {MAX_ROWS}행까지만 표시됨]"
    return text


# ─────────────────────────────────────────────
# 메인 질의 함수
# ─────────────────────────────────────────────
def query_db_with_llm(
    question: str,
    api_key: str | None = None,
) -> dict:
    """
    자연어 질문을 받아 Claude tool use로 DB를 조회하고 답변을 반환합니다.

    Args:
        question: 사용자 질문 (한국어/영어)
        api_key:  Anthropic API 키 (None이면 환경변수 ANTHROPIC_API_KEY 사용)

    Returns:
        {
            "queries": [{"sql": str, "rows": list[dict], "error": str|None}],
            "answer":  str,
            "error":   str | None   # 전체 실패 시만 설정
        }
    """
    if not api_key:
        api_key = os.getenv("ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key)
    messages: list[dict] = [{"role": "user", "content": question}]
    executed_queries: list[dict] = []
    MAX_ITERATIONS = 10

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            system=_SYSTEM_PROMPT,
            tools=_TOOLS,
            messages=messages,
        )

        # 어시스턴트 응답 메시지 히스토리에 추가
        messages.append({"role": "assistant", "content": response.content})

        # 최종 답변
        if response.stop_reason == "end_turn":
            answer = next(
                (b.text for b in response.content if b.type == "text"),
                "답변을 생성하지 못했습니다.",
            )
            return {"queries": executed_queries, "answer": answer, "error": None}

        # 도구 호출 처리
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use" or block.name != "run_sql":
                    continue
                sql = block.input.get("query", "")
                rows, error = execute_sql(sql)
                executed_queries.append({"sql": sql, "rows": rows, "error": error})

                if error:
                    content = f"SQL 오류: {error}"
                elif not rows:
                    content = "결과 없음 (0 rows)"
                else:
                    content = _format_rows_for_llm(rows)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # pause_turn: 서버 루프 한도 도달 → 재전송
        if response.stop_reason == "pause_turn":
            continue

        # max_tokens: 토큰 한도 도달 → 부분 응답에서 텍스트 추출 후 반환
        if response.stop_reason == "max_tokens":
            answer = next(
                (b.text for b in response.content if b.type == "text"),
                "응답이 너무 길어 잘렸습니다. 질문을 더 구체적으로 입력해주세요.",
            )
            return {"queries": executed_queries, "answer": answer, "error": None}

        # 예상치 못한 종료
        break

    return {
        "queries": executed_queries,
        "answer": "",
        "error": "최대 반복 횟수에 도달했습니다.",
    }
