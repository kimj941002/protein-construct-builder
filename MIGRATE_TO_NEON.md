# Supabase → Neon 이전 프로토콜

> 목적: Supabase 무료의 **자동정지(수동 Restore 반복)** 고통 탈피 → **Neon 무료**(접속하면 1초 내 자동으로 깨어남, 수동 복구 불필요).
> 데이터(단백질·PDB·논문·약물·Playground 전부)를 그대로 옮기고, **앱은 연결 주소만 교체**합니다. 코드 로직 변경 없음.
>
> **역할 분담**: 🧑 = 사용자(가입·주소 전달만) / 🤖 = Claude(기술 작업 전부 대행)

---

## 한눈에 보기

| 단계 | 누가 | 내용 | 소요 |
|---|---|---|---|
| 0 | 🧑 | Supabase 프로젝트 Restore(Active) | 2~5분 |
| 1 | 🧑 | Neon 가입 + 프로젝트 생성 → **연결 주소 복사해 Claude에게 전달** | ~10분 |
| 2 | 🤖 | Neon에 스키마 생성 + 데이터 전부 복사 + 검증 | 자동 |
| 3 | 🤖 | 앱이 Neon을 쓰도록 secrets/코드 교체 + 로컬 테스트 | 자동 |
| 4 | 🤖 | 업로드 PDF(Storage) 처리 방침 결정 | 이전 후 |

사용자님이 실제로 하실 일은 **0단계와 1단계뿐**입니다. 나머지는 URL만 주시면 제가 합니다.

---

## 0단계 🧑 — Supabase 켜기 (데이터를 읽어와야 하므로)
1. https://supabase.com/dashboard → 프로젝트 `izgdypalbnauzidoffyx`
2. **Paused면 Restore** 클릭 → **Active(정상)** 될 때까지 대기.
3. 이전 작업이 끝날 때까지 켜둔 채로 두세요.

## 1단계 🧑 — Neon 가입 + 프로젝트 (약 10분)
1. https://neon.tech → **Sign up** (GitHub/Google 계정으로 바로 가능, 무료, 카드 불필요).
2. **Create a project**:
   - Name: 아무거나 (예: `pdb`)
   - Postgres version: 기본(최신)
   - **Region: 가까운 아시아** — **Singapore (ap-southeast-1)** 권장. (한국과 가까워 빠름)
3. 프로젝트가 만들어지면 화면에 **Connection string**(연결 문자열)이 보입니다.
   - **"Pooled connection"** 스위치를 **켜고**,
   - 아래처럼 생긴 **전체 문자열을 복사**하세요:
     ```
     postgresql://<유저>:<비밀번호>@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
     ```
   - (나중에 다시 볼 수 있음: 프로젝트 → **Connection Details** → Pooled)
4. **이 연결 문자열을 Claude(저)에게 그대로 붙여넣어 전달**하세요.
   - ⚠️ 비밀번호가 들어있는 민감 정보입니다. 전달 후, 원하면 Neon 대시보드에서 비밀번호를 **Reset** 해 새로 발급할 수도 있습니다(그럼 새 문자열을 다시 주시면 됩니다).

> 여기까지가 사용자님 몫입니다. 이후는 URL을 받은 제가 진행합니다. 👇

---

## 2단계 🤖 — 데이터 이전 (Claude 실행)
준비된 스크립트 `migrate_to_neon.py`로:
```bash
NEON_DATABASE_URL="<받은 연결 문자열>" python -X utf8 migrate_to_neon.py
```
- Neon에 **pgvector 확장 + 전체 스키마(supabase_schema.sql) 생성**
- 모든 테이블 데이터를 **원본(Supabase) → Neon 복사** (외래키 우회로 순서 무관)
- IDENTITY 시퀀스 보정
- **원본과 Neon의 행 수를 테이블별로 대조**해 이전이 완전한지 검증
- 원본(Supabase)은 **읽기만** 하므로 원본 데이터는 안전.

## 3단계 🤖 — 앱 연결 교체 (Claude 실행)
- `db_config.py`에 **`DATABASE_URL` 우선** 로직 추가(있으면 그걸로 접속, 없으면 기존 Supabase 방식 — 안전한 후방호환).
- `.streamlit/secrets.toml`에 `DATABASE_URL = "<Neon 문자열>"` 추가.
- 로컬에서 `reflex run --backend-port 8060` 실행 → 목록·검색·클릭 정상 동작 확인.
- 데스크탑·노트북 **양쪽 모두** 이 secrets를 쓰면 됨(파일만 동일하게 두기).

## 4단계 🤖 — 업로드 PDF(파일) 처리
- Neon은 **DB(표 데이터)**만 있고 **파일 저장소는 없습니다.**
- 논문 **분석 결과(구조화 데이터)는 DB에 있어 이미 이전**됩니다. **원본 PDF 파일**만 별도 이슈.
- 두 방법 중 택1 (이전 완료 후 결정):
  - **(a) 권장**: 새로 올리는 PDF는 **DB에 직접 저장**(bytea)하도록 코드 소폭 조정. 파일저장소 불필요.
  - **(b)**: PDF 파일만 당분간 **Supabase Storage 유지**(단, 그러면 Supabase를 파일용으로만 살려둬야 함).
- 대부분의 사용에는 (a)로 충분합니다.

---

## 완료 후
- **Supabase는 더 이상 안 씀** → 정지되든 말든 무관. 원하면 나중에 삭제.
- Neon은 **자동으로 깨어나므로 수동 Restore가 사라짐.**
- 데스크탑·노트북 어디서나 지금처럼 접속(같은 Neon DB 공유).

## 되돌리기(안전장치)
- 이전 중 원본은 안 건드립니다. 문제가 생기면 `secrets.toml`에서 `DATABASE_URL`만 지우면 **즉시 Supabase로 원복**됩니다.

---

## 지금 사용자님이 할 일 요약
1. **Supabase Restore**(Active 확인)
2. **Neon 가입 → 프로젝트 생성(Singapore) → Pooled 연결 문자열 복사**
3. 그 **연결 문자열을 저에게 전달**

→ 그러면 2~4단계는 제가 처리하고, 끝나면 로컬 앱이 Neon으로 붙어 **자동정지 걱정 없이** 돌아갑니다.
