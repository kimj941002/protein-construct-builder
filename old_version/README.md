# old_version — 폐기된 코드 보관소

cMET 통합 워크스페이스로 전면 개편(2026-06-30)하면서 **더 이상 사용하지 않는** 코드를
한곳에 모았다. 현재 살아있는 앱(`pcb_reflex/pcb_reflex.py`)은 이 폴더의 어떤 파일도 import 하지 않는다.

| 파일 | 무엇이었나 | 폐기 이유 |
|---|---|---|
| `app.py` | 구 Streamlit + st_aggrid UI | Reflex 앱으로 대체 |
| `taipy_app.py` | 구 Taipy GUI 프로토타입 | Reflex 앱으로 대체 |
| `knowledge.py` | Knowledge Base(주제·인사이트·의미검색) 서비스 | 🧠 Knowledge Base 페이지 삭제 |
| `customllm.py` | 트리형 대화방(Custom LLM) 서비스 | 🤖 Custom LLM 페이지 삭제 |
| `embeddings.py` | pgvector 임베딩 헬퍼 | knowledge.py 전용 → 동반 폐기 |
| `llm_query.py` | 자연어→SQL 질의 (구 app.py / customllm 용) | 동반 폐기 |
| `chat_store.py` | 채팅 기록 저장 (구 app.py 용) | 동반 폐기 |

## 참고
- 관련 DB 테이블(`topics`/`insights`/`embeddings`/`chats`/`llm_rooms`/`llm_messages`)은
  `supabase_schema.sql` 에 아직 정의되어 있으나 현재 앱에서 쓰지 않는다. 완전 제거를 원하면
  스키마에서 해당 블록을 함께 지우면 된다.
- 되살리려면 파일을 상위 폴더로 옮기고 import 경로를 복구하면 된다.
