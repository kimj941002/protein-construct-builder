# uniprot_fetcher.py
# UniProt API를 이용해 단백질 기본 정보를 수집하는 모듈
# 검색어 정규화, API 호출, 결과 처리, DB 저장까지 담당합니다.

from __future__ import annotations
import json
import os
import requests
from config import UNIPROT_API, RCSB_SEARCH_API, SEQUENCES_DIR
from utils import api_call_with_retry, create_cached_session
from database import insert_protein, insert_domains_bulk, delete_domains_by_uniprot

# ─────────────────────────────────────────────
# 유전자 별칭(alias) → 표준 이름 매핑 사전
# 정규식 없이 exact-match(완전 일치) 방식으로만 변환합니다.
# 대소문자 구분 없이 비교하기 위해 키는 모두 대문자로 저장합니다.
# ─────────────────────────────────────────────
GENE_ALIASES = {
    # MET (c-MET, CMET 등)
    "C-MET":       "MET",
    "CMET":        "MET",
    "C MET":       "MET",
    # HER family
    "HER2":        "ERBB2",
    "HER3":        "ERBB3",
    "HER4":        "ERBB4",
    # VEGFR family
    "VEGFR1":      "FLT1",
    "VEGFR2":      "KDR",
    "VEGFR3":      "FLT4",
    # PDGFR family
    "PDGFR-ALPHA": "PDGFRA",
    "PDGFRA":      "PDGFRA",   # 이미 표준 이름 (그대로)
    "PDGFR-BETA":  "PDGFRB",
    "PDGFRB":      "PDGFRB",   # 이미 표준 이름 (그대로)
    "PDGFR-A":     "PDGFRA",
    "PDGFR-B":     "PDGFRB",
    # p53
    "P53":         "TP53",
}

# 모호한 별칭: 여러 유전자로 해석될 수 있어 자동 변환하지 않습니다.
AMBIGUOUS_ALIASES = {
    "FGFR": "FGFR1 / FGFR2 / FGFR3 / FGFR4 중 어느 것인지 명확히 입력해주세요.",
    "VEGFR": "VEGFR1(FLT1) / VEGFR2(KDR) / VEGFR3(FLT4) 중 어느 것인지 명확히 입력해주세요.",
    "PDGFR": "PDGFR-alpha(PDGFRA) / PDGFR-beta(PDGFRB) 중 어느 것인지 명확히 입력해주세요.",
    "IGF1R": None,  # None이면 그대로 검색
}


def normalize_gene_name(query: str) -> tuple[str, str | None]:
    """
    검색어를 표준 유전자 이름으로 변환합니다.
    정규식 없이 exact-match 사전 방식만 사용합니다.

    Parameters:
        query (str): 사용자가 입력한 검색어 (예: 'cMET', 'HER2', 'CDK2')

    Returns:
        (normalized_name, message) 형태의 튜플
        - normalized_name: 변환된 이름 (변환 불필요 시 원본 대문자)
        - message: 안내 메시지 (없으면 None)

    예시:
        'cMET'   → ('MET', None)
        'HER2'   → ('ERBB2', None)
        'CDK2'   → ('CDK2', None)   # C가 잘리면 안 됨!
        'FGFR'   → ('FGFR', "모호한 별칭 안내 메시지")
    """
    # 앞뒤 공백 제거 후 대문자로 변환 (사전 조회용)
    query_upper = query.strip().upper()

    # 1단계: 모호한 별칭 확인
    if query_upper in AMBIGUOUS_ALIASES:
        msg = AMBIGUOUS_ALIASES[query_upper]
        if msg:
            return (query.strip(), f"[안내] '{query}'는 모호한 별칭입니다. {msg}")
        # None이면 그대로 통과

    # 2단계: 별칭 사전에서 exact-match 조회
    if query_upper in GENE_ALIASES:
        converted = GENE_ALIASES[query_upper]
        print(f"[INFO] 검색어 정규화: '{query}' → '{converted}'")
        return (converted, None)

    # 3단계: 사전에 없으면 원본을 대문자로 반환 (예: CDK2 → CDK2)
    return (query_upper, None)


def search_uniprot(gene_name: str, session=None) -> list[dict]:
    """
    UniProt REST API로 단백질을 검색합니다.
    Human(organism_id:9606), Reviewed(Swiss-Prot) 항목만 검색합니다.

    Parameters:
        gene_name (str): 검색할 유전자 이름 (이미 정규화된 이름)
        session: CachedSession 또는 None (None이면 새 Session 생성)

    Returns:
        list[dict]: 검색된 UniProt 엔트리 목록 (없으면 빈 리스트)
    """
    params = {
        "query": f"gene_exact:{gene_name} AND organism_id:9606 AND reviewed:true",
        "format": "json",
        "fields": (
            "accession,gene_names,protein_name,organism_name,"
            "sequence,annotation_score,protein_existence,"
            "cc_function,cc_subcellular_location,ft_signal,ft_domain,"
            "xref_pdb"
        ),
        "size": 10,  # 최대 10개까지만 가져옴
    }

    print(f"[INFO] UniProt 검색 중: {gene_name}")
    result = api_call_with_retry(UNIPROT_API, params=params, session=session)

    if result is None:
        print(f"[WARN] UniProt API 응답 없음: {gene_name}")
        return []

    entries = result.get("results", [])
    print(f"[INFO] 검색 결과: {len(entries)}개")
    return entries


def select_best_entry(entries: list[dict]) -> dict | None:
    """
    여러 검색 결과 중 가장 적합한 항목을 선택합니다.

    선택 기준 (우선순위 순):
    1. annotationScore (높을수록 좋음)
    2. proteinExistence (1=experimental evidence > 5=predicted)
    3. sequenceLength (길수록 좋음 — 더 완전한 서열)

    Parameters:
        entries (list[dict]): UniProt 검색 결과 목록

    Returns:
        dict: 선택된 엔트리, 없으면 None
    """
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]

    # proteinExistence 숫자 변환 사전
    # UniProt API가 반환하는 값 예시: "1: Evidence at protein level"
    existence_rank = {
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    }

    def sort_key(entry):
        # annotationScore: 높을수록 좋으니 음수로 변환 (오름차순 정렬용)
        score = entry.get("annotationScore", 0)

        # proteinExistence: "1: Evidence at protein level" 형태에서 숫자 추출
        existence_str = str(entry.get("proteinExistence", "5"))
        existence_num = existence_rank.get(existence_str[0], 5)

        # sequenceLength: 높을수록 좋으니 음수로 변환
        seq_len = entry.get("sequence", {}).get("length", 0)

        return (score * -1, existence_num, seq_len * -1)

    sorted_entries = sorted(entries, key=sort_key)
    selected = sorted_entries[0]

    if len(entries) > 1:
        best_id = selected.get("primaryAccession", "?")
        print(f"[INFO] {len(entries)}개 결과 중 {best_id} 선택 (annotationScore 기준)")

    return selected


def extract_protein_data(entry: dict) -> dict:
    """
    UniProt 엔트리에서 필요한 정보를 추출하여 딕셔너리로 반환합니다.

    Parameters:
        entry (dict): UniProt API 응답의 단일 엔트리

    Returns:
        dict: proteins 테이블에 저장할 데이터
    """
    # UniProt ID
    uniprot_id = entry.get("primaryAccession", "")

    # 유전자 이름 (여러 개일 수 있음 — 첫 번째 우선)
    gene_names_data = entry.get("genes", [])
    gene_name = ""
    if gene_names_data:
        gene_name = gene_names_data[0].get("geneName", {}).get("value", "")

    # 단백질 이름
    protein_names = entry.get("proteinDescription", {})
    recommended = protein_names.get("recommendedName", {})
    protein_name = recommended.get("fullName", {}).get("value", "")
    if not protein_name:
        # recommendedName 없으면 submittedNames에서 시도
        submitted = protein_names.get("submittedNames", [])
        if submitted:
            protein_name = submitted[0].get("fullName", {}).get("value", "")

    # 생물종
    organism = entry.get("organism", {}).get("scientificName", "")

    # 서열 정보
    seq_data = entry.get("sequence", {})
    sequence = seq_data.get("value", "")
    sequence_length = seq_data.get("length", 0)

    # 기능 설명 (cc_function 주석)
    function_desc = ""
    comments = entry.get("comments", [])
    for comment in comments:
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts", [])
            if texts:
                function_desc = texts[0].get("value", "")
                break

    # 세포 내 위치
    subcellular_location = ""
    for comment in comments:
        if comment.get("commentType") == "SUBCELLULAR LOCATION":
            locations = comment.get("subcellularLocations", [])
            locs = []
            for loc in locations:
                loc_name = loc.get("location", {}).get("value", "")
                if loc_name:
                    locs.append(loc_name)
            subcellular_location = "; ".join(locs)
            break

    # 신호 펩타이드 범위
    signal_peptide = ""
    features = entry.get("features", [])
    for feat in features:
        if feat.get("type") == "Signal peptide":
            loc = feat.get("location", {})
            start = loc.get("start", {}).get("value", "")
            end = loc.get("end", {}).get("value", "")
            if start and end:
                signal_peptide = f"{start}-{end}"
            break

    # 도메인 정보 (JSON 형식으로 저장)
    domains = []
    for feat in features:
        if feat.get("type") == "Domain":
            loc = feat.get("location", {})
            start = loc.get("start", {}).get("value", "")
            end = loc.get("end", {}).get("value", "")
            desc = feat.get("description", "")
            if desc:
                domains.append({
                    "name": desc,
                    "start": start,
                    "end": end,
                })
    return {
        "uniprot_id":           uniprot_id,
        "gene_name":            gene_name,
        "protein_name":         protein_name,
        "organism":             organism,
        "sequence":             sequence,       # 파일 저장 후 제거됨 (fetch_protein 참고)
        "sequence_length":      sequence_length,
        "function_desc":        function_desc,
        "subcellular_location": subcellular_location,
        "signal_peptide":       signal_peptide,
        "_domains":             domains,        # DB 삽입 전 분리 (언더스코어 = 내부 전용)
    }


def get_pdb_ids_from_uniprot(entry: dict) -> list[str]:
    """
    UniProt 엔트리의 cross-reference에서 PDB ID 목록을 추출합니다.

    Parameters:
        entry (dict): UniProt API 응답의 단일 엔트리

    Returns:
        list[str]: PDB ID 목록 (예: ['2WGJ', '3DKC', ...])
    """
    pdb_ids = []
    xrefs = entry.get("uniProtKBCrossReferences", [])
    for xref in xrefs:
        if xref.get("database") == "PDB":
            pdb_id = xref.get("id", "")
            if pdb_id:
                pdb_ids.append(pdb_id.upper())
    return pdb_ids


def get_pdb_ids_from_rcsb(uniprot_id: str, session=None) -> list[str]:
    """
    RCSB Search API를 이용해 UniProt accession으로 PDB ID를 검색합니다.
    UniProt cross-reference에서 누락된 구조를 보완합니다.

    Parameters:
        uniprot_id (str): UniProt accession (예: P08581)
        session: CachedSession 또는 None

    Returns:
        list[str]: PDB ID 목록
    """
    # RCSB Search API 쿼리 (GraphQL-like JSON 형식)
    query_body = {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                "operator": "exact_match",
                "value": uniprot_id
            }
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {
                "start": 0,
                "rows": 10000  # 최대한 많이 가져옴
            }
        }
    }

    print(f"[INFO] RCSB Search API 조회 중: {uniprot_id}")
    try:
        use_session = session if session is not None else requests.Session()
        resp = use_session.post(
            RCSB_SEARCH_API,
            json=query_body,
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            result_set = data.get("result_set", [])
            pdb_ids = [r["identifier"].upper() for r in result_set]
            print(f"[INFO] RCSB에서 {len(pdb_ids)}개 PDB ID 수집")
            return pdb_ids
        else:
            print(f"[WARN] RCSB Search API 응답 코드: {resp.status_code}")
            return []
    except Exception as e:
        print(f"[WARN] RCSB Search API 오류: {e}")
        return []


def save_sequence_file(uniprot_id: str, gene_name: str, organism: str, sequence: str) -> str:
    """
    아미노산 서열을 FASTA 형식으로 sequences/ 폴더에 저장합니다.

    Parameters:
        uniprot_id (str): UniProt ID (파일명으로 사용)
        gene_name  (str): 유전자 이름 (헤더에 포함)
        organism   (str): 생물종 (헤더에 포함)
        sequence   (str): 단일 문자 아미노산 서열

    Returns:
        str: DB에 저장할 상대 경로 (예: "sequences/P08581.txt")

    FASTA 형식:
        >P08581 | MET | Homo sapiens
        MATGGRRGAAAAPLLVA...  (60자 단위 줄바꿈)
    """
    os.makedirs(SEQUENCES_DIR, exist_ok=True)

    filename = f"{uniprot_id}.txt"
    filepath = os.path.join(SEQUENCES_DIR, filename)
    rel_path = f"sequences/{filename}"

    header = f">{uniprot_id} | {gene_name or ''} | {organism or ''}"
    lines = [header] + [sequence[i:i + 60] for i in range(0, len(sequence), 60)]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[OK] 서열 파일 저장: {rel_path}")
    return rel_path


def load_sequence_from_file(sequence_path: str) -> str:
    """
    sequence_path (상대 경로)로부터 아미노산 서열을 읽어 반환합니다.

    Returns:
        str: 아미노산 서열 (없으면 빈 문자열)
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, sequence_path)
    if not os.path.isfile(full_path):
        return ""
    with open(full_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    return "".join(l for l in lines if not l.startswith(">"))


def fetch_protein(query: str, session=None) -> tuple[dict | None, list[str], str | None]:
    """
    단백질 이름을 받아 UniProt 정보를 수집하고 DB에 저장합니다.

    Parameters:
        query (str): 사용자 입력 검색어 (예: 'cMET', 'CDK2', 'HER2')
        session: CachedSession 또는 None

    Returns:
        (protein_data, pdb_ids, message) 형태의 튜플
        - protein_data: 수집된 단백질 딕셔너리 (실패 시 None)
        - pdb_ids: PDB ID 목록 (실패 시 빈 리스트)
        - message: 사용자에게 보여줄 안내 메시지 (없으면 None)
    """
    # 1단계: 검색어 정규화
    normalized, norm_msg = normalize_gene_name(query)
    if norm_msg and "[안내]" in norm_msg:
        # 모호한 별칭인 경우 사용자에게 안내 후 검색 계속 진행
        print(norm_msg)

    # 2단계: UniProt 검색
    entries = search_uniprot(normalized, session=session)

    if not entries:
        msg = f"'{normalized}' 검색 결과 없음. 유전자 이름을 확인해주세요."
        print(f"[WARN] {msg}")
        return (None, [], msg)

    # 3단계: 최적 엔트리 선택
    selected = select_best_entry(entries)
    if not selected:
        return (None, [], "항목 선택 실패")

    # 4단계: 데이터 추출
    raw_data   = extract_protein_data(selected)
    uniprot_id = raw_data["uniprot_id"]

    print(f"[OK] 선택된 단백질: {uniprot_id} ({raw_data['gene_name']})")
    print(f"     서열 길이: {raw_data['sequence_length']} aa")

    # 4a. 서열을 FASTA 파일로 저장
    sequence   = raw_data.pop("sequence", "")
    domains    = raw_data.pop("_domains", [])
    sequence_path = save_sequence_file(
        uniprot_id,
        raw_data["gene_name"],
        raw_data["organism"],
        sequence,
    )

    # 4b. DB 삽입용 dict 구성 (sequence 대신 sequence_path)
    protein_data = {**raw_data, "sequence_path": sequence_path}

    # 5단계: PDB ID 수집 (UniProt cross-ref + RCSB Search 합산)
    pdb_from_uniprot = get_pdb_ids_from_uniprot(selected)
    pdb_from_rcsb    = get_pdb_ids_from_rcsb(uniprot_id, session=session)

    # 중복 제거 (union)
    all_pdb_ids = list(set(pdb_from_uniprot) | set(pdb_from_rcsb))
    print(f"[OK] PDB 구조 수: UniProt {len(pdb_from_uniprot)}개 + RCSB 보완 → 총 {len(all_pdb_ids)}개")

    # 6단계: DB 저장
    insert_protein(protein_data)
    print(f"[OK] proteins 테이블에 저장 완료: {uniprot_id}")

    # 6a. 도메인을 protein_domains 테이블에 저장
    delete_domains_by_uniprot(uniprot_id)   # 재실행 시 중복 방지
    insert_domains_bulk(uniprot_id, domains)
    if domains:
        print(f"[OK] protein_domains 저장 완료: {len(domains)}개 도메인")

    return (protein_data, all_pdb_ids, norm_msg)


# ─────────────────────────────────────────────
# 직접 실행 시 테스트 (cMET 검색 예시)
# 터미널에서: python uniprot_fetcher.py
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    # 출력 인코딩을 UTF-8로 설정 (Windows 터미널 한글 깨짐 방지)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 50)
    print("Phase 2 테스트: cMET 검색")
    print("=" * 50)

    session = create_cached_session()
    protein_data, pdb_ids, message = fetch_protein("cMET", session=session)

    if protein_data:
        print()
        print("[결과]")
        print(f"  UniProt ID    : {protein_data['uniprot_id']}")
        print(f"  Gene Name     : {protein_data['gene_name']}")
        print(f"  Protein Name  : {protein_data['protein_name'][:60]}...")
        print(f"  Organism      : {protein_data['organism']}")
        print(f"  Sequence Len  : {protein_data['sequence_length']} aa")
        print(f"  Signal Peptide: {protein_data['signal_peptide']}")
        print(f"  PDB IDs ({len(pdb_ids)}개): {pdb_ids[:10]}{'...' if len(pdb_ids) > 10 else ''}")
    else:
        print(f"[FAIL] 검색 실패: {message}")

    print()
    print("=" * 50)
    print("정규화 테스트")
    print("=" * 50)
    test_cases = ['cMET', 'c-MET', 'CDK2', 'CREB1', 'p53', 'HER2', 'PDGFR-alpha', 'EGFR']
    for tc in test_cases:
        result, _ = normalize_gene_name(tc)
        print(f"  {tc:20s} -> {result}")
