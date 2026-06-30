"""
unichem_fetcher.py — PDB 리간드(CCD) ↔ ChEMBL 화합물 매핑 (EBI UniChem)

PDB 구조의 결합 리간드(CCD code)를 ChEMBL ID로 교차참조해 ligand_chembl_map 에 캐시한다.
이것이 "PDB 구조 ↔ 약물(ChEMBL 활성/임상)" 연결의 다리다.

UniChem v1 API: POST https://www.ebi.ac.uk/unichem/api/v1/compounds
  body: {"type":"sourceID", "compound":"<CCD>", "sourceID":3}   (src 3 = PDBe)
  → response.compounds[].sources[] 중 shortName=='chembl' 의 compoundId

매핑 없으면 chembl_id=NULL sentinel 로 저장해 재조회를 막는다(정직한 미연결).
실행: python unichem_fetcher.py [UNIPROT_ACC]
"""
from __future__ import annotations

import sys
import os
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_config import get_engine
from sqlalchemy import text

UNICHEM_URL = "https://www.ebi.ac.uk/unichem/api/v1/compounds"
PDBE_SRC_ID = 3
TIMEOUT = 20

# 명백한 결정학 첨가물/완충제 — UniChem 조회 스킵 (약물 아님)
_SKIP_LIGANDS = {
    "HOH", "NAG", "GOL", "SO4", "EDO", "PEG", "DMS", "PO4", "CL", "NA", "MG",
    "ZN", "CA", "K", "ACT", "FMT", "IPA", "MPD", "BME", "TRS", "EPE", "MES",
    "PG4", "PGE", "1PE", "BMA", "MAN", "FUC", "NDG", "SIA", "GAL", "BGC",
    "IOD", "BR", "FLC", "CIT", "TLA", "MLI", "SCN", "AZI", "NO3", "CO3",
}


def map_ccd_to_chembl(ccd: str) -> str | None:
    """단일 CCD code → ChEMBL ID (없으면 None)."""
    try:
        r = requests.post(UNICHEM_URL,
                          json={"type": "sourceID", "compound": ccd, "sourceID": PDBE_SRC_ID},
                          timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        for comp in r.json().get("compounds", []):
            for s in comp.get("sources", []):
                if s.get("shortName") == "chembl" or s.get("id") == 1:
                    return s.get("compoundId")
    except Exception as e:
        print(f"[WARN] UniChem 조회 실패 ({ccd}): {e}")
    return None


def get_unmapped_ligands(uniprot_acc: str) -> list[str]:
    """해당 단백질 구조의 리간드 중 아직 매핑 시도 안 한 CCD 목록."""
    with get_engine().connect() as c:
        rows = c.execute(text("""
            SELECT DISTINCT l.ligand_id
            FROM ligands l
            JOIN pdb_structures p ON l.structure_id = p.structure_id
            WHERE p.uniprot_id = :uid
              AND l.ligand_id IS NOT NULL
              AND l.ligand_id NOT IN (SELECT ligand_id FROM ligand_chembl_map)
        """), {"uid": uniprot_acc}).all()
    return [r[0] for r in rows]


def _save_mapping(ligand_id: str, chembl_id: str | None):
    with get_engine().begin() as c:
        c.execute(text("""
            INSERT INTO ligand_chembl_map (ligand_id, chembl_id, source, checked_at)
            VALUES (:lid, :cid, 'unichem', now())
            ON CONFLICT (ligand_id) DO UPDATE SET
                chembl_id = EXCLUDED.chembl_id, checked_at = now()
        """), {"lid": ligand_id, "cid": chembl_id})


def run(uniprot_acc: str = "P08581") -> dict:
    print(f"\n=== UniChem 리간드→ChEMBL 매핑: {uniprot_acc} ===")
    ligands = get_unmapped_ligands(uniprot_acc)
    print(f"[INFO] 미매핑 리간드 {len(ligands)}개")
    mapped = 0
    skipped = 0
    for i, ccd in enumerate(ligands):
        if ccd.upper() in _SKIP_LIGANDS:
            _save_mapping(ccd, None)   # 첨가물은 NULL sentinel
            skipped += 1
            continue
        chembl = map_ccd_to_chembl(ccd)
        _save_mapping(ccd, chembl)
        if chembl:
            mapped += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(ligands)} 처리 (매핑 {mapped})")
        time.sleep(0.15)
    print(f"[OK] 매핑됨 {mapped} / 첨가물 스킵 {skipped} / 총 {len(ligands)}")
    return {"mapped": mapped, "skipped": skipped, "total": len(ligands)}


if __name__ == "__main__":
    acc = sys.argv[1] if len(sys.argv) > 1 else "P08581"
    print(run(acc))
