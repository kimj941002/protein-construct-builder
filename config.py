# config.py
# 프로젝트 전체에서 사용하는 설정값을 한 곳에 모아놓은 파일
# API URL, 데이터베이스 경로, 캐시 설정 등을 여기서 관리합니다.

import os
from dotenv import load_dotenv

# .env 파일에서 환경변수 로드 (ANTHROPIC_API_KEY 등)
load_dotenv()

# ─────────────────────────────────────────────
# 데이터베이스 설정
# ─────────────────────────────────────────────

# protein_data.db: 수집한 단백질/구조 데이터를 저장하는 SQLite 파일
DB_PATH = os.path.join(os.path.dirname(__file__), "protein_data.db")

# sequences/: 아미노산 서열 FASTA 파일 저장 폴더
SEQUENCES_DIR = os.path.join(os.path.dirname(__file__), "sequences")

# papers/: 논문 PDF 파일 저장 폴더
PAPERS_DIR = os.path.join(os.path.dirname(__file__), "papers")

# protein_api_cache.sqlite: API 응답을 캐시하는 SQLite 파일 (재실행 시 빨라짐)
CACHE_PATH = os.path.join(os.path.dirname(__file__), "protein_api_cache.sqlite")

# 캐시 만료 시간 (초) — 7일 = 604800초
CACHE_EXPIRE = 604800

# ─────────────────────────────────────────────
# API URL 상수
# ─────────────────────────────────────────────

# UniProt REST API: 단백질 기본 정보 검색
UNIPROT_API = "https://rest.uniprot.org/uniprotkb/search"

# RCSB Search API: PDB 구조 검색
RCSB_SEARCH_API = "https://search.rcsb.org/rcsbsearch/v2/query"

# RCSB Entry API: PDB 엔트리(구조) 기본 정보 조회
# 사용법: RCSB_ENTRY_API + "/" + pdb_id (예: .../entry/2WGJ)
RCSB_ENTRY_API = "https://data.rcsb.org/rest/v1/core/entry"

# RCSB Polymer Entity API: 단백질 사슬(폴리머) 정보 조회
# 사용법: RCSB_POLYMER_ENTITY_API + "/" + pdb_id + "/" + entity_id
RCSB_POLYMER_ENTITY_API = "https://data.rcsb.org/rest/v1/core/polymer_entity"

# RCSB Non-polymer Entity API: 리간드 정보 조회
# 사용법: RCSB_NONPOLYMER_ENTITY_API + "/" + pdb_id + "/" + entity_id
RCSB_NONPOLYMER_ENTITY_API = "https://data.rcsb.org/rest/v1/core/nonpolymer_entity"

# RCSB Chem Comp API: 화학물질 세부정보(화학식, SMILES 등) 조회
# 사용법: RCSB_CHEM_COMP_API + "/" + chem_comp_id (예: .../chem_comp/ATP)
RCSB_CHEM_COMP_API = "https://data.rcsb.org/rest/v1/core/chem_comp"

# RCSB Branched Entity API: 올리고당류(Oligosaccharides) 정보 조회
# 사용법: RCSB_BRANCHED_ENTITY_API + "/" + pdb_id + "/" + entity_id
RCSB_BRANCHED_ENTITY_API = "https://data.rcsb.org/rest/v1/core/branched_entity"

# RCSB GraphQL API: 복합 쿼리 (struct_conn 등)
RCSB_GRAPHQL_API = "https://data.rcsb.org/graphql"

# SIFTS API (EBI PDBe): PDB 잔기번호 ↔ UniProt 잔기번호 매핑
# 사용법: SIFTS_API + "/" + pdb_id (예: .../uniprot/2WGJ)
SIFTS_API = "https://www.ebi.ac.uk/pdbe/api/mappings/uniprot"

# AlphaFold API: 단백질 구조 예측 데이터 조회
# 사용법: ALPHAFOLD_API + "/" + uniprot_id (예: .../prediction/P08581)
ALPHAFOLD_API = "https://alphafold.ebi.ac.uk/api/prediction"

# ─────────────────────────────────────────────
# API 요청 설정
# ─────────────────────────────────────────────

# 요청 타임아웃 (초)
REQUEST_TIMEOUT = 30

# 재시도 횟수
MAX_RETRIES = 3

# 병렬 처리 시 최대 스레드 수
MAX_WORKERS = 5
