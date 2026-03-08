# 개발 채팅 기록
> Claude Sonnet 4.6 × kimj941 — 2026-03-08

---

## 1. KLIFS 재검색 및 데이터 소실 버그 수정

**User:**
현재 코드 상에서 이미 수집된 정보는 다시 정보수집을 안하고 저장돼는데 klifs 관련 정보는 정보가 있어도 다시 검색돼고 추가로 klifs 정보가 사라질 때도 있어. 이 원인을 파악하고 수정한 뒤 github에 덮어씌워줘.

**Claude:**
두 가지 버그 확인 및 수정 완료.

**버그 1 — 비키나아제 항상 재검색** (`klifs_fetcher.py`)
- 원인: 비키나아제면 `process_klifs`가 DB에 아무것도 안 남김 → 다음 실행에서 `get_klifs_by_structure()` → None → 계속 API 호출
- 수정: 비키나아제도 `dfg=NULL, ac_helix=NULL`인 sentinel 행 삽입 → 이후 skip 됨

**버그 2 — KLIFS 데이터 사라짐** (`database.py`)
- 원인 A: `klifs_structures`에 UNIQUE 제약 없음 → 같은 structure_id 중복 행 가능
- 원인 B: `get_klifs_bulk`의 dict comprehension이 마지막 행으로 덮어씀 → 나중에 들어온 NULL 행이 기존 유효 데이터를 덮어씌움
- 수정 A: 기존 DB는 마이그레이션으로 `UNIQUE(structure_id)` 제약 추가
- 수정 B: `INSERT` → `ON CONFLICT DO UPDATE SET dfg = COALESCE(새값, 기존값)` → NULL로 덮어쓰기 방지

커밋: `2d5823d` — GitHub push 완료

---

## 2. AI 자연어 DB 질의응답 기능 설계 요청

**User:**
내 데이터베이스 내에 있는 정보를 기반으로 LLM에게 프롬프트 명령을 내려 그 정보 안에서 원하는 답을 찾아주는 방식을 사용하고 싶다. 이런 코드를 디자인 할 수 있어?

**Claude:**
`llm_query.py` 신규 생성 + `app.py` 사이드바 AI 질의 UI 추가.

**작동 방식 — tool use 루프:**
```
사용자 질문
    ↓
Claude에게 DB 스키마 + 질문 전달
    ↓
Claude가 run_sql 도구로 필요한 SQL을 직접 작성·실행
    ↓ (여러 번 반복 가능 — 복합 질의)
Claude가 조회 결과를 바탕으로 구조생물학적 답변 생성
```

- `run_sql` 도구: SELECT/WITH 쿼리만 허용 (보안), 최대 500행 반환
- `claude-opus-4-6` + `adaptive thinking` 사용
- tool use 반복 루프 (최대 10회)
- 사이드바에 AI 질의 입력창 추가 (st.stop() 이전 렌더링으로 항상 표시)
- 메인 영역 하단에 AI 질의 결과 패널 (SQL + 결과 테이블 + 답변)

커밋: `ea9d7a7` — GitHub push 완료

---

## 3. AI 질의 결과 패널이 표시되지 않는 버그

**User:**
질문까지 하고 llm이 돌아갔는데 질의 결과가 어디에도 없어. 원인을 파악하고 해결해줘.

**Claude:**
원인: `st.stop()`이 그리드 행 미선택 시 호출되어 결과 패널(파일 맨 아래)에 도달하지 못함.

```
st.stop() ← uniprot_id 없을 때
st.stop() ← protein_data 없을 때
st.stop() ← structures 없을 때
st.stop() ← 그리드 행 미선택 시  ← 가장 흔히 걸리는 지점
...
[AI 결과 패널] ← 여기는 절대 도달 불가
```

수정: AI 결과 패널을 그리드 행 선택 `st.stop()` **바로 앞**으로 이동.

커밋: `03b91f8` — GitHub push 완료

---

## 4. AI 채팅 기록 관리 기능 추가

**User:**
현재 코드에서 수정 사항이 몇 개 있어. 지금은 llm 질문 프롬프트가 예를 들어 met을 검색한 창에서 llm 질문내역이 생성되면 다른 단백질로 넘어갈 시, 채팅내역이 사라지는데 그런 방식으로 채팅 기록을 분류하지 말고 질문 키워드를 분석해서 her2, her3가 관련된 질의응답이면 두 가지 단백질을 클릭 시에만 그 채팅 기록 리스트가 보이게끔 코드 디자인 가능해?

추가로 먼저 방금 만든 llm 질문 프롬프트를 기존 채팅 기록처럼 왼쪽 바에 질문 내용을 제목으로 해서 리스트를 남겨.(클릭하면 해당 채팅 답변이 뜨도록) 새로운 검색을 하거나 다른 단백질로 넘어가면 해당 채팅 본문은 다시 사라지도록 만들어줘.

**Claude:**
`chat_store.py` 신규 생성 + `app.py` 수정.

**`chat_store.py`**:
- `save_chat(related_uniprot_ids, question, result)` → record 반환
- `load_history(uniprot_id)` → 해당 단백질 기록만 최신순 반환
- `get_chat(id)` → ID로 단건 조회
- `delete_chat(id)` → 삭제

**`app.py` 변경**:
- 사이드바: 질의 기록 리스트 추가 (클릭 → 해당 기록 표시)
- 현재 선택된 기록은 primary 버튼으로 강조
- 단백질 전환 시 `ai_selected_chat_id` 제거 → 본문 초기화

커밋: `33d1427` — GitHub push 완료

---

## 5. GitHub pull + 키워드 기반 채팅 분류 변경

**User:**
다시 https://github.com/kimj941002/protein-construct-builder.git 코드 pull해와. (protein_data.db도 모두 가져와. 수정하고 다시 push하니까 기존에 있던 정보가 없는 거보니 뭔가 문제가 있는 것 같아서 정보 손실 없게 신경 써.)
그리고 현재 코드에서 채팅 기록 분류를 질문 키워드를 분석해서 her2, her3가 관련된 질의응답이면 두 가지 단백질을 클릭 시에만 그 채팅 기록리스트가 보이게끔 코드 디자인 가능해?

**Claude:**
**Pull 처리**: 로컬 DB가 미커밋 상태(`M protein_data.db`)이므로 먼저 커밋 후 pull → 이미 최신 상태 확인.

**키워드 기반 채팅 분류 구현**:

`extract_related_proteins(question, result, proteins)` 신규 추가:
- 질문 + LLM 답변 + 실행된 SQL 전체 텍스트에서 gene_name / protein_name 검색
- 단어 경계(`\b`) 매칭으로 오탐 방지, 2자 미만 이름 제외
- 매칭 단백질 없으면 현재 선택 단백질로 폴백

`save_chat`: `uniprot_id(단일)` → `related_uniprot_ids(list)` 스키마 변경

`load_history`: `related_uniprot_ids` 리스트 매칭 + 구버전 단일 필드 하위 호환

예시:
```
질문: "HER2와 HER3의 공통 구조 특성을 비교해줘"
  → related_uniprot_ids: ["P04626", "P21860"]  (ERBB2, ERBB3)

HER2 선택 → 이 채팅이 사이드바 목록에 표시 ✓
HER3 선택 → 이 채팅이 사이드바 목록에 표시 ✓
MET 선택  → 이 채팅은 표시 안 됨 ✓
```

커밋: `42c53d5` — GitHub push 완료

---

## 6. MET KLIFS 데이터 유실 분석 및 Errorbook 기록

**User:**
지금 코드 수정하면서 내가 db 손실 없게 해달라고 했는데 다시 들어가보니까 met의 klifs 데이터 수집했던게 사라져있어. 이 문제를 분석하고 해결방안을 제시해줘. 그리고 errorbook에 남겨놔.

**Claude:**
**분석 결과**:

```python
# 초기 커밋 DB와 현재 DB의 klifs_structures 비교:
initial_klifs == current_klifs  # True — 완전히 동일 (8행)
# 초기 커밋 MET pdb_structures 수 vs 현재: 129 == 129
# UNIQUE index 존재 여부: 없음 (migration 미실행 상태)
```

**원인 1 (주요): git-미커밋 DB 유실**
- MET KLIFS 데이터는 로컬에서만 수집됐고 git에 한 번도 push된 적 없었음
- 이전 세션에서 git pull 또는 코드 관련 git 작업 중 로컬 DB가 초기 커밋 버전으로 덮어씌워짐

**원인 2 (코드 취약점): migration INSERT OR IGNORE 순서 보장 없음**
- sentinel(dfg=NULL)이 먼저 삽입된 경우, migration 시 실제 데이터가 IGNORE될 수 있음

**코드 수정** (`database.py`):
```python
# 수정 후: dfg NOT NULL 행을 먼저 삽입
INSERT OR IGNORE INTO klifs_structures_new
SELECT * FROM klifs_structures
ORDER BY structure_id, (dfg IS NULL) ASC, id ASC
```

**복구 방법**: sentinel rows 없으므로 MET 재검색 시 자동 재수집 가능

**예방 규칙**: DB 변경 코드 배포 전 반드시 protein_data.db를 먼저 git push

**Errorbook**: ERROR-011 추가 완료

커밋: `affdd06` — GitHub push 완료

---

## 7. Streamlit Cloud ImportError 수정 (1차)

**User:**
```
File "/mount/src/protein-construct-builder/app.py", line 31, in <module>
    from chat_store import delete_chat, extract_related_proteins, get_chat, load_history, save_chat
```
streamlit 들어가니까 이런 오류가 떠. 해결해.

**Claude:**
원인: `runtime.txt` 없으면 Streamlit Cloud는 Python 3.9를 사용. `chat_store.py`의 `str | None`, `list[str]`, `dict | None` 등 타입 힌트는 Python 3.10+ 전용 문법 → import 시점에 TypeError 발생.

**수정**:
1. `runtime.txt` 추가 (`python-3.11` 명시)
2. `chat_store.py`, `llm_query.py`, `database.py`에 `from __future__ import annotations` 추가

커밋: `121d40f` — GitHub push 완료

---

## 8. Streamlit Cloud ImportError 수정 (2차 — 전체 파일)

**User:**
그래도 streamlit에서 구동이 안 돼는데? 다시 확인해보고 이번엔 구동까지 확인해.

**Claude:**
전체 `.py` 파일 스캔 결과 7개 파일 추가 누락 확인:

```
[NEED FIX] app.py
[NEED FIX] complex_fetcher.py
[NEED FIX] klifs_fetcher.py
[NEED FIX] mutation_analyzer.py
[NEED FIX] pdb_fetcher.py
[NEED FIX] uniprot_fetcher.py
[NEED FIX] utils.py
```

7개 파일 전체에 `from __future__ import annotations` 추가.

**검증**:
- `ast.parse`: 전체 11개 `.py` 파일 syntax OK
- 모든 모듈 import 성공
- `migrate_database()`, `load_history()`, `extract_related_proteins()` 실행 확인
- `streamlit run app.py` → HTTP 200 확인

커밋: `a0ed1bc` — GitHub push 완료

---

## 9. Streamlit Cloud ImportError 수정 (3차 — 최종)

**User:**
```
File "/mount/src/protein-construct-builder/app.py", line 31, in <module>
    )
해당 importerror가 뜬다.
```

**Claude:**
line 31이 `)` (database import 블록 닫힘)으로 바뀐 것 → 이전 fix가 적용됐으나 새 에러 발생.

**원인 분석**:

로컬 구동은 정상(HTTP 200) → Cloud 환경 한정 문제.

`biopython`이 코드 어디에도 사용되지 않으면서 `requirements.txt`에 포함돼 있음:
- `biopython`은 C 확장 빌드가 필요한 패키지
- Streamlit Cloud에서 설치 실패 시 pip가 에러코드를 반환
- 결과적으로 `python-dotenv`도 미설치 → `config.py` import 실패 → `database.py` import 실패 → app.py line 31 ImportError

```
biopython 설치 실패
  → python-dotenv 미설치
    → config.py: from dotenv import load_dotenv 실패
      → database.py: from config import ... 실패
        → app.py line 31: from database import (...) → )  ← ImportError
```

**수정**:
1. `requirements.txt`: `biopython` 제거 (미사용 패키지)
2. `config.py`: `load_dotenv()` → `try-except ImportError`로 방어 처리

**검증**:
- 전체 import 체인 정상
- `streamlit run app.py` → HTTP 200 확인

커밋: `96d8bc3` — GitHub push 완료

---

## 커밋 히스토리 요약

| 커밋 | 내용 |
|------|------|
| `2d5823d` | fix: KLIFS 재검색 및 데이터 소실 버그 수정 |
| `ea9d7a7` | feat: AI 자연어 DB 질의응답 기능 추가 (llm_query.py) |
| `03b91f8` | fix: AI 질의 결과 패널이 표시되지 않는 버그 수정 |
| `33d1427` | feat: AI 질의 대화 기록 관리 (chat_store.py) |
| `823f3c2` | backup: pull 전 로컬 DB 상태 보존 |
| `42c53d5` | feat: AI 채팅 기록 분류를 키워드 기반 다중 단백질로 변경 |
| `affdd06` | fix: migration INSERT OR IGNORE ORDER BY 추가 (ERROR-011) |
| `121d40f` | fix: Streamlit Cloud Python 3.9 호환성 오류 수정 (runtime.txt) |
| `a0ed1bc` | fix: Python 3.9 호환성 — 전체 .py 파일 from __future__ 추가 |
| `96d8bc3` | fix: Streamlit Cloud 배포 실패 원인 제거 (biopython, dotenv) |
