# Protein Construct Builder — 전체 프로젝트 계획 흐름서

**작성일:** 2026-03-08
**기반 문서:** Plan v1.1 ~ v2.2, Feedback v1.1 ~ v1.3, Construct Feedback v0/v0.1
**현재 구현 버전:** v2.2

---

## 1. 프로젝트 개요

### 목적
재조합 단백질 construct 설계 시 필요한 정보를 자동 수집하는 통합 데이터베이스 시스템.
단백질 이름 하나 입력 → UniProt/RCSB PDB/KLIFS에서 자동 수집 → SQLite DB 누적 → Streamlit UI로 조회

### 핵심 사용 시나리오
1. 연구자가 단백질 이름 입력 (e.g., cMET, EGFR)
2. 시스템이 UniProt → RCSB PDB API를 순차 호출하여 모든 구조 정보 수집
3. Ag-Grid 테이블로 PDB 목록 조회, 필터링, 행 클릭으로 상세 정보 확인
4. PDF 논문 업로드 → Claude AI로 발현/정제/결정화 조건 자동 추출
5. Excel/CSV 내보내기

### 기술 스택 (최종 확정)
| 구성 요소 | 기술 | 최소 버전 |
|-----------|------|-----------|
| UI 프레임워크 | Streamlit | ≥1.35.0 |
| DB | SQLite | 내장 |
| API 캐싱 | requests-cache (CachedSession 방식) | ≥1.0.0 |
| 서열 분석 | BioPython PairwiseAligner | ≥1.79 |
| 테이블 UI | streamlit-aggrid | ≥0.3.4 |
| 병렬 처리 | concurrent.futures | 표준 라이브러리 |
| AI 분석 | Anthropic Claude API | - |

---

## 2. 버전별 진화 흐름

```
v1.1 → 초기 설계 (4테이블, 기본 API 플로우)
  │  [피드백] BioPython deprecated, RCSB 엔드포인트 오류, Streamlit 클릭 미지원
  ↓
v1.2 → API 검증 반영 (chem_comp 2단계, 병렬처리, SIFTS 잔기매핑)
  │  [피드백] chem_comp API 누락 (nonpolymer_entity만으론 SMILES 불가)
  ↓
v1.3 → API 실검증 반영 (8단계 API 플로우, Mutation JSON, 패키지 버전 명시)
  │  [피드백] normalize_gene_name 정규식 버그, requests-cache 스레드 안전성
  ↓
v1.4 → 설계 원칙 확립 (계획서에 실행 없는 코드 스니펫 제거, 의사코드만)
  │
  ↓ [DB 정규화 요청 (Construct Feedback v0)]
v2.0 → 8테이블 정규화 DB + Ag-Grid UI 도입
  │  sequences/FASTA 파일 분리, JSON 컬럼 → 정규화 테이블 이전
  ↓
v2.1 → max_count 슬라이더 제거 + 증분 수집 (이미 수집된 PDB 건너뜀)
  ↓
v2.2 → KLIFS 키나아제 연동 + Claude AI 논문 분석 기능 추가 (현재 버전)
```

---

## 3. 데이터 수집 API 플로우 (v1.3 확정, v2.2 현재 적용)

| 단계 | API | 엔드포인트 | 수집 정보 |
|------|-----|-----------|-----------|
| 1 | UniProt REST | `rest.uniprot.org/uniprotkb/search` | 기본정보, 서열, PDB cross-reference |
| 2 | RCSB Search | `search.rcsb.org/rcsbsearch/v2/query` | UniProt accession 기반 PDB ID (1단계와 union) |
| 3 | RCSB Entry | `data.rcsb.org/rest/v1/core/entry/{pdb_id}` | method, resolution, 결정화조건, DOI |
| 4 | RCSB Polymer Entity | `.../polymer_entity/{pdb_id}/{entity_id}` | expression_system, host_cell_line, 서열, pdbx_mutation |
| 5 | RCSB Non-polymer Entity | `.../nonpolymer_entity/{pdb_id}/{entity_id}` | ligand comp_id만 (SMILES 없음) |
| 6 | RCSB Chem Comp | `.../chem_comp/{comp_id}` | formula, SMILES, InChI, ligand 이름 |
| 7 | PDBe SIFTS | `ebi.ac.uk/pdbe/api/mappings/uniprot/{pdb_id}` | PDB↔UniProt 잔기번호 매핑 (PDB만, AlphaFold skip) |
| 8 | KLIFS | `klifs.net/api/structures_pdb_list` | DFG, aC_helix, qualityscore, gatekeeper |

> **DOI 필드명 주의:** `citation[0].pdbx_database_id_doi` (소문자 doi, citation 배열의 [0])
> **chem_comp 주의:** nonpolymer_entity API는 comp_id만 반환. SMILES/formula는 반드시 chem_comp 2차 호출 필요
> **KLIFS 엔드포인트 주의:** `/structures_pdb_list` (언더스코어, 슬래시 없음). 필드명: `DFG`, `aC_helix` (대소문자 정확히)

---

## 4. 현재 DB 스키마 (v2.2 기준, 10테이블)

```
proteins (1) ──→ (∞) protein_domains
proteins (1) ──→ (∞) pdb_structures
                          │
                          ├── (∞) structure_mutations
                          ├── (∞) ligands
                          ├── (∞) partner_proteins ──→ (∞) partner_protein_chains
                          ├── (∞) ptm_oligosaccharides
                          ├── (∞) klifs_structures
                          └── (1) paper_analysis
```

### 테이블별 핵심 컬럼

**proteins**
- `uniprot_id` PK, `gene_name`, `protein_name`, `organism`, `sequence_path` (FASTA 파일 경로), `sequence_length`, `function_desc`, `subcellular_location`, `signal_peptide`, `created_at`

**pdb_structures**
- `structure_id` PK, `uniprot_id` FK, `source`, `method`, `resolution`, `mean_plddt`, `chain_id`, `residue_range`, `expression_system` (단백질 유래 생물), `host_cell_line` (발현 숙주), `crystal_method/ph/temp/details`, `space_group`, `complex_type`, `doi`, `deposition_date`

**structure_mutations**
- `structure_id` FK, `mutation` (e.g., K1110A), `position`, `mutation_type` (engineered/natural_variant)

**klifs_structures**
- `structure_id` FK, `klifs_id`, `kinase_name`, `family`, `dfg`, `ac_helix`, `qualityscore`, `missing_residues`, `missing_atoms`, `rmsd1`, `rmsd2`, `gatekeeper`

**paper_analysis**
- `structure_id` FK UNIQUE, `pdf_path`, `status`, `raw_text`, `analyzed_at`
- (ERD Construct.md에 설계된 상세 추출 컬럼들은 미구현 — Section 6 참조)

---

## 5. 모듈 구성 (현재 파일 구조)

```
protein_construct_builder/
├── app.py                    # Streamlit 메인 UI (Ag-Grid, 상세 패널, Claude 논문 분석)
├── config.py                 # API URL, DB 경로, 설정값
├── database.py               # SQLite CRUD (10테이블), init/migrate 함수
├── uniprot_fetcher.py        # UniProt API + RCSB Search API, 입력 정규화
├── pdb_fetcher.py            # RCSB Entry/Polymer Entity API, 병렬 처리, 증분 수집
├── complex_fetcher.py        # Non-polymer Entity + Chem Comp API + Partner protein
├── mutation_analyzer.py      # SIFTS 매핑 + PairwiseAligner + mutation JSON 생성
├── klifs_fetcher.py          # KLIFS API 연동
├── requirements.txt
├── protein_data.db           # SQLite DB (GitHub 추적 대상, 다기기 공유용)
├── protein_api_cache.sqlite  # API 캐시 (.gitignore 대상)
├── sequences/                # FASTA 파일 저장 폴더
└── archive_legacy_code/      # 구버전 코드 보관
```

### 모듈별 역할 요약

| 모듈 | 핵심 책임 | 주요 함수 |
|------|----------|-----------|
| `uniprot_fetcher.py` | UniProt 검색, 입력 정규화, PDB ID 수집 | `search_uniprot()`, `get_pdb_ids()` |
| `pdb_fetcher.py` | RCSB 병렬 수집, 증분 처리, complex_type 판별 | `fetch_all_structures()` |
| `complex_fetcher.py` | Ligand SMILES/formula (chem_comp 2단계), Partner protein 추출 | `fetch_ligand_info()`, `fetch_partner_info()` |
| `mutation_analyzer.py` | SIFTS 잔기번호 매핑, PairwiseAligner 서열 비교, engineered/natural 분류 | `analyze_mutations()` |
| `klifs_fetcher.py` | KLIFS API 키나아제 DFG/aC_helix 정보 수집 | `fetch_klifs_info()` |
| `database.py` | 10테이블 CRUD, init/migrate | `init_database()`, `migrate_database()`, insert/get 함수들 |
| `app.py` | Streamlit UI, Ag-Grid, Claude API 논문 분석 | 메인 앱 |

---

## 6. 주요 설계 결정 사항

### 6-1. DB 정규화 (v2.0)
- v1.x까지 JSON 컬럼으로 저장하던 mutations, ligands, partner_proteins를 각각 독립 테이블로 분리
- `sequences/` 폴더에 FASTA 파일 저장 (DB에 서열 TEXT 직접 저장 → 파일 경로 참조로 전환)
- 재검색 시 증분 수집: 이미 DB에 있는 PDB는 건너뜀

### 6-2. expression_system vs host_cell_line 컬럼 의미
| DB 컬럼 | API 소스 | 실제 내용 | UI 라벨 |
|---------|---------|----------|---------|
| `expression_system` | `rcsb_entity_source_organism` | 단백질 유래 생물 (Homo sapiens 등) | **Organism** |
| `host_cell_line` | `rcsb_entity_host_organism` | 발현 숙주 (E. coli, Sf9 등) | **Expr System** |

### 6-3. paper_analysis 현황
- 현재 구현: `id, structure_id, pdf_path, status, raw_text, analyzed_at`
- Claude API로 `raw_text` 추출 후 논문 분석 정보를 UI에 표시
- Construct.md ERD에 설계된 상세 컬럼들 (paper_topic, conclusions, vector 등 25+개)은 **미구현 상태** — 향후 확장 예정

### 6-4. AlphaFold 처리
- `source = 'AlphaFoldDB'` 행에 대해 SIFTS 호출 skip (AlphaFold는 PDB ID 형식 미지원)
- `mean_plddt` 컬럼 존재하지만, 현재 수집 파이프라인이 RCSB 기반이라 전부 NULL
- pLDDT UI 컬럼은 v2.2에서 제거됨

### 6-5. 병렬 처리 및 캐싱
- `requests-cache.install_cache()` 전역 패치 방식 → 스레드 안전 문제로 **`CachedSession`** 방식 권장
- ThreadPoolExecutor max_workers=5로 병렬 수집
- 캐시 설정: `expire_after=604800, allowable_codes=(200,)` (429/500 캐싱 방지)

---

## 7. Ag-Grid UI 구조 (v2.0~)

```
┌─[사이드바]──────────┐  ┌─[메인 영역]────────────────────────────┐
│ 단백질 검색창        │  │ [단백질 헤더 카드] UniProt ID / Gene    │
│ 수집된 단백질 목록   │  │                                        │
│  (라디오 버튼)       │  │ ══ Ag-Grid 구조 테이블 ════════════════ │
└─────────────────────┘  │  floatingFilter: Method[▼] Complex[▼]  │
                          │  PDB ID / Res / Chain / Mutations ...  │
                          │  [Excel] [CSV] 내보내기                 │
                          │                                        │
                          │ ══ 상세 패널 (행 선택 시) ══════════════ │
                          │  📋 기본정보 / 💊Ligand / 🤝Partner    │
                          │  🍬PTM / 🧬Mutation / 📄논문분석        │
                          └────────────────────────────────────────┘
```

### 필터 전략
- Method, Complex_type: Ag-Grid 커스텀 floatingFilterComponent (`<select>` 드롭다운)
- 나머지 컬럼: Ag-Grid 내장 floatingFilter (텍스트/숫자 범위)

---

## 8. 논문 분석 기능 (v2.2, Claude API)

- PDF 업로드 → Anthropic Claude API로 논문 내용 분석
- 1차: PDF 직접 전송 (document type)
- 실패 시 fallback: `pypdf`로 텍스트 추출 후 텍스트로 재전송
- 분석 결과는 `paper_analysis.raw_text`에 저장, UI에서 파싱하여 표시
- `st.rerun()` 후 Ag-Grid 선택 초기화 문제로 rerun 대신 **로컬 변수 직접 갱신** 방식 채택

---

## 9. 알려진 미구현 / 향후 과제

| 항목 | 상태 | 관련 버전 |
|------|------|----------|
| paper_analysis 상세 컬럼 (vector, expression_host 등) | 미구현 | Construct.md ERD 설계 완료 |
| AlphaFold 수집 파이프라인 | 미구현 | v1.3 선택기능으로 설계 |
| SIFTS 기반 정확한 잔기번호 매핑 | 미구현 | mutation_analyzer에 설계만 |
| 3D 구조 뷰어 (py3Dmol) | 미구현 | v1.3 확장 아이디어 |

---

## 10. GitHub 운용 전략

- `protein_data.db`: GitHub 추적 O (다기기 DB 공유 목적)
- `protein_api_cache.sqlite`: .gitignore (API 캐시, 기기별 독립)
- pull/push는 코드 구조 변경 시에만 수행
- pull 전 반드시 현재 DB를 커밋/푸시하여 데이터 손실 방지
