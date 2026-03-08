# complex_fetcher.py
# 구조의 리간드(소분자), 파트너 단백질, 올리고당(PTM) 정보를 수집하는 모듈
#
# [변경 이력]
# v1.1 (2026-03-01):
#   - fetch_partners_for_structure(): UniProt 매핑 없는 항체 단편 등 모든 폴리펩타이드 포함
#   - fetch_branched_linkage(): GraphQL struct_conn으로 올리고당 단백질 결합 위치 수집
#   - fetch_oligosaccharides_for_structure(): Branched entity (올리고당) 수집 추가
#   - process_complex(): 올리고당 처리 포함

from __future__ import annotations
import requests
import json
from config import (
    RCSB_NONPOLYMER_ENTITY_API,
    RCSB_POLYMER_ENTITY_API,
    RCSB_BRANCHED_ENTITY_API,
    RCSB_GRAPHQL_API,
)
from utils import api_call_with_retry, create_cached_session
from database import (
    insert_ligand, delete_ligands_by_structure,
    insert_partner_protein, insert_partner_chains_bulk, delete_partners_by_structure,
    insert_oligosaccharide, delete_oligosaccharides_by_structure,
)

# 물 분자 / 버퍼 이온 — 리간드로 저장하지 않음
EXCLUDE_COMP_IDS = {
    "HOH", "DOD", "H2O",   # 물
    "EDO", "PEG",           # PEG 계열
    "GOL", "PGE",           # 글리세롤 계열
}

# 의미 있는 리간드로 취급하지 않는 단일 원소 이온
METAL_IONS = {"MG", "CA", "ZN", "MN", "FE", "CU", "NA", "CL", "SO4", "PO4", "ACT"}


def fetch_chem_comp_info(comp_id: str) -> dict:
    """
    RCSB GraphQL API로 화학물질 정보를 가져옵니다.

    Parameters:
        comp_id (str): 화학물질 코드 (예: 'ATP', 'LKG')

    Returns:
        dict: name, formula, smiles (없으면 빈 문자열)
    """
    query = f"""
    {{
      chem_comp(comp_id: "{comp_id}") {{
        chem_comp {{
          id
          name
          formula
          type
        }}
        rcsb_chem_comp_descriptor {{
          SMILES
          SMILES_stereo
        }}
      }}
    }}
    """
    try:
        resp = requests.get(
            RCSB_GRAPHQL_API,
            params={"query": query},
            timeout=15
        )
        if resp.status_code != 200:
            return {"name": "", "formula": "", "smiles": ""}

        data = resp.json()
        cc_data = data.get("data", {}).get("chem_comp", {})
        if not cc_data:
            return {"name": "", "formula": "", "smiles": ""}

        cc = cc_data.get("chem_comp", {})
        descriptor = cc_data.get("rcsb_chem_comp_descriptor", {})

        # SMILES_stereo 우선, 없으면 SMILES 사용
        smiles = descriptor.get("SMILES_stereo") or descriptor.get("SMILES") or ""

        return {
            "name":    cc.get("name", ""),
            "formula": cc.get("formula", ""),
            "smiles":  smiles,
        }
    except Exception as e:
        print(f"[WARN] chem_comp GraphQL 오류 ({comp_id}): {e}")
        return {"name": "", "formula": "", "smiles": ""}


def fetch_ligands_for_structure(pdb_id: str, entry_data: dict, session) -> list[dict]:
    """
    PDB 구조에서 리간드 정보를 수집합니다.

    Parameters:
        pdb_id (str): PDB ID
        entry_data (dict): Entry API 응답 (non_polymer_entity_ids 포함)
        session: CachedSession

    Returns:
        list[dict]: 리간드 딕셔너리 목록
    """
    ids = entry_data.get("rcsb_entry_container_identifiers", {})
    nonpoly_ids = ids.get("non_polymer_entity_ids", []) or []

    if not nonpoly_ids:
        return []

    ligands = []
    for eid in nonpoly_ids:
        url = f"{RCSB_NONPOLYMER_ENTITY_API}/{pdb_id}/{eid}"
        data = api_call_with_retry(url, session=session)
        if not data:
            continue

        comp_id = data.get("pdbx_entity_nonpoly", {}).get("comp_id", "")
        entity_name = data.get("pdbx_entity_nonpoly", {}).get("name", "")

        # 물/버퍼 제외
        if comp_id.upper() in EXCLUDE_COMP_IDS:
            continue

        # Chem Comp 상세 정보 (2단계 조회)
        chem_info = fetch_chem_comp_info(comp_id)

        ligand = {
            "structure_id": pdb_id,
            "ligand_id":    comp_id,
            "ligand_name":  chem_info["name"] or entity_name,
            "formula":      chem_info["formula"],
            "smiles":       chem_info["smiles"],
            "ligand_type":  "small_molecule",
        }
        ligands.append(ligand)

    return ligands


def fetch_partners_for_structure(pdb_id: str, entry_data: dict,
                                  target_uniprot_id: str, session) -> list[dict]:
    """
    PDB 구조에서 타겟 단백질이 아닌 모든 폴리펩타이드 entity를 파트너로 수집합니다.
    UniProt 매핑이 없는 항체 단편(Fab 등)도 포함됩니다.

    변경사항 (v1.1):
    - 기존: UniProt 매핑 있는 entity만 포함 (항체 단편 누락)
    - 신규: 타겟 UniProt이 없는 모든 polypeptide entity 포함
    - 새 필드: entity_id, partner_chains, sequence_length, organism

    Parameters:
        pdb_id (str): PDB ID
        entry_data (dict): Entry API 응답
        target_uniprot_id (str): 타겟 단백질의 UniProt ID
        session: CachedSession

    Returns:
        list[dict]: 파트너 단백질 딕셔너리 목록
    """
    ids = entry_data.get("rcsb_entry_container_identifiers", {})
    polymer_entity_ids = ids.get("polymer_entity_ids", []) or []

    partners = []
    for eid in polymer_entity_ids:
        url = f"{RCSB_POLYMER_ENTITY_API}/{pdb_id}/{eid}"
        entity_data = api_call_with_retry(url, session=session)
        if not entity_data:
            continue

        # 폴리머 타입 확인 (단백질만)
        poly_type = entity_data.get("entity_poly", {}).get("type", "")
        if "polypeptide" not in poly_type.lower():
            continue

        # 이 entity의 UniProt ID 목록
        ref_ids = entity_data.get(
            "rcsb_polymer_entity_container_identifiers", {}
        ).get("reference_sequence_identifiers", []) or []

        uniprot_ids = [
            r.get("database_accession", "")
            for r in ref_ids
            if r.get("database_name") == "UniProt"
        ]

        # 타겟 UniProt이 포함된 entity → 스킵 (이것이 타겟 단백질)
        if target_uniprot_id in uniprot_ids:
            continue

        # 파트너의 UniProt (있으면 첫 번째, 없으면 빈 문자열)
        partner_uniprot = uniprot_ids[0] if uniprot_ids else ""

        # 분자 이름 (pdbx_description 우선, 없으면 common name)
        partner_gene = (
            entity_data.get("rcsb_polymer_entity", {}).get("pdbx_description", "")
            or (entity_data.get("rcsb_polymer_entity_name_com", [{}]) or [{}])[0].get("name", "")
        )

        # 모든 체인 ID
        chain_ids = entity_data.get(
            "rcsb_polymer_entity_container_identifiers", {}
        ).get("auth_asym_ids", []) or []

        # 서열 길이
        sequence_length = entity_data.get("entity_poly", {}).get(
            "rcsb_sample_sequence_length"
        )

        # 소스 생물종 (organism)
        src_org = entity_data.get("rcsb_entity_source_organism", []) or []
        organism = src_org[0].get("scientific_name") if src_org else None

        # 발현 시스템 (host organism 우선, 없으면 source organism)
        host_org = entity_data.get("rcsb_entity_host_organism", []) or []
        expression_system = (
            host_org[0].get("scientific_name") if host_org else organism
        )

        # 잔기 범위 (UniProt 번호 기준, UniProt 있는 경우만)
        residue_range = None
        if partner_uniprot:
            align_list = entity_data.get("rcsb_polymer_entity_align", []) or []
            if align_list:
                aligned_regions = align_list[0].get("aligned_regions", []) or []
                if aligned_regions:
                    starts = [r.get("ref_beg_seq_id", 0) for r in aligned_regions]
                    ends = [
                        r.get("ref_beg_seq_id", 0) + r.get("length", 0) - 1
                        for r in aligned_regions
                    ]
                    residue_range = f"{min(starts)}-{max(ends)}"

        partners.append({
            "structure_id":              pdb_id,
            "entity_id":                 str(eid),
            "partner_uniprot_id":        partner_uniprot,
            "partner_gene_name":         partner_gene,
            "partner_chain_id":          chain_ids[0] if chain_ids else None,
            "_chains_list":              chain_ids,   # 내부 전용 — partner_protein_chains 삽입에 사용
            "sequence_length":           sequence_length,
            "organism":                  organism,
            "partner_residue_range":     residue_range,
            "partner_expression_system": expression_system,
        })

    return partners


def fetch_branched_linkage(pdb_id: str, branched_auth_chains: set) -> dict:
    """
    RCSB GraphQL struct_conn에서 branched entity(올리고당)의 단백질 결합 위치를 가져옵니다.
    conn_type_id='covale' 인 레코드 중 한 쪽이 branched chain인 것을 필터링합니다.

    Parameters:
        pdb_id (str): PDB ID
        branched_auth_chains (set): branched entity의 auth_asym_id 집합

    Returns:
        dict: {branched_chain_id: {"linked_chain": str, "linked_position": int, "linked_residue": str}}
    """
    if not branched_auth_chains:
        return {}

    query = f"""
    {{
      entry(entry_id: "{pdb_id}") {{
        struct_conn {{
          conn_type_id
          ptnr1_auth_asym_id
          ptnr1_auth_seq_id
          ptnr1_label_comp_id
          ptnr2_auth_asym_id
          ptnr2_auth_seq_id
          ptnr2_label_comp_id
        }}
      }}
    }}
    """
    try:
        resp = requests.get(
            RCSB_GRAPHQL_API,
            params={"query": query},
            timeout=15
        )
        if resp.status_code != 200:
            return {}

        data = resp.json()
        conns = (
            data.get("data", {})
                .get("entry", {})
                .get("struct_conn", [])
            or []
        )

        result = {}
        for conn in conns:
            if conn.get("conn_type_id") != "covale":
                continue

            chain1 = conn.get("ptnr1_auth_asym_id") or ""
            chain2 = conn.get("ptnr2_auth_asym_id") or ""

            # 한쪽이 branched chain이고 다른 쪽이 단백질 chain인 경우
            if chain1 in branched_auth_chains and chain2 not in branched_auth_chains:
                branched_chain = chain1
                linked_chain   = chain2
                linked_pos_raw = conn.get("ptnr2_auth_seq_id")
                linked_residue = conn.get("ptnr2_label_comp_id", "")
            elif chain2 in branched_auth_chains and chain1 not in branched_auth_chains:
                branched_chain = chain2
                linked_chain   = chain1
                linked_pos_raw = conn.get("ptnr1_auth_seq_id")
                linked_residue = conn.get("ptnr1_label_comp_id", "")
            else:
                continue

            # 이미 등록된 chain은 첫 번째 결합만 사용
            if branched_chain in result:
                continue

            try:
                linked_position = int(linked_pos_raw) if linked_pos_raw is not None else None
            except (ValueError, TypeError):
                linked_position = None

            result[branched_chain] = {
                "linked_chain":    linked_chain,
                "linked_position": linked_position,
                "linked_residue":  linked_residue,
            }

        return result

    except Exception as e:
        print(f"[WARN] {pdb_id} struct_conn GraphQL 오류: {e}")
        return {}


def fetch_oligosaccharides_for_structure(pdb_id: str, entry_data: dict,
                                          session) -> list[dict]:
    """
    PDB 구조에서 올리고당류(Branched Entity) 정보를 수집합니다.
    각 체인(인스턴스)별로 단백질 결합 위치(chain, position, residue)를 포함합니다.

    Parameters:
        pdb_id (str): PDB ID
        entry_data (dict): Entry API 응답
        session: CachedSession

    Returns:
        list[dict]: 올리고당 딕셔너리 목록 (체인 인스턴스 단위)
    """
    ids = entry_data.get("rcsb_entry_container_identifiers", {})
    branched_ids = ids.get("branched_entity_ids", []) or []

    if not branched_ids:
        return []

    # 1단계: 각 branched entity의 이름과 체인 수집
    entity_info = {}      # {eid_str: {name, auth_chains}}
    all_branched_chains = set()

    for eid in branched_ids:
        url = f"{RCSB_BRANCHED_ENTITY_API}/{pdb_id}/{eid}"
        data = api_call_with_retry(url, session=session)
        if not data:
            continue

        # 이름: rcsb_branched_entity.pdbx_description 우선
        name = (
            (data.get("rcsb_branched_entity") or {}).get("pdbx_description")
            or (data.get("pdbx_entity_branch") or {}).get("type")
            or "Oligosaccharide"
        )

        auth_chains = (
            (data.get("rcsb_branched_entity_container_identifiers") or {})
            .get("auth_asym_ids", [])
            or []
        )

        entity_info[str(eid)] = {"name": name, "auth_chains": auth_chains}
        all_branched_chains.update(auth_chains)

    if not all_branched_chains:
        return []

    # 2단계: 한 번의 GraphQL 호출로 모든 branched chain의 결합 위치 수집
    linkage_map = fetch_branched_linkage(pdb_id, all_branched_chains)

    # 3단계: 체인 인스턴스별 레코드 생성
    oligos = []
    for eid_str, info in entity_info.items():
        name = info["name"]
        for chain in info["auth_chains"]:
            link = linkage_map.get(chain, {})
            oligos.append({
                "structure_id":    pdb_id,
                "entity_id":       eid_str,
                "name":            name,
                "chain_id":        chain,
                "linked_chain":    link.get("linked_chain"),
                "linked_position": link.get("linked_position"),
                "linked_residue":  link.get("linked_residue"),
            })

    return oligos


def process_complex(pdb_id: str, target_uniprot_id: str, entry_data: dict,
                    session=None) -> tuple[list[dict], list[dict], list[dict]]:
    """
    단일 PDB 구조의 복합체 정보(리간드 + 파트너 단백질 + 올리고당)를 수집하고 DB에 저장합니다.

    Parameters:
        pdb_id (str): PDB ID
        target_uniprot_id (str): 타겟 단백질 UniProt ID
        entry_data (dict): 이미 가져온 Entry API 응답
        session: CachedSession

    Returns:
        (ligands_list, partners_list, oligos_list) 튜플
    """
    if session is None:
        session = create_cached_session()

    # 기존 데이터 삭제 후 재삽입 (재실행 시 중복 방지)
    delete_ligands_by_structure(pdb_id)
    delete_partners_by_structure(pdb_id)
    delete_oligosaccharides_by_structure(pdb_id)

    # 리간드 수집 + DB 저장
    ligands = fetch_ligands_for_structure(pdb_id, entry_data, session)
    for lig in ligands:
        insert_ligand(lig)

    # 파트너 단백질 수집 + DB 저장
    partners = fetch_partners_for_structure(pdb_id, entry_data, target_uniprot_id, session)
    for partner in partners:
        chains_list = partner.pop("_chains_list", [])   # 내부 키 분리
        partner_id  = insert_partner_protein(partner)   # 삽입 후 id 반환
        insert_partner_chains_bulk(partner_id, chains_list)

    # 올리고당(PTM) 수집 + DB 저장
    oligos = fetch_oligosaccharides_for_structure(pdb_id, entry_data, session)
    for oligo in oligos:
        insert_oligosaccharide(oligo)

    return ligands, partners, oligos


# ─────────────────────────────────────────────
# 직접 실행 시 테스트
# 터미널에서: python complex_fetcher.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    from config import RCSB_ENTRY_API
    from utils import api_call_with_retry

    TARGET_UNIPROT = "P04626"  # HER2 (ERBB2)
    TEST_PDB_IDS = ["7MN6"]

    print("=" * 50)
    print("complex_fetcher v1.1 테스트 (HER2 복합체)")
    print("=" * 50)

    session = create_cached_session()

    for pdb_id in TEST_PDB_IDS:
        print(f"\n[처리] {pdb_id}")
        entry_data = api_call_with_retry(f"{RCSB_ENTRY_API}/{pdb_id}", session=session)
        if not entry_data:
            print(f"  [WARN] {pdb_id} Entry API 응답 없음")
            continue

        ligands, partners, oligos = process_complex(
            pdb_id, TARGET_UNIPROT, entry_data, session
        )

        if partners:
            print(f"  파트너 단백질 {len(partners)}개:")
            for p in partners:
                print(f"    - Entity {p['entity_id']}: {p['partner_gene_name']} "
                      f"| chains={json.loads(p['partner_chains'])} "
                      f"| UniProt={p['partner_uniprot_id'] or '없음'}")
        else:
            print("  파트너 단백질: 없음")

        if oligos:
            print(f"  올리고당 {len(oligos)}개:")
            for o in oligos:
                print(f"    - {o['name']} chain={o['chains']} "
                      f"-> {o['linked_chain']}:{o['linked_position']} ({o['linked_residue']})")
        else:
            print("  올리고당: 없음")
