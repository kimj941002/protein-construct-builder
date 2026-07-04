# 문헌(Literature) 탭 전면 개편 — Playground 시스템 전략서

> 논문 탭 → **문헌** 으로 개편. Claude API 기반 연구 Playground(살아있는 정리본 + 대화누적
> + 중첩 + 문헌↔PDB/약물 연결). 사용자 요청 5개를 빠짐없이 반영한 실현 가능 설계.

## 0. 사용자 요청 원문 매핑
1. "주제 생성" 클릭+이름 → Playground 1개 생성.
2. 대화형 LLM. **차이점**: 대화를 Raw data 로 누적 + 답변마다 Playground 전체를 **일목요연하게
   정리**(과요약 금지, 부차 제외, 커버된 내용을 체계적으로). 재오픈 시 정리본을 보고 필요하면
   대화를 클릭해 열람.
3. 대주제→중주제 분기 시 Playground 안에 **또 다른 Playground** 생성(중첩).
4. Playground 내 업로드 문헌을 DB 논문과 대조(이름 또는 더 나은 식별자) → 같으면 **PDB·약물
   정보와 유기적 연결**.
5. **자유로운 삭제·수정**(기존 업로드 논문도 삭제 가능하게).

## 1. 데이터 모델 (Supabase) — 옛 llm_rooms/llm_messages 재활용·확장
```sql
CREATE TABLE playgrounds (
  id          BIGINT PK,
  parent_id   BIGINT REFERENCES playgrounds(id) ON DELETE CASCADE,  -- [req3] 중첩
  uniprot_id  TEXT,              -- 스코프(단백질별) 또는 NULL(전역) — §결정1
  name        TEXT,
  digest      TEXT,              -- [req2] 살아있는 정리본
  digest_updated_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ
);
CREATE TABLE playground_messages (       -- [req2] Raw 누적
  id BIGINT PK, playground_id BIGINT REF playgrounds ON DELETE CASCADE,
  role TEXT,            -- 'user'|'assistant'
  content TEXT, created_at TIMESTAMPTZ
);
CREATE TABLE playground_papers (         -- [req4] 문헌 연결
  id BIGINT PK, playground_id BIGINT REF playgrounds ON DELETE CASCADE,
  title TEXT, doi TEXT, storage_path TEXT,   -- 업로드/참조
  matched_structure_id TEXT,           -- DB papers 매칭 결과(있으면 PDB/약물 연결)
  matched BOOLEAN
);
```
- 기존 `papers`(doi,title,structure_id)·`paper_analysis`(PDB별 PDF)와 매칭에 사용.

## 2. Claude API 흐름 (매 사용자 메시지)
1. 컨텍스트 조립: system + 이 playground 의 **메시지 히스토리(raw)** + **연결 문헌**(제목/초록/기존
   분석본) + (매칭된 문헌의) **PDB·약물 팩트**.
2. Claude 답변 생성 → `playground_messages` 에 append(raw).
3. **정리본 재생성(2차 호출)**: "이 Playground 가 다룬 내용을 체계적으로 정리(과요약·부차 제외)".
   → `playgrounds.digest` 갱신. (§결정2 = 매턴/증분/버튼)
4. UI: digest 갱신, raw 는 접힘/펼침.
- 모델: 기본 `claude-opus-4-8`(품질) — §결정4. 정리는 sonnet 로 비용절감 가능.

## 3. 중첩 Playground [req3]
- `parent_id` 트리. 열린 Playground 에서 "＋ 하위 Playground" → 자식 생성(옵션: 부모 digest 를
  시드 컨텍스트로 — 옛 branch_room 방식). UI: 좌측 트리 + 브레드크럼.

## 4. 문헌↔PDB/약물 연결 [req4]
- Playground 에 문헌 추가: PDF 업로드(방금 만든 Storage 직접업로드 재사용) 또는 DOI/제목 참조.
- 매칭: **DOI 정확일치 우선**, 없으면 제목 정규화 후 유사도. 매칭되면 `matched_structure_id` →
  기존 `get_structures_for_drug`/`get_drugs_for_structure`/구조 정보로 **PDB·약물 카드 표시** +
  LLM 컨텍스트로 주입(유기적 연결).

## 5. 삭제·수정 [req5]
- Playground: 삭제(자식·메시지 cascade), 이름 수정, digest 수동 재생성.
- 메시지: 개별 삭제/수정.
- **기존 업로드 논문 삭제**: 현재 문헌 목록에 삭제 버튼(paper_analysis/papers 행 + Storage 객체 제거).

## 6. UI (문헌 탭)
- 탭명 논문→**문헌**.
- 상단: 기존 업로드 문헌 목록(삭제 가능) — 유지·개선.
- 본체: 좌측 **Playground 트리** + "＋ 주제 생성"(이름 입력). 우측 = 선택 Playground:
  - **정리본(digest) 뷰가 메인** + 접힌 raw 대화(클릭 펼침) + 입력창 + 연결 문헌 패널(PDB/약물
    링크) + "＋ 하위 Playground".

## 7. 단계별 로드맵 (컨텍스트 분할 대비 — 단계마다 커밋·검증)
- **P1**: 탭명 변경 + 기존 논문 삭제 기능(빠른 착수). [req5 일부]
- **P2**: playgrounds/messages DB + 기본 Claude 채팅 + raw 누적. [req1,2 일부]
- **P3**: 정리본(digest) 재생성. [req2]
- **P4**: 중첩 Playground. [req3]
- **P5**: 문헌 업로드+DB 매칭+PDB/약물 연결. [req4]
- **P6**: 편집/삭제 마감 + 인터페이스 다듬기. [req5]

## 8. 결정 확정 (사용자, 2026-07)
1. **스코프 = 단백질별**. playgrounds.uniprot_id = 현재 단백질. 문헌 탭 안에서 그 단백질 연구.
2. **정리본 = 수동 '정리' 버튼**. 매턴 자동 갱신 안 함. 버튼 클릭 시 digest 생성/갱신(Sonnet).
3. **LLM 권한 = 강력(인터넷+앱DB+문헌 모두)**. 기본 대화형처럼 진행하되:
   - 툴: (a) **web_search**(Claude API 서버툴) — 인터넷, (b) **앱 DB 조회툴**(read-only run_sql
     또는 안전 쿼리함수) — 사용자가 "앱 DB로 분석하라" 하면 내부 데이터 직접 분석, (c) 연결 문헌 컨텍스트.
   - read-only 강제(SELECT만), 파괴적 쿼리 차단.
4. **모델 = 채팅 opus-4-8 / 정리 sonnet-4-6.**

## 9. 리스크·비용 메모
- 정리본 매턴 재생성은 히스토리 길수록 토큰↑ → 증분 권장(요청은 매턴이므로 절충: 매턴 증분 갱신).
- Reflex 스트리밍 채팅은 background 이벤트 + 폴링/yield 로 구현(옛 customllm 참고).
- Claude API 키는 secrets 의 ANTHROPIC_API_KEY(이미 있음).
