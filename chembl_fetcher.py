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
from database import upsert_compound, upsert_bioactivity

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


# ── 화합물 기본정보 수집 ─────────────────────────────────────
def fetch_compound_info(chembl_id: str) -> dict | None:
    """ChEMBL ID로 화합물 기본 정보 조회."""
    url = f"{CHEMBL_BASE}/molecule/{chembl_id}?format=json"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] 화합물 조회 실패 ({chembl_id}): {e}")
    return None


# ── 메인 실행 ───────────────────────────────────────────────
def run(uniprot_acc: str = UNIPROT_ACC) -> dict:
    print(f"\n=== ChEMBL 수집: {uniprot_acc} ===")

    # 1. target ID 조회
    target_id = get_target_chembl_id(uniprot_acc)
    if not target_id:
        print("[ERROR] ChEMBL target ID 조회 실패. 종료.")
        return {"error": "target_lookup_failed"}
    print(f"[OK] Target: {target_id}")

    # 2. 활성 레코드 수집
    print("[INFO] 활성 데이터 수집 중...")
    activities = fetch_activities(target_id)
    print(f"[OK] {len(activities)} 레코드 수집됨")

    # 3. 화합물 집합 추출 + upsert
    chembl_ids = {a["molecule_chembl_id"] for a in activities if a.get("molecule_chembl_id")}
    print(f"[INFO] {len(chembl_ids)} 고유 화합물 처리 중...")

    compound_cache: dict[str, dict] = {}
    ok_compounds = 0
    for i, cid in enumerate(chembl_ids):
        info = fetch_compound_info(cid)
        if not info:
            continue
        cp = info.get("molecule_properties") or {}
        record = {
            "chembl_id":        cid,
            "pref_name":        info.get("pref_name"),
            "canonical_smiles": (info.get("molecule_structures") or {}).get("canonical_smiles"),
            "inchikey":         (info.get("molecule_structures") or {}).get("standard_inchi_key"),
            "max_phase":        info.get("max_phase"),
        }
        try:
            upsert_compound(record)
            compound_cache[cid] = record
            ok_compounds += 1
        except Exception as e:
            print(f"[WARN] compound upsert 실패 ({cid}): {e}")
        if (i + 1) % 50 == 0:
            print(f"  화합물 {i+1}/{len(chembl_ids)} 처리됨")
        time.sleep(0.1)
    print(f"[OK] {ok_compounds} 화합물 저장됨")

    # 4. 활성 레코드 upsert
    ok_bio = 0
    skip_bio = 0
    for a in activities:
        cid = a.get("molecule_chembl_id")
        if not cid or cid not in compound_cache:
            skip_bio += 1
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

        record = {
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
        }
        try:
            upsert_bioactivity(record)
            ok_bio += 1
        except Exception as e:
            print(f"[WARN] bioactivity upsert 실패: {e}")

    print(f"[OK] {ok_bio} 활성 레코드 저장 / {skip_bio} 건 화합물 없어 스킵")
    return {"compounds": ok_compounds, "bioactivities": ok_bio}


if __name__ == "__main__":
    result = run()
    print(f"\n완료: {result}")
