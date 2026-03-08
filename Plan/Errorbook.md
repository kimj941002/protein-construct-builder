# Errorbook — 오류 누적 기록
> 코드 생성 및 실행 시 미리 참고하여 오류를 방지하기 위한 문서입니다.

---

## ERROR-001: UnicodeEncodeError (이모지 출력)

**발생 Phase:** Phase 1
**발생 파일:** `database.py` (line 109)
**발생 일시:** 2026-02-28

### 오류 메시지
```
UnicodeEncodeError: 'cp949' codec can't encode character '\u2705' in position 0: illegal multibyte sequence
```

### 원인
Windows 터미널(cmd/PowerShell)의 기본 인코딩은 **cp949** (한국어 Windows)이며, UTF-8 이모지 문자(✅ \u2705, ⚠️, ❌ 등)를 출력할 수 없음.
Python 소스 파일은 UTF-8로 저장되어 있어 이모지를 포함할 수 있지만, 터미널 출력 시 cp949로 인코딩하려다 실패함.

### 해결 방법
`print()` 문에서 이모지를 ASCII 대체 텍스트로 교체:
| 이모지 | 대체 텍스트 |
|--------|-----------|
| ✅ | `[OK]` |
| ⚠️ | `[WARN]` |
| ❌ | `[ERR]` |

또는 실행 시 환경변수 설정으로 UTF-8 출력 강제:
```bash
PYTHONIOENCODING=utf-8 python script.py
```

### 예방 규칙
> **앞으로 생성하는 모든 Python 파일의 `print()` 문에서 이모지를 사용하지 않는다.**
> 터미널 출력에는 `[OK]`, `[WARN]`, `[ERR]`, `[INFO]` 등 ASCII 텍스트만 사용.
> 이모지는 Streamlit UI (`st.write`, `st.success` 등)에서만 사용 가능.

---

## ERROR-002: RCSB Chem Comp REST API 404

**발생 Phase:** Phase 3
**발생 파일:** `complex_fetcher.py`
**발생 일시:** 2026-02-28

### 오류 내용
```
GET https://data.rcsb.org/rest/v1/core/chem_comp/{comp_id}
→ 404 {"status":404,"message":"HTTP 404 Not Found"}
```
LKG, MG 등 일부 comp_id에서 REST API가 404를 반환함.

### 원인
RCSB REST API의 `/core/chem_comp/{comp_id}` 엔드포인트는 모든 comp_id를 지원하지 않음.

### 해결 방법
**GraphQL API** 사용:
```python
query = f'{{chem_comp(comp_id: "{comp_id}") {{chem_comp{{id name formula type}} rcsb_chem_comp_descriptor{{SMILES SMILES_stereo}}}}}}'
resp = requests.get("https://data.rcsb.org/graphql", params={"query": query})
```
GraphQL은 모든 comp_id를 지원하며 `SMILES`, `SMILES_stereo` 필드를 직접 반환함.

### GraphQL 올바른 필드명
- `rcsb_chem_comp_descriptor.SMILES` (직접 접근)
- `rcsb_chem_comp_descriptor.SMILES_stereo`
- 주의: REST API의 `type`/`descriptor` 배열 구조가 아님!

### 예방 규칙
> **앞으로 Chem Comp 정보 조회 시 REST `/core/chem_comp/` 대신 GraphQL API를 사용한다.**

---

## ERROR-003: KLIFS API 엔드포인트 및 필드명 불일치

**발생 파일:** `klifs_fetcher.py`
**발생 일시:** 2026-03-01

### 오류 내용
KLIFS API가 400을 반환하고 모든 PDB가 "비키나아제 미등록"으로 처리됨.

### 원인
1. **잘못된 엔드포인트**: `/structures/pdb_complexes` → 존재하지 않는 경로 (400 반환)
2. **필드명 불일치**: 올바른 엔드포인트의 응답 키가 다름

### 해결 방법
```python
# 올바른 엔드포인트
url = "https://klifs.net/api/structures_pdb_list"
# params={"pdb-codes": pdb_id}

# 올바른 필드명
matched.get("DFG")       # DFG 형태
matched.get("aC_helix")  # αC Helix (구 코드: "ac_helix" → 오류)
```

### 예방 규칙
> **KLIFS API 엔드포인트는 `/structures_pdb_list` (언더스코어, 슬래시 없음).**
> 응답 필드명: `DFG`, `aC_helix`, `quality_score` (대소문자 정확히 일치).
> swagger.json 확인 URL: `https://klifs.net/swagger/swagger.json`

---

## ERROR-004: SQLite ON CONFLICT excluded 버그

**발생 파일:** `database.py` — `upsert_paper_analysis()`
**발생 일시:** 2026-03-01

### 오류 내용
분석 완료(`status=completed`) 후 DB에서 모든 분석 컬럼이 NULL로 저장됨.

### 원인
```sql
-- 잘못된 코드: INSERT에 3개 컬럼만 지정
INSERT INTO paper_analysis (structure_id, pdf_path, status)
VALUES (:structure_id, :pdf_path, :status)
ON CONFLICT(structure_id) DO UPDATE SET
    paper_topic = excluded.paper_topic  -- excluded는 INSERT 컬럼만 가짐 → NULL!
```
SQLite `excluded`는 **INSERT에 명시된 컬럼만** 값을 가짐. 나머지는 NULL.

### 해결 방법
INSERT 컬럼 목록에 **모든 컬럼 포함**:
```sql
INSERT INTO paper_analysis (structure_id, pdf_path, status, raw_text, analyzed_at)
VALUES (:structure_id, :pdf_path, :status, :raw_text, :analyzed_at)
ON CONFLICT(structure_id) DO UPDATE SET
    raw_text = excluded.raw_text  -- 이제 올바른 값 참조
```

### 예방 규칙
> **ON CONFLICT DO UPDATE에서 `excluded.컬럼`을 사용할 때, 해당 컬럼이 반드시 INSERT 컬럼 목록에 포함되어야 한다.**

---

## ERROR-005: st.rerun() 후 Ag-Grid 선택 초기화

**발생 파일:** `app.py`
**발생 일시:** 2026-03-01

### 오류 내용
논문 분석 완료 후 `st.rerun()` 호출 시 결과가 표시되지 않고 사라짐.

### 원인
`st.rerun()` → 페이지 전체 재실행 → Ag-Grid 선택 상태 초기화 → `selected_rows` 비어있음 → `st.stop()` 실행 → 상세 패널(분석 결과 포함) 렌더링 안 됨.

### 해결 방법
`st.rerun()` 제거. 분석 완료 후 로컬 변수를 직접 갱신하여 즉시 결과 표시:
```python
# st.rerun() 제거
pa     = get_paper_analysis(selected_pdb) or {}
status = "completed"
st.success("분석 완료!")
# 이후 코드에서 pa 변수로 결과 바로 렌더링
```

### 예방 규칙
> **Ag-Grid 선택 상태에 의존하는 상세 패널 내부에서 `st.rerun()`을 호출하지 않는다.**
> 로컬 변수 갱신으로 대체 가능한 경우 `st.rerun()` 불필요.

---

## ERROR-006: Claude API "Could not process PDF" (400)

**발생 파일:** `app.py` — `_run_paper_analysis()`
**발생 일시:** 2026-03-01

### 오류 내용
```
Error code: 400 - {'error': {'type': 'invalid_request_error', 'message': 'Could not process PDF'}}
```

### 원인
- PDF 파일이 너무 크거나 (base64 인코딩 시 ~33% 증가)
- 출판사 DRM/암호화 적용
- 스캔 전용 PDF (텍스트 레이어 없음)

### 해결 방법
1차 PDF 문서 직접 전송 실패 시 `pypdf`로 텍스트 추출 후 텍스트로 재전송:
```python
try:
    msg = client.messages.create(..., content=[{"type": "document", ...}])
except Exception as e:
    if "Could not process PDF" in str(e):
        extracted = _extract_pdf_text(pdf_path)  # pypdf 사용
        msg = client.messages.create(..., content=f"[논문 텍스트]\n{extracted}\n{prompt}")
```

### 예방 규칙
> **Anthropic PDF document API는 대용량/암호화/스캔 PDF에서 실패할 수 있다.**
> 항상 `pypdf` 텍스트 추출 fallback을 함께 구현한다.

---

## ERROR-007: normalize_gene_name 정규식 버그

**발생 파일:** `uniprot_fetcher.py` — `normalize_gene_name()`
**출처:** Feedback_v1.3.md (Critical C-1)
**발생 일시:** 2026-02-28 (설계 단계)

### 오류 내용
정규식 `r'^[cp]-?'`에서 `-?` (하이픈 선택적)로 인해 `c` 또는 `p`로 시작하는 모든 gene name의 첫 글자가 제거됨.

| 입력 | 잘못된 결과 | 올바른 결과 |
|------|-----------|-----------|
| `"CREB1"` | `"REB1"` | `"CREB1"` |
| `"CDK2"` | `"DK2"` | `"CDK2"` |
| `"p53"` | `"53"` | `"TP53"` |
| `"PDGFRB"` | `"DGRFB"` | `"PDGFRB"` |

### 원인
`re.sub(r'^[cp]-?', '', ...)` — `-?`는 하이픈을 optional로 만들어 `c-` 뿐 아니라 `c`(하이픈 없음)도 제거 대상이 됨.

### 해결 방법
```python
# 안전한 방식: 정규식 대신 explicit GENE_ALIASES whitelist 사용
GENE_ALIASES = {
    'C-MET': 'MET', 'CMET': 'MET',
    'P-EGFR': 'EGFR', 'PEGFR': 'EGFR',
    'HER2': 'ERBB2', 'HER3': 'ERBB3', 'HER4': 'ERBB4',
    'VEGFR1': 'FLT1', 'VEGFR2': 'KDR', 'VEGFR3': 'FLT4',
    'PDGFR-ALPHA': 'PDGFRA', 'PDGFR-BETA': 'PDGFRB',
    # 'FGFR': 'FGFR1' ← 제거: 모호한 별칭, FGFR1~4 중 하나를 임의 선택하는 문제
}

def normalize_gene_name(user_input):
    name = user_input.strip().upper().replace('-', '')
    return GENE_ALIASES.get(name, user_input.strip().upper())
```

### 예방 규칙
> **gene name 정규화에 `^[cp]-?` 같은 선택적 문자 제거 정규식을 사용하지 않는다.**
> GENE_ALIASES dict를 사용하는 explicit whitelist 방식만 사용한다.
> 모호한 별칭 (FGFR → FGFR1처럼 family를 특정 gene으로 임의 매핑)은 aliases에 등록하지 않는다.

---

## ERROR-008: requests-cache + ThreadPoolExecutor 스레드 안전성 결함

**발생 파일:** `pdb_fetcher.py`
**출처:** Feedback_v1.3.md (Critical C-2)
**발생 일시:** 2026-02-28 (설계 단계)

### 오류 내용
```
InterfaceError: bad parameter or other API misuse  (Python 3.12+)
# 또는 segfault (GitHub issue #1008, #845)
```

### 원인
`requests_cache.install_cache()` 전역 패치는 단일 SQLite 연결을 공유함.
`ThreadPoolExecutor`에서 여러 스레드가 동시에 이 연결에 접근하면 SQLite의 스레드 제한에 위반.

### 해결 방법
각 스레드 함수 내부에서 `CachedSession`을 독립 생성:
```python
def fetch_single_pdb(pdb_id):
    with requests_cache.CachedSession(
        'protein_api_cache',
        expire_after=604800,
        allowable_codes=(200,)   # 429, 500 등은 캐싱 방지
    ) as session:
        resp = session.get(f"{RCSB_ENTRY_API}/{pdb_id}", timeout=30)
        ...
```

### 예방 규칙
> **`requests_cache.install_cache()` 전역 패치와 `ThreadPoolExecutor` 병렬 처리를 함께 사용하지 않는다.**
> 멀티스레드 환경에서는 반드시 각 스레드 함수 내부에서 `CachedSession`을 개별 생성한다.
> `allowable_codes=(200,)` 항상 명시: rate limit(429) 응답이 캐싱되면 재시도 시에도 캐시된 429가 반환됨.

---

## ERROR-009: DOI 필드명 대소문자 오류

**발생 파일:** `pdb_fetcher.py`
**출처:** Feedback_v1.3.md (Moderate M-1)
**발생 일시:** 2026-02-28 (설계 단계)

### 오류 내용
RCSB Entry API 응답에서 DOI를 찾지 못해 항상 '해당없음'으로 저장됨.

### 원인
계획서에 `rcsb_primary_citation.pdbx_database_id_DOI` (대문자 DOI)로 기술되었으나,
실제 RCSB API 응답 필드명은 다름:
```json
"citation": [{"pdbx_database_id_doi": "10.1021/..."}]
```
- 키는 `pdbx_database_id_doi` (소문자 doi)
- 위치는 `citation` 배열의 [0], `rcsb_primary_citation`이 아님

### 해결 방법
```python
entry_data = api_call_with_retry(f"{RCSB_ENTRY_API}/{pdb_id}")
citations = entry_data.get('citation', [])
doi = citations[0].get('pdbx_database_id_doi', '해당없음') if citations else '해당없음'
```

### 예방 규칙
> **RCSB API DOI 필드명: `citation[0].pdbx_database_id_doi` (소문자 doi, citation 배열의 첫 원소)**
> Python dict key는 대소문자 구별함. API 응답을 실제로 출력하여 필드명을 확인한 후 사용.

---

## ERROR-011: MET KLIFS 데이터 유실 (DB 미커밋 + migration INSERT OR IGNORE 취약점)

**발생 파일:** `database.py` (migrate_database), `klifs_fetcher.py`
**발생 일시:** 2026-03-08
**영향 범위:** klifs_structures 테이블의 수집된 KLIFS 데이터

### 증상
MET의 klifs_structures 데이터(dfg, ac_helix 값)가 앱 재실행 후 사라짐.
`klifs_structures` 전체 행이 초기 커밋(v2.2) 상태와 완전히 동일해짐.

### 진단 결과
```python
# 초기 커밋 DB와 현재 DB의 klifs_structures 비교:
initial_klifs == current_klifs  # True — 완전히 동일
# 초기 커밋 MET pdb_structures 수 vs 현재: 129 == 129 — 동일
# UNIQUE index 존재 여부: 없음 (migration 미실행 또는 DB 리셋)
```

### 원인 1 (주요): git-미커밋 로컬 DB 유실
- MET KLIFS 데이터는 로컬에서 수집했으나 `protein_data.db`를 git에 push하지 않은 상태였음
- 이전 세션에서 `git pull` 또는 코드 관련 git 작업 중 로컬 DB가 git 추적 버전으로 덮어씌워짐
- **증거**: `git show 4a03505:protein_data.db`의 klifs_structures와 현재 DB가 100% 동일
- 수집된 MET 구조 수(129개)도 동일 → 구조 데이터 이후 KLIFS만 미커밋 상태였음

### 원인 2 (코드 취약점): migration의 INSERT OR IGNORE 순서 보장 없음
```python
# 기존 코드 (취약)
cursor.execute("""
    INSERT OR IGNORE INTO klifs_structures_new
    SELECT * FROM klifs_structures        -- 행 순서 미보장
""")
```
- sentinel 행(dfg=NULL)이 실제 데이터 행(dfg='DFGin')보다 먼저 삽입된 경우,
  UNIQUE 제약 추가 migration 시 sentinel이 보존되고 실제 데이터는 IGNORE됨
- SQLite SELECT * 는 rowid 순서로 반환되므로 먼저 삽입된 행이 우선됨

### 해결 방법

**즉각 복구**: sentinel rows가 없으므로 MET 재검색 시 자동 재수집 가능

**코드 수정 (migration ORDER BY 추가)**:
```python
# 수정 후: dfg NOT NULL 행을 먼저 삽입하여 sentinel보다 실제 데이터 우선 보존
cursor.execute("""
    INSERT OR IGNORE INTO klifs_structures_new
    SELECT * FROM klifs_structures
    ORDER BY structure_id, (dfg IS NULL) ASC, id ASC
""")
# (dfg IS NULL) ASC: false(0, 데이터 있음)가 true(1, NULL sentinel)보다 먼저
```

### 예방 규칙
> **DB 변경(마이그레이션 포함)이 포함된 코드 변경 전, 반드시 `protein_data.db`를 git add/commit/push 해야 한다.**
> DB는 코드보다 훨씬 복구하기 어렵다. 코드와 DB의 git push 주기를 분리하되,
> DB를 수정하는 코드 배포 전에는 반드시 DB snapshot을 먼저 push한다.
>
> 또한 migration에서 데이터를 복사할 때, 실제 값이 있는 행이 sentinel(NULL) 행보다
> 먼저 삽입되도록 `ORDER BY (nullable_col IS NULL) ASC` 를 추가한다.

---

## ERROR-010: st.dataframe 행 선택 인덱스 불일치

**발생 파일:** `app.py`
**출처:** Feedback_v1.3.md (Moderate M-7)
**발생 일시:** 2026-02-28 (설계 단계)

### 오류 내용
정렬/필터링 후 행 선택 시 엉뚱한 데이터가 상세 패널에 표시됨.

### 원인
```python
selected_pdb = pdb_df.iloc[selected_row]  # 위험
```
`event.selection.rows`는 화면에 **표시된 순서** 기준 인덱스를 반환함.
DataFrame이 사용자에 의해 정렬/필터링되면 `iloc` 인덱스와 화면 인덱스가 불일치.

### 해결 방법
```python
# 표시 전 reset_index 필수
pdb_df_display = pdb_df.reset_index(drop=True)

event = st.dataframe(
    pdb_df_display,
    on_select="rerun",
    selection_mode="single-row"
)

if event.selection.rows:
    selected_row = event.selection.rows[0]
    selected_pdb = pdb_df_display.iloc[selected_row]  # reset_index 이후라 안전
```

### 예방 규칙
> **`st.dataframe(on_select="rerun")` 사용 시 반드시 표시 전에 `df.reset_index(drop=True)` 적용.**
> `event.selection.rows`는 화면 표시 순서 기준이므로 원본 DataFrame 인덱스와 다를 수 있음.
