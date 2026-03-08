
// ============================================================
// Protein Construct Builder — DB ERD (database.py 기준 검증 완료)
// 버전: v2.2 | 갱신: 2026-03-08
// ============================================================

Table "proteins" {
  "uniprot_id"           TEXT [pk]  // UniProt Accession 번호 (e.g., P08581) — 기본키
  "gene_name"            TEXT       // 공식 유전자 이름 (e.g., MET, EGFR)
  "protein_name"         TEXT       // 단백질 전체 명칭
  "organism"             TEXT       // 단백질 유래 생물 (e.g., Homo sapiens)
  "sequence_path"        TEXT       // FASTA 파일 경로 (sequences/ 폴더, DB에 서열 직접 저장 안 함)
  "sequence_length"      INTEGER    // 아미노산 서열 길이
  "function_desc"        TEXT       // UniProt 단백질 기능 설명
  "subcellular_location" TEXT       // 세포 내 위치 (e.g., Nucleus, Cytoplasm)
  "signal_peptide"       TEXT       // Signal peptide 범위 (e.g., 1-24)
  "created_at"           TIMESTAMP  // 데이터 수집 시각 (자동 기록)
}

Table "protein_domains" {
  "id"         INTEGER [pk]  // 자동 증가 기본키
  "uniprot_id" TEXT          // proteins 테이블 참조 (FK)
  "name"       TEXT          // 도메인 이름 (e.g., Protein kinase, Sema)
  "start_pos"  INTEGER       // UniProt 기준 도메인 시작 잔기 번호
  "end_pos"    INTEGER       // UniProt 기준 도메인 종료 잔기 번호
}

Table "pdb_structures" {
  "structure_id"      TEXT [pk]  // PDB ID (e.g., 2WGJ) 또는 AlphaFold ID (AF-P08581-F1)
  "uniprot_id"        TEXT       // proteins 테이블 참조 (FK)
  "source"            TEXT       // 데이터 출처 ('PDB' 또는 'AlphaFoldDB')
  "method"            TEXT       // 실험 방법 ('X-RAY DIFFRACTION' / 'ELECTRON MICROSCOPY' / 'SOLUTION NMR')
  "resolution"        REAL       // 해상도 (Å) — NMR은 NULL
  "mean_plddt"        REAL       // AlphaFold 예측 신뢰도 평균 (0~100) — AlphaFoldDB만 해당, PDB는 NULL
  "chain_id"          TEXT       // 타겟 단백질 체인 ID (다중 체인이면 "A, B" 형식)
  "residue_range"     TEXT       // 구조에 포함된 서열 범위 (e.g., 25-932) — 첫 번째 체인 기준
  "expression_system" TEXT       // 단백질 유래 생물 (rcsb_entity_source_organism) — UI에서 'Organism' 라벨로 표시
  "host_cell_line"    TEXT       // 발현 숙주 (rcsb_entity_host_organism, e.g., E. coli, Sf9) — UI에서 'Expr System' 라벨로 표시
  "crystal_method"    TEXT       // 결정화 방법 (e.g., VAPOR DIFFUSION) — NMR/Cryo-EM은 NULL
  "crystal_ph"        REAL       // 결정화 pH — NMR/Cryo-EM은 NULL
  "crystal_temp"      REAL       // 결정화 온도 (K) — NMR/Cryo-EM은 NULL
  "crystal_details"   TEXT       // 결정화 상세 조건 (buffer, PEG 농도 등) — NMR/Cryo-EM은 NULL
  "space_group"       TEXT       // 결정 공간군 (e.g., P 21 21 21) — NMR/Cryo-EM은 NULL
  "complex_type"      TEXT       // 복합체 유형 ('apo' / 'ligand' / 'protein-protein' / 'mixed')
  "doi"               TEXT       // 논문 DOI (없으면 '해당없음') — citation[0].pdbx_database_id_doi 에서 추출
  "deposition_date"   TEXT       // RCSB PDB 등록일
}

Table "structure_mutations" {
  "id"            INTEGER [pk]  // 자동 증가 기본키
  "structure_id"  TEXT          // pdb_structures 참조 (FK)
  "mutation"      TEXT          // 변이 표기 (e.g., K1110A, F1223Y) — UniProt 번호 기준
  "position"      INTEGER       // UniProt 기준 잔기 번호
  "mutation_type" TEXT          // 변이 종류 ('engineered' / 'natural_variant')
}

Table "ligands" {
  "id"          INTEGER [pk]  // 자동 증가 기본키
  "structure_id" TEXT         // pdb_structures 참조 (FK) — complex_type이 'ligand' 또는 'mixed'인 경우
  "ligand_id"   TEXT          // PDB Chem Comp ID (e.g., ATP, 3VC)
  "ligand_name" TEXT          // Ligand 화학명 — chem_comp API에서 획득
  "formula"     TEXT          // 화학식 (e.g., C10H15N5O10P2) — chem_comp API에서 획득
  "smiles"      TEXT          // SMILES 표기법 — chem_comp GraphQL API에서 획득
  "ligand_type" TEXT          // Ligand 분류 ('small_molecule' / 'peptide' / 'antibody_fragment')
}

Table "partner_proteins" {
  "id"                       INTEGER [pk]  // 자동 증가 기본키
  "structure_id"             TEXT          // pdb_structures 참조 (FK) — complex_type이 'protein-protein' 또는 'mixed'인 경우
  "entity_id"                TEXT          // RCSB entity ID (파트너 단백질 식별용)
  "partner_uniprot_id"       TEXT          // 파트너 단백질 UniProt Accession
  "partner_gene_name"        TEXT          // 파트너 단백질 유전자 이름 (e.g., HGF)
  "partner_chain_id"         TEXT          // 파트너 단백질 체인 ID (다중 체인은 partner_protein_chains 테이블 참조)
  "sequence_length"          INTEGER       // 파트너 단백질 서열 길이
  "organism"                 TEXT          // 파트너 단백질 유래 생물
  "partner_residue_range"    TEXT          // 파트너 단백질 구조 서열 범위 (e.g., 25-728)
  "partner_expression_system" TEXT         // 파트너 단백질 발현 숙주
}

Table "partner_protein_chains" {
  "id"         INTEGER [pk]  // 자동 증가 기본키
  "partner_id" INTEGER       // partner_proteins 참조 (FK) — 다중 체인 파트너 처리용
  "chain_id"   TEXT          // 개별 체인 ID (e.g., B, C)
}

Table "ptm_oligosaccharides" {
  "id"              INTEGER [pk]  // 자동 증가 기본키
  "structure_id"    TEXT          // pdb_structures 참조 (FK)
  "entity_id"       TEXT          // RCSB entity ID (당쇄 엔티티 식별용)
  "name"            TEXT          // 당쇄 이름 (e.g., NAG, MAN)
  "chain_id"        TEXT          // 당쇄가 속한 체인 ID
  "linked_chain"    TEXT          // 당쇄가 연결된 단백질 체인 ID
  "linked_position" INTEGER       // 당쇄가 연결된 단백질 잔기 번호
  "linked_residue"  TEXT          // 당쇄가 연결된 잔기 종류 (e.g., ASN — N-glycosylation)
}

Table "klifs_structures" {
  "id"               INTEGER [pk]  // 자동 증가 기본키
  "structure_id"     TEXT          // pdb_structures 참조 (FK)
  "klifs_id"         INTEGER       // KLIFS 내부 구조 ID
  "kinase_id"        INTEGER       // KLIFS 키나아제 ID
  "kinase_name"      TEXT          // 키나아제 이름 (KLIFS 기준)
  "family"           TEXT          // 키나아제 패밀리 (e.g., TK, CMGC)
  "dfg"              TEXT          // DFG 모티프 형태 ('in' / 'out' / 'out-like' / 'na')
  "ac_helix"         TEXT          // αC Helix 형태 ('in' / 'out' / 'na')
  "qualityscore"     REAL          // KLIFS 구조 품질 점수
  "missing_residues" INTEGER       // 누락된 잔기 수
  "missing_atoms"    INTEGER       // 누락된 원자 수
  "rmsd1"            REAL          // KLIFS RMSD1 (kinase domain 기준)
  "rmsd2"            REAL          // KLIFS RMSD2 (binding site 기준)
  "gatekeeper"       TEXT          // Gatekeeper 잔기 (e.g., T, M, F — 선택성 관련 핵심 잔기)
}

Table "paper_analysis" {
  "id"           INTEGER [pk]  // 자동 증가 기본키
  "structure_id" TEXT          // pdb_structures 참조 (FK, UNIQUE — 구조당 1건)
  "pdf_path"     TEXT          // 업로드된 PDF 파일 경로
  "status"       TEXT          // 분석 상태 ('none' / 'pending' / 'completed' / 'failed')
  "raw_text"     TEXT          // Claude API 분석 결과 원문 텍스트
  "analyzed_at"  TIMESTAMP     // 분석 완료 시각

}

Ref: "proteins"."uniprot_id"        < "protein_domains"."uniprot_id"
Ref: "proteins"."uniprot_id"        < "pdb_structures"."uniprot_id"
Ref: "pdb_structures"."structure_id" < "structure_mutations"."structure_id"
Ref: "pdb_structures"."structure_id" < "ligands"."structure_id"
Ref: "pdb_structures"."structure_id" < "partner_proteins"."structure_id"
Ref: "partner_proteins"."id"         < "partner_protein_chains"."partner_id"
Ref: "pdb_structures"."structure_id" < "ptm_oligosaccharides"."structure_id"
Ref: "pdb_structures"."structure_id" < "klifs_structures"."structure_id"
Ref: "pdb_structures"."structure_id" < "paper_analysis"."structure_id"
