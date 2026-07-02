"""
ct_fetcher.py — ClinicalTrials.gov v2 로 약물별 임상시험 수집 + 진행상황 판정.

전략(요청 5 검토 결과): **사실은 구조화 파싱, 요약은 LLM**.
  - 진행상황 3분류(진행중/중단/승인완료)와 임상시험 목록은 CT.gov 구조화 필드로 결정론적 산출.
  - LLM 은 온디맨드 '임상 요약 서사'에만 사용(app 에서 버튼 클릭 시).

CT.gov v2: GET https://clinicaltrials.gov/api/v2/studies?query.intr={drug}
실행: python ct_fetcher.py [UNIPROT_ACC]
"""
from __future__ import annotations

import sys
import os
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import (
    get_clinical_drug_names,
    upsert_clinical_trials_bulk,
    update_compound_clinical_status,
    get_drug_trials,
)

CT_URL = "https://clinicaltrials.gov/api/v2/studies"
TIMEOUT = 20

_ACTIVE = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION",
           "NOT_YET_RECRUITING", "AVAILABLE"}
_STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED", "NO_LONGER_AVAILABLE"}


def _phase_str(phases: list[str]) -> str:
    if not phases:
        return ""
    return "/".join(p.replace("PHASE", "Phase ") for p in phases)


def fetch_trials_for_drug(name: str, max_n: int = 40) -> list[dict]:
    """약물명으로 CT.gov 임상시험 조회 → 정규화된 dict 목록."""
    params = {
        "query.intr": name, "pageSize": min(max_n, 100),
        "fields": ("NCTId,BriefTitle,Phase,OverallStatus,Condition,"
                   "StartDate,WhyStopped"),
    }
    try:
        r = requests.get(CT_URL, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        studies = r.json().get("studies", [])
    except Exception as e:
        print(f"[WARN] CT.gov 조회 실패 ({name}): {e}")
        return []
    out = []
    for s in studies[:max_n]:
        p = s.get("protocolSection", {})
        idm = p.get("identificationModule", {})
        st = p.get("statusModule", {})
        dm = p.get("designModule", {})
        cm = p.get("conditionsModule", {})
        out.append({
            "nct_id": idm.get("nctId"),
            "title": idm.get("briefTitle"),
            "phase": _phase_str(dm.get("phases") or []),
            "overall_status": st.get("overallStatus"),
            "conditions": ", ".join((cm.get("conditions") or [])[:4]),
            "why_stopped": st.get("whyStopped") or "",
            "start_date": (st.get("startDateStruct") or {}).get("date"),
        })
    return out


def derive_clinical_status(max_phase, trials: list[dict]) -> str:
    """진행상황 3분류: '승인 완료' | '진행중' | '중단'.

    - 승인 완료: ChEMBL max_phase == 4 (승인).
    - 진행중: 활성/모집중 임상이 있거나(임상 개발 지속) 미상시 임상단계 1~3.
    - 중단: 임상이 있으나 활성 임상 없고 중단(terminated/withdrawn/suspended) 존재.
    """
    try:
        mp = float(max_phase) if max_phase is not None else None
    except (TypeError, ValueError):
        mp = None
    if mp is not None and mp >= 4:
        return "승인 완료"
    statuses = {(t.get("overall_status") or "").upper() for t in trials}
    if statuses & _ACTIVE:
        return "진행중"
    if (statuses & _STOPPED) and not (statuses & _ACTIVE):
        # 활성 임상 없이 중단만 → 중단 (완료 임상만 있고 중단 없으면 진행중으로 봄)
        if not any(s == "COMPLETED" for s in statuses) or (statuses & _STOPPED):
            return "중단"
    if mp is not None and mp >= 1:
        return "진행중"
    return "진행중" if trials else "-"


def run(uniprot_acc: str = "P08581") -> dict:
    print(f"\n=== ClinicalTrials.gov 임상 수집: {uniprot_acc} ===")
    drugs = get_clinical_drug_names(uniprot_acc)
    print(f"[INFO] 임상 약물 {len(drugs)}개")
    n_trials = 0
    for i, d in enumerate(drugs):
        name = d.get("pref_name")
        cid = d.get("chembl_id")
        if not name:
            continue
        trials = fetch_trials_for_drug(name)
        for t in trials:
            t["chembl_id"] = cid
        upsert_clinical_trials_bulk(trials)
        status = derive_clinical_status(d.get("max_phase"), trials)
        update_compound_clinical_status(cid, status)
        n_trials += len(trials)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(drugs)} (임상 {n_trials}건)")
        time.sleep(0.2)
    print(f"[OK] 임상시험 {n_trials}건 · 약물 {len(drugs)}개 진행상황 판정")
    return {"drugs": len(drugs), "trials": n_trials}


def summarize_trials_llm(name: str, trials: list[dict], model: str = "claude-sonnet-4-6") -> str:
    """(온디맨드) 임상시험 목록을 사람이 읽기 좋은 한국어 서사로 종합 (LLM).

    구조화 데이터로는 드러나지 않는 패턴·중단사유·개발 궤적을 요약한다.
    """
    if not trials:
        return "임상시험 데이터가 없습니다."
    try:
        from paper_pipeline import _ensure_anthropic_key
        _ensure_anthropic_key()
        import anthropic
    except Exception as e:
        return f"(LLM 사용 불가: {e})"
    lines = []
    for t in trials[:40]:
        lines.append(f"- {t.get('nct_id')} | {t.get('phase')} | {t.get('overall_status')} | "
                     f"{t.get('conditions')}" +
                     (f" | 중단사유: {t.get('why_stopped')}" if t.get('why_stopped') else ""))
    prompt = (
        f"다음은 약물 '{name}' 의 ClinicalTrials.gov 임상시험 목록이다. "
        "이를 바탕으로 **임상 개발 진행 상황**을 한국어 3~5문장으로 종합하라. "
        "포함: 주요 적응증, 현재 활발한 임상 단계, 중단/실패가 있으면 그 사유·패턴, "
        "전반적 개발 궤적. 목록에 없는 사실은 지어내지 말 것.\n\n" + "\n".join(lines)
    )
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(model=model, max_tokens=800,
                                     messages=[{"role": "user", "content": prompt}])
        return next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "").strip()
    except Exception as e:
        return f"(요약 실패: {e})"


if __name__ == "__main__":
    acc = sys.argv[1] if len(sys.argv) > 1 else "P08581"
    print(run(acc))
