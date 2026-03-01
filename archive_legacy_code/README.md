# archive_legacy_code/

**생성일:** 2026-03-01
**목적:** 정규화 이전 코드 및 구 버전 스냅샷 보관

## 보관 항목

| 파일 | 원본 위치 | 보관 이유 |
|------|-----------|-----------|
| `Code_patch_v1.0.md` | `Update/Code_patch_v1.0.md` | v1.0 패치 이력 |
| `snapshots/app_v1_streamlit_dataframe.py` | `app.py` (Ag-Grid 이전) | st.dataframe 버전 참고용 |
| `snapshots/database_schema_v1_json_columns.py` | `database.py` (정규화 이전) | JSON 컬럼 단일 테이블 스키마 참고용 |

## 현재 활성 파일 (메인 폴더)

- `config.py` — API URL + 경로 상수
- `database.py` — 8테이블 정규화 스키마 (schema v2)
- `utils.py` — API 재시도 + CachedSession
- `uniprot_fetcher.py` — UniProt 수집
- `pdb_fetcher.py` — PDB 구조 수집
- `complex_fetcher.py` — 리간드 / 파트너 / 올리고당 수집
- `mutation_analyzer.py` — Mutation 분석
- `app.py` — Streamlit + Ag-Grid UI (schema v2 기반)
