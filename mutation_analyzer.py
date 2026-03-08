# mutation_analyzer.py
# PDB 구조에서 돌연변이(mutation)를 감지하는 모듈
# SIFTS API로 PDB ↔ UniProt 잔기 번호를 매핑하고,
# WT(UniProt) 서열과 PDB 서열을 비교하여 차이를 찾습니다.

from __future__ import annotations
import json
import os
import requests
from config import SIFTS_API, RCSB_POLYMER_ENTITY_API
from utils import api_call_with_retry, create_cached_session
from database import get_protein, get_structure, insert_mutations_bulk
from uniprot_fetcher import load_sequence_from_file


def get_sifts_mapping(pdb_id: str, target_uniprot_id: str,
                      session=None) -> list[dict]:
    """
    PDBe SIFTS API로 PDB 잔기 번호 ↔ UniProt 잔기 번호 매핑을 가져옵니다.

    Parameters:
        pdb_id (str): PDB ID (예: '2WGJ')
        target_uniprot_id (str): 타겟 단백질 UniProt ID (예: 'P08581')
        session: CachedSession 또는 None

    Returns:
        list[dict]: 매핑 세그먼트 목록
                    각 딕셔너리: {
                        'pdb_start': int,   # PDB entity 서열 번호 (1-indexed)
                        'pdb_end': int,
                        'unp_start': int,   # UniProt 잔기 번호
                        'unp_end': int,
                    }
                    실패 시 빈 리스트
    """
    url = f"{SIFTS_API}/{pdb_id.lower()}"
    data = api_call_with_retry(url, session=session)

    if not data:
        return []

    # SIFTS 응답: {pdb_id_lower: {UniProt: {uniprot_id: {mappings: [...]}}}}
    pdb_key = pdb_id.lower()
    if pdb_key not in data:
        return []

    uniprot_section = data[pdb_key].get("UniProt", {})
    target_mapping = uniprot_section.get(target_uniprot_id, {})
    mappings = target_mapping.get("mappings", [])

    segments = []
    for m in mappings:
        pdb_start = m.get("start", {}).get("residue_number")
        pdb_end   = m.get("end",   {}).get("residue_number")
        unp_start = m.get("unp_start")
        unp_end   = m.get("unp_end")

        # 매핑 정보가 완전한 경우만 포함
        if None not in (pdb_start, pdb_end, unp_start, unp_end):
            segments.append({
                "pdb_start": pdb_start,
                "pdb_end":   pdb_end,
                "unp_start": unp_start,
                "unp_end":   unp_end,
            })

    return segments


def build_pdb_to_unp_map(sifts_segments: list[dict]) -> dict[int, int]:
    """
    SIFTS 세그먼트 목록을 {pdb_residue_idx → uniprot_pos} 딕셔너리로 변환합니다.

    Parameters:
        sifts_segments (list[dict]): get_sifts_mapping() 반환값

    Returns:
        dict: {pdb_1indexed_pos: uniprot_pos}
    """
    mapping = {}
    for seg in sifts_segments:
        pdb_start = seg["pdb_start"]
        unp_start = seg["unp_start"]
        length    = seg["pdb_end"] - seg["pdb_start"]

        for i in range(length + 1):
            pdb_pos = pdb_start + i
            unp_pos = unp_start + i
            mapping[pdb_pos] = unp_pos

    return mapping


def get_entity_sequence(pdb_id: str, entity_id: str = "1",
                        session=None) -> str:
    """
    RCSB Polymer Entity API에서 construct 서열을 가져옵니다.

    Parameters:
        pdb_id (str): PDB ID
        entity_id (str): Entity 번호 (보통 '1')
        session: CachedSession

    Returns:
        str: 단일 문자 아미노산 서열 (없으면 빈 문자열)
    """
    url = f"{RCSB_POLYMER_ENTITY_API}/{pdb_id}/{entity_id}"
    data = api_call_with_retry(url, session=session)
    if not data:
        return ""

    # pdbx_seq_one_letter_code: 삽입 코드/gap 포함 원본 서열
    seq = data.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can", "")
    if not seq:
        seq = data.get("entity_poly", {}).get("pdbx_seq_one_letter_code", "")

    # X (알 수 없는 잔기) 처리: 비교에서 제외
    return seq.strip().replace("\n", "")


def find_target_entity_id(pdb_id: str, target_uniprot_id: str,
                           session=None) -> str:
    """
    PDB 구조에서 타겟 UniProt에 해당하는 entity ID를 찾습니다.

    Returns:
        str: entity ID (예: '1'), 없으면 '1' (기본값)
    """
    from config import RCSB_ENTRY_API
    entry_url = f"{RCSB_ENTRY_API}/{pdb_id}"
    entry_data = api_call_with_retry(entry_url, session=session)
    if not entry_data:
        return "1"

    polymer_ids = entry_data.get(
        "rcsb_entry_container_identifiers", {}
    ).get("polymer_entity_ids", ["1"])

    for eid in polymer_ids:
        entity_url = f"{RCSB_POLYMER_ENTITY_API}/{pdb_id}/{eid}"
        entity_data = api_call_with_retry(entity_url, session=session)
        if not entity_data:
            continue

        ref_ids = entity_data.get(
            "rcsb_polymer_entity_container_identifiers", {}
        ).get("reference_sequence_identifiers", [])

        for ref in ref_ids:
            if (ref.get("database_name") == "UniProt" and
                    ref.get("database_accession") == target_uniprot_id):
                return str(eid)

    return "1"


def get_pdbx_mutation(pdb_id: str, entity_id: str, session=None) -> str | None:
    """
    RCSB에서 pdbx_mutation 필드(공식 mutation 기술)를 가져옵니다.

    Returns:
        str: mutation 기술 문자열 (없으면 None)
    """
    url = f"{RCSB_POLYMER_ENTITY_API}/{pdb_id}/{entity_id}"
    data = api_call_with_retry(url, session=session)
    if not data:
        return None
    return data.get("rcsb_polymer_entity", {}).get("pdbx_mutation")


def compare_sequences(wt_sequence: str, pdb_sequence: str,
                      pdb_to_unp: dict[int, int]) -> list[dict]:
    """
    WT 서열과 PDB construct 서열을 SIFTS 매핑 기반으로 비교하여 돌연변이를 찾습니다.

    Parameters:
        wt_sequence (str): UniProt WT 서열 (전체 단백질)
        pdb_sequence (str): PDB construct 서열 (entity 서열)
        pdb_to_unp (dict): {pdb_1indexed_pos: uniprot_pos}

    Returns:
        list[dict]: [{"mutation": "K1110A", "position": 1110}, ...]
    """
    mutations_found = []

    for pdb_pos_1indexed, unp_pos in pdb_to_unp.items():
        # PDB 서열에서 해당 위치의 아미노산
        pdb_seq_idx = pdb_pos_1indexed - 1  # 0-indexed
        if pdb_seq_idx < 0 or pdb_seq_idx >= len(pdb_sequence):
            continue
        pdb_aa = pdb_sequence[pdb_seq_idx]

        # UniProt 서열에서 해당 위치의 WT 아미노산
        unp_seq_idx = unp_pos - 1  # 0-indexed
        if unp_seq_idx < 0 or unp_seq_idx >= len(wt_sequence):
            continue
        wt_aa = wt_sequence[unp_seq_idx]

        # X는 알 수 없는 잔기 — 비교 제외
        if pdb_aa == "X" or wt_aa == "X":
            continue

        # 차이 발견 → mutation
        if pdb_aa != wt_aa:
            mutation_code = f"{wt_aa}{unp_pos}{pdb_aa}"
            mutations_found.append({
                "mutation": mutation_code,
                "position": unp_pos,
            })

    return mutations_found


def classify_mutations(mutations_found: list[dict],
                       pdbx_mutation: str | None) -> list[dict]:
    """
    감지된 돌연변이를 engineered / natural_variant로 분류합니다.

    분류 기준:
    - pdbx_mutation 필드(공식 기술)에 언급된 위치 → 'engineered'
    - 그 외 → 'natural_variant'

    Parameters:
        mutations_found (list[dict]): compare_sequences() 반환값
        pdbx_mutation (str|None): RCSB pdbx_mutation 필드 값

    Returns:
        list[dict]: [{"mutation": "K1110A", "type": "engineered"}, ...]
    """
    # pdbx_mutation 문자열에서 언급된 위치 번호 추출
    engineered_positions = set()
    if pdbx_mutation:
        import re
        # 숫자 추출 (예: "K1110A" → 1110, "1110A" → 1110)
        positions = re.findall(r'\d+', pdbx_mutation)
        engineered_positions = {int(p) for p in positions}

    classified = []
    for mut in mutations_found:
        mutation_type = (
            "engineered"
            if mut["position"] in engineered_positions
            else "natural_variant"
        )
        classified.append({
            "mutation": mut["mutation"],
            "type":     mutation_type,
        })

    return classified


def analyze_mutations(structure_id: str, target_uniprot_id: str,
                      session=None) -> list[dict]:
    """
    단일 PDB 구조의 돌연변이를 분석하고 DB를 업데이트합니다.

    Parameters:
        structure_id (str): PDB ID 또는 'AlphaFoldDB' source인 경우 AF ID
        target_uniprot_id (str): 타겟 단백질 UniProt ID
        session: CachedSession

    Returns:
        list[dict]: 최종 mutation 목록 [{"mutation": "K1110A", "type": "engineered"}, ...]
    """
    if session is None:
        session = create_cached_session()

    # AlphaFold 구조는 mutation 분석 불필요 → 빈 배열 저장
    structure = get_structure(structure_id)
    if structure and structure.get("source") == "AlphaFoldDB":
        print(f"[INFO] {structure_id}: AlphaFold 구조 — mutation 분석 skip")
        insert_mutations_bulk(structure_id, [])
        return []

    print(f"[INFO] {structure_id}: mutation 분석 중...")

    # 1. WT 서열 (sequences/ 폴더의 FASTA 파일에서 읽음)
    protein_data = get_protein(target_uniprot_id)
    if not protein_data:
        print(f"[WARN] {structure_id}: WT 서열 없음 (DB에 단백질 정보 없음)")
        return []
    sequence_path = protein_data.get("sequence_path", "")
    wt_sequence = load_sequence_from_file(sequence_path) if sequence_path else ""
    if not wt_sequence:
        print(f"[WARN] {structure_id}: WT 서열 없음 (파일 없음: {sequence_path})")
        return []

    # 2. SIFTS 매핑 가져오기
    sifts_segments = get_sifts_mapping(structure_id, target_uniprot_id, session)
    if not sifts_segments:
        print(f"[WARN] {structure_id}: SIFTS 매핑 없음")
        insert_mutations_bulk(structure_id, [])
        return []

    # 3. PDB → UniProt 위치 매핑 딕셔너리 생성
    pdb_to_unp = build_pdb_to_unp_map(sifts_segments)

    # 4. 타겟 entity ID 찾기
    entity_id = find_target_entity_id(structure_id, target_uniprot_id, session)

    # 5. PDB construct 서열 가져오기
    pdb_sequence = get_entity_sequence(structure_id, entity_id, session)
    if not pdb_sequence:
        print(f"[WARN] {structure_id}: PDB 서열을 가져오지 못함")
        insert_mutations_bulk(structure_id, [])
        return []

    # 6. pdbx_mutation 가져오기 (engineered 분류용)
    pdbx_mutation = get_pdbx_mutation(structure_id, entity_id, session)

    # 7. 서열 비교 → mutation 감지
    raw_mutations = compare_sequences(wt_sequence, pdb_sequence, pdb_to_unp)

    # 8. engineered / natural_variant 분류
    classified = classify_mutations(raw_mutations, pdbx_mutation)

    # 9. structure_mutations 테이블에 저장
    insert_mutations_bulk(structure_id, classified)

    if classified:
        print(f"[OK] {structure_id}: {len(classified)}개 mutation 감지: "
              f"{[m['mutation'] for m in classified[:3]]}{'...' if len(classified) > 3 else ''}")
    else:
        print(f"[OK] {structure_id}: Wild-type (mutation 없음)")

    return classified


def analyze_all_structures(uniprot_id: str, session=None) -> None:
    """
    DB에 저장된 모든 PDB 구조에 대해 mutation 분석을 실행합니다.

    Parameters:
        uniprot_id (str): 분석할 단백질의 UniProt ID
        session: CachedSession
    """
    from database import get_structures_by_uniprot

    if session is None:
        session = create_cached_session()

    structures = get_structures_by_uniprot(uniprot_id)
    print(f"[INFO] mutation 분석 대상: {len(structures)}개 구조")

    for struct in structures:
        sid = struct["structure_id"]
        try:
            analyze_mutations(sid, uniprot_id, session)
        except Exception as e:
            print(f"[WARN] {sid} mutation 분석 오류: {e}")


# ─────────────────────────────────────────────
# 직접 실행 시 테스트
# 터미널에서: python mutation_analyzer.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    TARGET_UNIPROT = "P08581"  # MET (cMET)

    print("=" * 50)
    print("Phase 4 테스트: Mutation 분석")
    print("=" * 50)

    session = create_cached_session()
    analyze_all_structures(TARGET_UNIPROT, session)

    # DB 결과 확인
    import sqlite3
    conn = sqlite3.connect("protein_data.db")
    cursor = conn.cursor()
    print()
    print("[DB 결과]")
    cursor.execute(
        "SELECT structure_id, mutations FROM pdb_structures WHERE mutations IS NOT NULL"
    )
    for row in cursor.fetchall():
        muts = json.loads(row[1]) if row[1] else []
        print(f"  {row[0]}: {muts}")
    conn.close()
