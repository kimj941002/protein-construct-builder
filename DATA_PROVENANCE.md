# 데이터 출처 & 정합성 (Data Provenance)

> cMET 플랫폼이 표시하는 **과학적으로 하중이 큰 값들의 출처·좌표계·검증 방법**을 명시한다.
> (피드백 `cMET_app_integration_feedback.md` P1-1 / P1-2 대응)

---

## 1. 잔기·변이 번호 체계 — UniProt P08581 canonical 좌표 (C3)

**좌표계: UniProt P08581 canonical (isoform 1, 1390 aa).** 모든 변이 `position` 과
구조 `residue_range` 는 author/SEQRES 번호가 아니라 **canonical 좌표**로 정규화되어 저장된다.

| 값 | 적재 경로 | 좌표 근거 |
|---|---|---|
| 변이 `X{pos}Y` | `mutation_analyzer.compare_sequences()` | SIFTS(PDBe) 매핑의 `unp_pos`. WT 글자 `X` 는 canonical 서열에서 직접 읽음 |
| `residue_range` | `pdb_fetcher` (`rcsb_polymer_entity_align.aligned_regions.ref_beg_seq_id`) | RCSB 정렬의 UniProt 참조 좌표 |

### WT 검증 게이트
- `mutation_analyzer.validate_mutation_wt(code, canonical_seq)` / `apply_wt_gate()` 가
  저장 직전 `canonical_seq[pos-1] == X` 를 검사하여 **불일치 행을 차단 + 로깅**한다.
- ⚠️ **정직한 한계**: 현행 적재 경로는 WT 글자를 canonical 에서 *생성*하므로 이 게이트는
  현재 데이터에 대해 **no-op**(항상 통과)이다. 향후 수동입력·논문추출 등 *다른* 적재 경로가
  추가될 때 좌표계 오염을 막는 **안전망**으로 둔 것이다.

### 검증 결과 (2026-06-30 기준, MET/P08581)
- canonical 길이 **1390 aa** ✓ (isoform 1)
- 테스트 케이스 WT 일치: **D1228·Y1230·Y1234·Y1235·L1157(gatekeeper)** 전부 일치 ✓
- 저장된 변이 **375개 전부** WT 가 canonical 과 일치(불일치 0) ✓
- `residue_range` **전부 1~1390 범위 내**(이상 0) ✓
- `source_position`(author 원문 번호) 별도 보존: **미구현(백로그)** — 현재는 author 번호를
  per-mutation 으로 받지 않고 곧장 canonical 로 정규화하므로 손실되는 원문이 없어 정합성에는
  영향 없음. 추적성 강화가 필요하면 컬럼 추가 + 재적재로 확장.

---

## 2. DFG / αC-helix 입체구조 분류 — KLIFS (C-앱 핵심)

**출처: KLIFS (Kinase–Ligand Interaction Fingerprints and Structures), https://klifs.net**
— Dunbrack lab 계열의 **표준 키나아제 입체구조 분류 DB**(권위 있는 외부 소스, 피드백 분류상 case-a).

| 항목 | 값 |
|---|---|
| 소스 | KLIFS REST API |
| 엔드포인트 | `GET https://klifs.net/api/structures_pdb_list?pdb-codes={PDB_ID}` |
| 매핑 키 | PDB ID (+ 타겟 chain 우선 매칭) — `klifs_fetcher.process_klifs()` |
| 가져오는 필드 | 응답의 `DFG`(in/out/na), `aC_helix`(in/out/na) → `klifs_structures.dfg`, `.ac_helix` |
| 미등록 처리 | 비키나아제/미등록은 sentinel(NULL) 행으로 저장해 재조회 방지 |

### Inhibitor type 컬럼은 **파생 휴리스틱**(KLIFS 직접값 아님)
`pcb_reflex._inhibitor_type(dfg, ac)` 가 KLIFS 의 DFG/αC 로부터 **추정**한다:

| DFG | αC-helix | → Inhibitor type | 근거 |
|---|---|---|---|
| out | (any) | **Type II** | Type II ≈ DFG-out 포켓 결합자 |
| in | out | **Type I½** | DFG-in / αC-out |
| in | in | **Type I** | 활성형(DFG-in/αC-in) ATP-경쟁 |
| na/그외 | | `-` | 판정 불가 |

> 이는 결합자 자체의 실험적 분류가 아니라 **수용체 입체구조 기반 추정**이다. 엄밀한 Type 분류는
> 리간드–포켓 분석이 필요. 컬럼명/툴팁에서 "구조 기반 추정"임을 드러낸다.

### 검증 (MET/P08581, 130 구조 중 100개에 KLIFS 값)
DFG/αC 분포: `in/out 64`, `out/in 22`, `out/out 6`, `in/in 5`, (na 3), (없음 30).
→ MET 의 알려진 저해제 구조 경향(Type I½·Type II 다수)과 **생물학적으로 타당**.

- ⚠️ **정직한 한계**: Kincore 등 *다른* 표준과의 표본 교차검증은 **미실시**. 근거는 KLIFS 자체가
  표준 분류원이라는 점 + 위 분포 타당성. 더 높은 보증이 필요하면 Kincore 표본 10~20개 대조를
  백로그로 추가(현행 KLIFS 접근/스키마는 작업 시점 재확인 필요).

---

## 3. 미구현(백로그) — v2 백엔드 항목

코드 전수 검색 결과 아래는 **현재 미존재**(이번 범위 비강제, 기록만):
`[C1]` bioactivities 단위정규화(`value_nM_normalized`) · `[C2]` median pChEMBL 집계뷰 ·
`[C4]` `curation_queue` · `[C5]` `target_relevance` 가중. (ChEMBL 연동 자체가 미구현)
