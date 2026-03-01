# Code Patch v1.0

**작성일:** 2026-03-01
**기반 피드백:** `Code Feedback_v1.0.txt`

---

## 수정 항목 요약

| # | 항목 | 수정 파일 |
|---|------|-----------|
| 1 | RCSB-PDB 하이퍼링크 추가 | `app.py` |
| 2 | Partner Protein 수집 범위 확대 (항체 단편 등 포함) | `complex_fetcher.py`, `database.py` |
| 3 | Partner 테이블 컬럼 재구성 (RCSB Macromolecules 양식) | `app.py` |
| 4 | Oligosaccharides / PTM 정보 수집 및 표시 | `config.py`, `database.py`, `complex_fetcher.py`, `app.py` |
| 5 | 수정 내용 md 문서화 | `Update/Code_patch_v1.0.md` (본 파일) |

---

## 상세 변경 내용

### 1. RCSB-PDB 하이퍼링크 추가 (`app.py`)

- 구조 상세 패널의 **"📋 기본 정보"** expander 최상단에 RCSB-PDB 웹페이지 링크 추가
- 링크 형식: `https://www.rcsb.org/structure/{PDB_ID}`
- Streamlit markdown 하이퍼링크로 표시되어 클릭 시 RCSB 웹페이지로 바로 이동

---

### 2. Partner Protein 수집 범위 확대 (`complex_fetcher.py`, `database.py`)

#### 문제점
- 기존 코드는 `reference_sequence_identifiers`에 UniProt ID가 있는 entity만 파트너로 등록
- Trastuzumab Fab Light/Heavy Chain 등 항체 단편은 UniProt 매핑이 없어 누락됨

#### 해결 방법
- `fetch_partners_for_structure()` 로직 재작성
- **변경 전:** UniProt 매핑 있는 entity 중 target UniProt과 다른 것만 포함
- **변경 후:** 타겟 UniProt을 보유한 entity를 제외한 **모든 polypeptide entity** 포함
  - UniProt 있는 경우: 정상 파트너로 등록 (UniProt, 잔기 범위 포함)
  - UniProt 없는 경우: 분자 이름, 체인, 서열 길이만으로도 등록

#### 수집 필드 확대
| 신규 필드 | 설명 |
|-----------|------|
| `entity_id` | RCSB Entity 번호 |
| `partner_chains` | 모든 체인 ID (JSON 배열) |
| `sequence_length` | 서열 길이 |
| `organism` | 소스 생물종 |

#### DB 스키마 변경 (`database.py`)
- `partner_proteins` 테이블에 4개 컬럼 추가 (`migrate_database()` 자동 적용):
  - `entity_id TEXT`
  - `partner_chains TEXT`
  - `sequence_length INTEGER`
  - `organism TEXT`

---

### 3. Partner 테이블 컬럼 재구성 (`app.py`)

RCSB PDB 웹사이트의 **Macromolecules** 섹션 표준과 동일한 컬럼 구성으로 변경:

| 컬럼명 | 내용 |
|--------|------|
| Entity ID | RCSB entity 번호 |
| Molecule | 분자 이름 |
| Chains | 모든 체인 ID (쉼표 구분) |
| Seq Length | 서열 길이 |
| Organism | 소스 생물종 |
| Details (UniProt ID) | UniProt 접근 번호 |

---

### 4. Oligosaccharides / PTM 정보 추가

#### 4-1. 신규 API 엔드포인트 (`config.py`)
```python
RCSB_BRANCHED_ENTITY_API = "https://data.rcsb.org/rest/v1/core/branched_entity"
RCSB_GRAPHQL_API         = "https://data.rcsb.org/graphql"
```

#### 4-2. 신규 테이블: `ptm_oligosaccharides` (`database.py`)
```sql
CREATE TABLE ptm_oligosaccharides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    structure_id    TEXT,           -- PDB 구조 ID
    entity_id       TEXT,           -- RCSB Entity 번호
    name            TEXT,           -- 올리고당 이름
    chains          TEXT,           -- 올리고당 체인 ID (단일 문자, 인스턴스별)
    linked_chain    TEXT,           -- 결합된 단백질 체인 ID
    linked_position INTEGER,        -- 결합된 잔기 번호
    linked_residue  TEXT            -- 결합된 아미노산 (3-letter, 예: ASN)
)
```

#### 4-3. 신규 함수 (`complex_fetcher.py`)

| 함수 | 역할 |
|------|------|
| `fetch_branched_linkage(pdb_id, branched_chains)` | RCSB GraphQL `struct_conn`에서 `conn_type_id='covale'`인 레코드를 필터링하여 올리고당-단백질 결합 위치 수집 |
| `fetch_oligosaccharides_for_structure(pdb_id, entry_data, session)` | `branched_entity_ids` 기반으로 모든 올리고당 수집. 체인 인스턴스별로 결합 위치 포함 |

- `process_complex()` 반환값: `(ligands, partners, oligos)` 3-tuple로 변경

#### 4-4. UI: PTM / Oligosaccharides 섹션 (`app.py`)
- 새 expander: **"🍬 PTM / Oligosaccharides"**
- 올리고당 있으면 자동 expanded
- 테이블 컬럼: Entity ID, 이름, Chain, 결합 Chain, 결합 Position, 결합 Residue
- "결합 Residue"는 3-letter 아미노산 코드 (예: ASN = N-glycosylation 부위)

---

## 파일별 변경 요약

### `config.py`
- `RCSB_BRANCHED_ENTITY_API` 추가
- `RCSB_GRAPHQL_API` 추가 (기존 hardcoded URL → 상수화)

### `database.py`
- `partner_proteins` CREATE TABLE: `entity_id`, `partner_chains`, `sequence_length`, `organism` 컬럼 추가
- `ptm_oligosaccharides` 신규 테이블 CREATE TABLE 추가
- `migrate_database()`: 기존 DB에 새 컬럼 자동 추가 + `ptm_oligosaccharides` 테이블 생성
- `insert_partner_protein()`: SQL에 새 컬럼 포함
- 신규 함수: `insert_oligosaccharide()`, `get_oligosaccharides_by_structure()`, `delete_oligosaccharides_by_structure()`

### `complex_fetcher.py`
- import: `RCSB_BRANCHED_ENTITY_API`, `RCSB_GRAPHQL_API` 추가; `insert_oligosaccharide`, `delete_oligosaccharides_by_structure` 추가
- `fetch_chem_comp_info()`: hardcoded URL → `RCSB_GRAPHQL_API` 사용
- `fetch_partners_for_structure()`: 전면 재작성 (항체 단편 포함)
- 신규: `fetch_branched_linkage()`, `fetch_oligosaccharides_for_structure()`
- `process_complex()`: 올리고당 처리 추가, 반환값 3-tuple

### `app.py`
- `get_oligosaccharides_by_structure` import 추가
- `ensure_complex_data()`: 반환값 `(ligands, partners, oligos)` 3-tuple로 변경
- 상세 패널: RCSB-PDB 하이퍼링크 추가
- Ligand/Partner 섹션: `ensure_complex_data` 호출을 단일 호출로 통합
- Partner 테이블: 컬럼 재구성 (Entity ID, Molecule, Chains, Seq Length, Organism, UniProt ID)
- 신규 섹션: "🍬 PTM / Oligosaccharides" 테이블
