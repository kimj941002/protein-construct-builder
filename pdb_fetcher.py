# pdb_fetcher.py
# RCSB PDB API를 이용해 구조 정보를 수집하는 모듈
# Entry API → 기본 정보/결정화 조건/DOI
# Polymer Entity API → 발현 시스템/서열/mutation
# ThreadPoolExecutor로 병렬 처리

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    RCSB_ENTRY_API, RCSB_POLYMER_ENTITY_API,
    MAX_WORKERS
)
from utils import api_call_with_retry, create_cached_session
from database import insert_structure, get_structures_by_uniprot

# 수용액(물) 및 흔한 버퍼 이온 — 리간드로 취급하지 않음
EXCLUDE_LIGANDS = {
    "HOH", "DOD", "H2O",  # 물
    "EDO", "PEG",          # 흔한 결정화 시약
}


def fetch_entry_info(pdb_id: str, session) -> dict | None:
    """
    RCSB Entry API에서 PDB 구조 기본 정보를 가져옵니다.

    Parameters:
        pdb_id (str): PDB ID (예: '2WGJ')
        session: CachedSession

    Returns:
        dict: Entry API 응답 전체 (실패 시 None)
    """
    url = f"{RCSB_ENTRY_API}/{pdb_id}"
    return api_call_with_retry(url, session=session)


def extract_method_and_resolution(entry_data: dict) -> tuple[str, float | None]:
    """
    실험 방법과 해상도를 추출합니다.

    Returns:
        (method, resolution) 튜플
        - method: 'X-RAY DIFFRACTION' / 'SOLUTION NMR' / 'ELECTRON MICROSCOPY' 등
        - resolution: float 또는 None (NMR은 None)
    """
    exptl = entry_data.get("exptl", [])
    method = exptl[0].get("method", "UNKNOWN") if exptl else "UNKNOWN"

    resolution_combined = entry_data.get("rcsb_entry_info", {}).get("resolution_combined")
    if resolution_combined and isinstance(resolution_combined, list) and len(resolution_combined) > 0:
        try:
            resolution = float(resolution_combined[0])
        except (TypeError, ValueError):
            resolution = None
    elif isinstance(resolution_combined, (int, float)):
        try:
            resolution = float(resolution_combined)
        except (TypeError, ValueError):
            resolution = None
    else:
        resolution = None

    # NMR은 해상도가 없음 → None 유지
    if "NMR" in method.upper():
        resolution = None

    return method, resolution


def extract_crystal_info(entry_data: dict, method: str) -> dict:
    """
    결정화 조건을 추출합니다. X-RAY인 경우에만 유의미한 데이터가 있습니다.

    Returns:
        dict: crystal_method, crystal_ph, crystal_temp, crystal_details, space_group
              X-RAY 이외에는 모두 None
    """
    crystal = {
        "crystal_method":  None,
        "crystal_ph":      None,
        "crystal_temp":    None,
        "crystal_details": None,
        "space_group":     None,
    }

    # NMR / Cryo-EM 등은 결정화 조건 없음
    if "X-RAY" not in method.upper() and "X RAY" not in method.upper():
        return crystal

    # 결정화 조건
    grow_list = entry_data.get("exptl_crystal_grow", [])
    if grow_list:
        grow = grow_list[0]
        crystal["crystal_method"]  = grow.get("method")
        ph = grow.get("p_h")
        crystal["crystal_ph"]      = float(ph) if ph is not None else None
        temp = grow.get("temp")
        crystal["crystal_temp"]    = float(temp) if temp is not None else None
        crystal["crystal_details"] = grow.get("pdbx_details")

    # 공간군
    symmetry = entry_data.get("symmetry", {})
    crystal["space_group"] = symmetry.get("space_group_name_hm")

    return crystal


def extract_doi_and_date(entry_data: dict) -> tuple[str | None, str | None]:
    """DOI와 등록일을 추출합니다."""
    # DOI
    doi = None
    citations = entry_data.get("citation", [])
    for cit in citations:
        doi_val = cit.get("pdbx_database_id_doi")
        if doi_val:
            doi = doi_val
            break

    # 등록일 (YYYY-MM-DD 형식으로 자름)
    dep_date_raw = entry_data.get("rcsb_accession_info", {}).get("deposit_date", "")
    deposition_date = dep_date_raw[:10] if dep_date_raw else None

    return doi, deposition_date


def fetch_polymer_entity(pdb_id: str, entity_id: str, session) -> dict | None:
    """
    RCSB Polymer Entity API에서 폴리머(단백질/RNA 등) 정보를 가져옵니다.

    Returns:
        dict: Polymer Entity API 응답 전체 (실패 시 None)
    """
    url = f"{RCSB_POLYMER_ENTITY_API}/{pdb_id}/{entity_id}"
    return api_call_with_retry(url, session=session)


def find_target_entity(pdb_id: str, polymer_entity_ids: list[str],
                       target_uniprot_id: str, session) -> tuple[dict | None, str | None]:
    """
    여러 폴리머 entity 중 타겟 UniProt ID에 해당하는 entity를 찾습니다.

    Returns:
        (entity_data, entity_id) 튜플
        타겟을 못 찾으면 (None, None)
    """
    for eid in polymer_entity_ids:
        entity_data = fetch_polymer_entity(pdb_id, eid, session)
        if not entity_data:
            continue

        # UniProt 매핑 확인
        ref_ids = entity_data.get(
            "rcsb_polymer_entity_container_identifiers", {}
        ).get("reference_sequence_identifiers", [])

        for ref in ref_ids:
            if (ref.get("database_name") == "UniProt" and
                    ref.get("database_accession") == target_uniprot_id):
                return entity_data, eid

    return None, None


def extract_entity_details(entity_data: dict, session) -> dict:
    """
    Polymer Entity 데이터에서 발현 시스템, 숙주, 잔기 범위, mutation을 추출합니다.

    Returns:
        dict: expression_system, host_cell_line, residue_range, pdbx_mutation, chain_id
    """
    result = {
        "expression_system": None,
        "host_cell_line":    None,
        "residue_range":     None,
        "pdbx_mutation":     None,
        "chain_id":          None,
    }

    # 발현 시스템 (소스 생물종)
    src_org = entity_data.get("rcsb_entity_source_organism", [])
    if src_org:
        result["expression_system"] = src_org[0].get("scientific_name")

    # 숙주 생물종
    host_org = entity_data.get("rcsb_entity_host_organism", [])
    if host_org:
        result["host_cell_line"] = host_org[0].get("scientific_name")

    # 잔기 범위 (UniProt 번호 기준)
    align_list = entity_data.get("rcsb_polymer_entity_align", [])
    if align_list:
        aligned_regions = align_list[0].get("aligned_regions", [])
        if aligned_regions:
            # 여러 aligned_region이 있을 수 있음 → 전체 범위 계산
            starts = [r.get("ref_beg_seq_id", 0) for r in aligned_regions]
            ends = [
                r.get("ref_beg_seq_id", 0) + r.get("length", 0) - 1
                for r in aligned_regions
            ]
            result["residue_range"] = f"{min(starts)}-{max(ends)}"

    # mutation 정보 (pdbx_mutation 필드)
    pdbx_mutation = entity_data.get("rcsb_polymer_entity", {}).get("pdbx_mutation")
    result["pdbx_mutation"] = pdbx_mutation

    # 체인 ID
    chain_ids = entity_data.get(
        "rcsb_polymer_entity_container_identifiers", {}
    ).get("auth_asym_ids", [])
    if chain_ids:
        result["chain_id"] = chain_ids[0]

    return result


def classify_complex_type(entry_data: dict, target_uniprot_id: str,
                           polymer_entity_ids: list[str], session) -> str:
    """
    복합체 유형을 분류합니다.

    분류 기준:
    - apo: 타겟 단백질만 있음 (리간드 없음, 파트너 없음)
    - ligand: 소분자 리간드 있음, 파트너 없음
    - protein-protein: 파트너 단백질 있음, 리간드 없음
    - mixed: 리간드 + 파트너 모두 있음

    Returns:
        str: 'apo' / 'ligand' / 'protein-protein' / 'mixed'
    """
    pdb_id = entry_data.get("rcsb_id", "")
    ids = entry_data.get("rcsb_entry_container_identifiers", {})

    # 비폴리머 entity 수 (물 제외 실제 리간드)
    nonpoly_ids = ids.get("non_polymer_entity_ids", []) or []
    has_ligand = len(nonpoly_ids) > 0  # 간단히 존재 여부로 판단

    # 파트너 단백질: 타겟 UniProt 외 다른 UniProt에 매핑된 polymer entity
    has_partner = False
    for eid in polymer_entity_ids:
        entity_data = fetch_polymer_entity(pdb_id, eid, session)
        if not entity_data:
            continue

        # 폴리머 타입 확인 (단백질만)
        poly_type = entity_data.get("entity_poly", {}).get("type", "")
        if "polypeptide" not in poly_type.lower():
            continue

        ref_ids = entity_data.get(
            "rcsb_polymer_entity_container_identifiers", {}
        ).get("reference_sequence_identifiers", [])

        for ref in ref_ids:
            if (ref.get("database_name") == "UniProt" and
                    ref.get("database_accession") != target_uniprot_id):
                has_partner = True
                break

        if has_partner:
            break

    if has_ligand and has_partner:
        return "mixed"
    elif has_ligand:
        return "ligand"
    elif has_partner:
        return "protein-protein"
    else:
        return "apo"


def process_single_pdb(pdb_id: str, target_uniprot_id: str, session) -> dict | None:
    """
    단일 PDB ID의 구조 정보를 수집하여 딕셔너리로 반환합니다.
    이 함수가 ThreadPoolExecutor에서 병렬로 호출됩니다.

    Returns:
        dict: pdb_structures 테이블에 삽입할 데이터 (실패 시 None)
    """
    # 1. Entry API 호출
    entry_data = fetch_entry_info(pdb_id, session)
    if not entry_data:
        return None

    # 2. 실험 방법 + 해상도
    method, resolution = extract_method_and_resolution(entry_data)

    # 3. 결정화 조건
    crystal_info = extract_crystal_info(entry_data, method)

    # 4. DOI + 등록일
    doi, deposition_date = extract_doi_and_date(entry_data)

    # 5. Polymer entity ID 목록
    ids = entry_data.get("rcsb_entry_container_identifiers", {})
    polymer_entity_ids = ids.get("polymer_entity_ids", []) or []

    # 6. 타겟 entity 찾기
    target_entity, target_eid = find_target_entity(
        pdb_id, polymer_entity_ids, target_uniprot_id, session
    )

    if not target_entity:
        # 타겟 단백질이 이 구조에 없으면 건너뜀
        return None

    # 7. 발현 시스템 등 추출
    entity_details = extract_entity_details(target_entity, session)

    # 8. 복합체 분류 (타겟 제외 나머지 polymer entity 분석)
    other_polymer_ids = [e for e in polymer_entity_ids if e != target_eid]
    complex_type = classify_complex_type(
        entry_data, target_uniprot_id, other_polymer_ids, session
    )

    # 9. 최종 데이터 조립
    structure = {
        "structure_id":     pdb_id,
        "uniprot_id":       target_uniprot_id,
        "source":           "PDB",
        "method":           method,
        "resolution":       resolution,
        "mean_plddt":       None,  # AlphaFold 전용
        "chain_id":         entity_details["chain_id"],
        "residue_range":    entity_details["residue_range"],
        "expression_system":entity_details["expression_system"],
        "host_cell_line":   entity_details["host_cell_line"],
        "crystal_method":   crystal_info["crystal_method"],
        "crystal_ph":       crystal_info["crystal_ph"],
        "crystal_temp":     crystal_info["crystal_temp"],
        "crystal_details":  crystal_info["crystal_details"],
        "space_group":      crystal_info["space_group"],
        "complex_type":     complex_type,
        "doi":              doi,
        "deposition_date":  deposition_date,
    }

    return structure


def fetch_all_structures(pdb_ids: list[str], target_uniprot_id: str,
                         progress_callback=None) -> list[dict]:
    """
    PDB ID 목록 전체를 병렬로 처리하여 구조 정보를 수집합니다.
    이미 DB에 수집된 PDB ID는 건너뛰고 신규 항목만 처리합니다 (증분 수집).
    수집된 데이터는 pdb_structures 테이블에 자동 저장됩니다.

    Parameters:
        pdb_ids (list[str]): 처리할 PDB ID 목록 (UniProt 전체 목록)
        target_uniprot_id (str): 타겟 단백질 UniProt ID
        progress_callback: 진행률 콜백 함수 (current, total) → None (Streamlit용)

    Returns:
        list[dict]: 신규 수집된 구조 정보 목록
    """
    # 이미 수집된 PDB ID 조회 → 신규 항목만 처리 (증분 수집)
    existing_ids = {s["structure_id"] for s in get_structures_by_uniprot(target_uniprot_id)}
    target_ids = [pid for pid in pdb_ids if pid not in existing_ids]
    total = len(target_ids)
    print(f"[INFO] UniProt PDB 전체: {len(pdb_ids)}개 | 이미 수집: {len(existing_ids)}개 | 신규 수집 대상: {total}개")

    if total == 0:
        print("[OK] 신규 수집 대상 없음. 기존 데이터를 그대로 사용합니다.")
        return []

    results = []
    completed_count = 0

    # CachedSession은 스레드별로 개별 생성 (thread-safe 문제 방지)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 각 PDB ID마다 독립적인 session 생성하여 처리
        future_to_pdb = {
            executor.submit(
                process_single_pdb,
                pdb_id,
                target_uniprot_id,
                create_cached_session()   # 스레드별 개별 session
            ): pdb_id
            for pdb_id in target_ids
        }

        for future in as_completed(future_to_pdb):
            pdb_id = future_to_pdb[future]
            completed_count += 1

            try:
                structure = future.result()
                if structure:
                    insert_structure(structure)
                    results.append(structure)
            except Exception as e:
                print(f"[WARN] {pdb_id} 처리 오류: {e}")

            # 진행률 표시
            if completed_count % 10 == 0 or completed_count == total:
                print(f"[INFO] 진행: {completed_count}/{total} ({len(results)}개 수집)")

            # Streamlit progress_callback 호출
            if progress_callback:
                progress_callback(completed_count, total)

    print(f"[OK] 수집 완료: 총 {len(results)}개 구조 저장됨")
    return results


# ─────────────────────────────────────────────
# 직접 실행 시 테스트: cMET 일부 PDB ID 처리
# 터미널에서: python pdb_fetcher.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    TARGET_UNIPROT = "P08581"  # MET (cMET)
    TEST_PDB_IDS = ["2WGJ", "3DKC", "3CCN", "1R0P", "2G15"]

    print("=" * 50)
    print("Phase 3 테스트: cMET PDB 구조 수집")
    print("=" * 50)

    results = fetch_all_structures(TEST_PDB_IDS, TARGET_UNIPROT)

    print()
    print("[결과 요약]")
    for s in results:
        doi_short = (s['doi'][:30] + "...") if s.get('doi') else "없음"
        print(f"  {s['structure_id']} | {s['method'][:10]} | "
              f"{s['resolution']}A | {s['complex_type']} | DOI:{doi_short}")
