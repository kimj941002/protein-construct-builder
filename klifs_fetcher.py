# klifs_fetcher.py
# KLIFS (Kinase-Ligand Interaction Fingerprints and Structures) API를 통해
# 키나아제 구조 정보를 수집합니다.
# 비키나아제 단백질의 경우 데이터가 없으므로 graceful skip 처리됩니다.
#
# API 문서: https://klifs.net/swagger/
# 엔드포인트: GET https://klifs.net/api/structures/pdb_complexes?pdb-codes={pdb_id}

import requests
from database import insert_klifs_structure, get_klifs_by_structure

KLIFS_API_BASE = "https://klifs.net/api"
KLIFS_TIMEOUT  = 10  # seconds


def fetch_klifs_for_pdb(pdb_id: str) -> list[dict]:
    """
    KLIFS API에서 PDB ID에 해당하는 구조 정보를 가져옵니다.

    Parameters:
        pdb_id (str): PDB ID (예: '4HJO')

    Returns:
        list[dict]: KLIFS 구조 목록 (비키나아제이거나 실패 시 빈 리스트)
    """
    url = f"{KLIFS_API_BASE}/structures_pdb_list"
    try:
        resp = requests.get(url, params={"pdb-codes": pdb_id}, timeout=KLIFS_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
        return []
    except Exception as e:
        print(f"[WARN] KLIFS API 오류 ({pdb_id}): {e}")
        return []


def process_klifs(pdb_id: str, target_chain: str | None = None) -> bool:
    """
    단일 PDB ID의 KLIFS 데이터를 수집하여 DB에 저장합니다.

    Parameters:
        pdb_id (str): PDB ID
        target_chain (str | None): 타겟 체인 ID — 일치하는 KLIFS 항목 우선 선택

    Returns:
        bool: 수집 성공 여부 (비키나아제면 False)
    """
    structures = fetch_klifs_for_pdb(pdb_id)

    if not structures:
        # 비키나아제 또는 KLIFS 미등록 → sentinel 행 삽입하여 다음 실행 시 재검색 방지
        insert_klifs_structure({"structure_id": pdb_id, "dfg": None, "ac_helix": None})
        return False

    # 타겟 체인과 일치하는 항목 우선 선택
    matched = None
    if target_chain:
        for s in structures:
            if s.get("chain", "").upper() == target_chain.upper():
                matched = s
                break

    if matched is None:
        matched = structures[0]  # 일치 없으면 첫 번째 사용

    data = {
        "structure_id": pdb_id,
        "dfg":          matched.get("DFG"),
        "ac_helix":     matched.get("aC_helix"),
    }

    insert_klifs_structure(data)
    return True


def fetch_klifs_for_structures(structures: list[dict]) -> int:
    """
    여러 구조에 대해 KLIFS 데이터를 일괄 수집합니다.
    이미 DB에 수집된 구조는 건너뜁니다.

    Parameters:
        structures (list[dict]): pdb_structures 행 목록
                                 (structure_id, chain_id 키 필요)

    Returns:
        int: 신규 수집된 건수
    """
    collected = 0
    for s in structures:
        sid   = s["structure_id"]
        chain = s.get("chain_id")

        # 이미 수집된 경우 skip
        if get_klifs_by_structure(sid) is not None:
            continue

        success = process_klifs(sid, target_chain=chain)
        if success:
            collected += 1
            print(f"[OK] KLIFS 수집: {sid} (chain={chain})")
        else:
            print(f"[INFO] KLIFS 미등록 (비키나아제): {sid}")

    return collected
