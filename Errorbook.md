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
