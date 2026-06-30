"""
chembl_fetcher.py — cMET (P08581) ChEMBL 활성 데이터 수집기

실행 방법:
  python chembl_fetcher.py

기능:
  1. P08581 UniProt 어세션으로 ChEMBL target ID 자동 조회
  2. 활성 데이터 (IC50/Ki/Kd 등) 페이지 단위 수집
  3. 단위 정규화: nM/µM/mM/M → value_nM_normalized [C1]
  4. compounds + bioactivities upsert (멱등)

ChEMBL API: https://www.ebi.ac.uk/chembl/api/data/  (REST, JSON, 무료)
타겟 UniProt: P08581 (MET proto-oncogene, human)
"""
from __future__ import annotations

import sys
import os
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import upsert_compounds_bulk, upsert_bioactivities_bulk

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
UNIPROT_ACC  = "P08581"
PAGE_SIZE    = 1000
REQUEST_TIMEOUT = 20


# ── 단위 → nM 정규화 [C1] ──────────────────────────────────
_UNIT_TO_NM: dict[str, float] = {
    "nM":    1.0,
    "nm":    1.0,
    "µM":    1_000.0,
    "uM":    1_000.0,
    "um":    1_000.0,
    "mM":    1_000_000.0,
    "mm":    1_000_000.0,
    "M":     1_000_000_000.0,
    "mol/L": 1_000_000_000.0,
}


def _normalize_nM(value: float | None, units: str | None) -> float | None:
    if value is None or units is None:
        return None
    factor = _UNIT_TO_NM.get(units.strip())
    if factor is None:
        return None   # 비농도 단위 (%, ug/mL, ratio 등) → null
    return value * factor


def _to_num(v) -> float | None:
    """ChEMBL 이 '2.0'·'4.0' 같은 문자열로 주는 수치를 float 로 안전 변환."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── ChEMBL target lookup ────────────────────────────────────
def get_target_chembl_id(uniprot_acc: str) -> str | None:
    """UniProt accession → ChEMBL target ChEMBL ID 조회."""
    url = (f"{CHEMBL_BASE}/target"
           f"?target_components__accession={uniprot_acc}"
           f"&target_type=SINGLE+PROTEIN&format=json&limit=5")
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        targets = resp.json().get("targets", [])
        for t in targets:
            if t.get("target_type") == "SINGLE PROTEIN":
                return t["target_chembl_id"]
    except Exception as e:
        print(f"[ERROR] ChEMBL target lookup 실패: {e}")
    return None


# ── 활성 데이터 페이지 수집 ──────────────────────────────────
def fetch_activities(target_chembl_id: str) -> list[dict]:
    """pChEMBL 값이 있는 활성 레코드 전체 수집."""
    all_records: list[dict] = []
    offset = 0
    url = (f"{CHEMBL_BASE}/activity"
           f"?target_chembl_id={target_chembl_id}"
           f"&pchembl_value__isnull=false"
           f"&format=json&limit={PAGE_SIZE}")

    while True:
        page_url = f"{url}&offset={offset}"
        try:
            resp = requests.get(page_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[ERROR] 활성 데이터 수집 실패 (offset={offset}): {e}")
            break

        activities = data.get("activities", [])
        if not activities:
            break
        all_records.extend(activities)
        total = data.get("page_meta", {}).get("total_count", len(all_records))
        print(f"  [{offset + len(activities)}/{total}] 수집 중...")
        offset += len(activities)
        if offset >= total:
            break
        time.sleep(0.3)  # 레이트리밋 방지

    return all_records


# ── 화합물 메타데이터 벌크 조회 ──────────────────────────────
def fetch_molecule_meta(chembl_ids: list[str]) -> dict[str, dict]:
    """molecule 엔드포인트를 50개씩 묶어 벌크 조회 → {cid: {inchikey, max_phase, pref_name}}.

    activity 응답에 없는 inchikey·max_phase 보강용. 개별 호출(수천 회) 대신 ~수십 회로 단축.
    """
    meta: dict[str, dict] = {}
    CHUNK = 50
    total_chunks = (len(chembl_ids) + CHUNK - 1) // CHUNK
    for ci in range(0, len(chembl_ids), CHUNK):
        chunk = chembl_ids[ci:ci + CHUNK]
        url = (f"{CHEMBL_BASE}/molecule"
               f"?molecule_chembl_id__in={','.join(chunk)}"
               f"&format=json&limit={CHUNK}")
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            mols = resp.json().get("molecules", [])
        except Exception as e:
            print(f"[WARN] molecule 벌크 조회 실패 (chunk {ci//CHUNK+1}): {e}")
            continue
        for m in mols:
            cid = m.get("molecule_chembl_id")
            if not cid:
                continue
            ms = m.get("molecule_structures") or {}
            meta[cid] = {
                "inchikey":  ms.get("standard_inchi_key"),
                "max_phase": _to_num(m.get("max_phase")),
                "pref_name": m.get("pref_name"),
            }
        print(f"  molecule 메타 {min(ci+CHUNK, len(chembl_ids))}/{len(chembl_ids)} (chunk {ci//CHUNK+1}/{total_chunks})")
        time.sleep(0.2)
    return meta


# ── 메인 실행 ───────────────────────────────────────────────
def run(uniprot_acc: str = UNIPROT_ACC) -> dict:
    print(f"\n=== ChEMBL 수집: {uniprot_acc} ===")

    # 1. target ID 조회
    target_id = get_target_chembl_id(uniprot_acc)
    if not target_id:
        print("[ERROR] ChEMBL target ID 조회 실패. 종료.")
        return {"error": "target_lookup_failed"}
    print(f"[OK] Target: {target_id}")

    # 2. 활성 레코드 수집 (activity 응답에 molecule_chembl_id·canonical_smiles 등 포함)
    print("[INFO] 활성 데이터 수집 중...")
    activities = fetch_activities(target_id)
    print(f"[OK] {len(activities)} 레코드 수집됨")
    if not activities:
        return {"compounds": 0, "bioactivities": 0}

    # 3. 화합물 — activity 필드로 1차 구성 (개별 호출 없음)
    compounds: dict[str, dict] = {}
    for a in activities:
        cid = a.get("molecule_chembl_id")
        if not cid or cid in compounds:
            continue
        compounds[cid] = {
            "chembl_id":        cid,
            "pref_name":        a.get("molecule_pref_name"),
            "canonical_smiles": a.get("canonical_smiles"),
            "inchikey":         None,    # molecule 벌크로 보강
            "max_phase":        None,
        }
    print(f"[INFO] {len(compounds)} 고유 화합물 — inchikey·max_phase 벌크 보강 중...")

    # 4. inchikey·max_phase 벌크 보강
    meta = fetch_molecule_meta(list(compounds.keys()))
    for cid, mt in meta.items():
        if cid in compounds:
            compounds[cid]["inchikey"] = mt.get("inchikey")
            compounds[cid]["max_phase"] = mt.get("max_phase")
            if not compounds[cid]["pref_name"]:
                compounds[cid]["pref_name"] = mt.get("pref_name")

    # 5. 화합물 벌크 upsert (단일 트랜잭션)
    try:
        upsert_compounds_bulk(list(compounds.values()))
        ok_compounds = len(compounds)
        print(f"[OK] {ok_compounds} 화합물 저장됨")
    except Exception as e:
        print(f"[ERROR] 화합물 벌크 저장 실패: {e}")
        return {"error": f"compound_bulk_failed: {str(e)[:160]}"}

    # 6. 활성 레코드 벌크 upsert (페이지 단위 청크)
    bio_records = []
    for a in activities:
        cid = a.get("molecule_chembl_id")
        if not cid or cid not in compounds:
            continue
        std_val = a.get("standard_value")
        std_units = a.get("standard_units")
        try:
            std_val_f = float(std_val) if std_val is not None else None
        except (TypeError, ValueError):
            std_val_f = None
        pch = a.get("pchembl_value")
        try:
            pch_f = float(pch) if pch is not None else None
        except (TypeError, ValueError):
            pch_f = None
        bio_records.append({
            "chembl_id":          cid,
            "uniprot_acc":        uniprot_acc,
            "standard_type":      a.get("standard_type"),
            "standard_value":     std_val_f,
            "standard_units":     std_units,
            "value_nM_normalized": _normalize_nM(std_val_f, std_units),
            "pchembl_value":      pch_f,
            "assay_chembl_id":    a.get("assay_chembl_id") or "",
            "assay_description":  a.get("assay_description"),
            "document_chembl_id": a.get("document_chembl_id"),
        })

    ok_bio = 0
    BATCH = 1000
    try:
        for bi in range(0, len(bio_records), BATCH):
            batch = bio_records[bi:bi + BATCH]
            upsert_bioactivities_bulk(batch)
            ok_bio += len(batch)
            print(f"  활성 저장 {ok_bio}/{len(bio_records)}")
    except Exception as e:
        print(f"[ERROR] 활성 벌크 저장 실패: {e}")
        return {"compounds": ok_compounds, "bioactivities": ok_bio,
                "error": f"bioactivity_bulk_failed: {str(e)[:160]}"}

    print(f"[OK] {ok_bio} 활성 레코드 저장")
    return {"compounds": ok_compounds, "bioactivities": ok_bio}


if __name__ == "__main__":
    result = run()
    print(f"\n완료: {result}")
