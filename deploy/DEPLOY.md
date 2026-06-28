# 배포 가이드 — 내 컴퓨터 + Cloudflare Tunnel + 공유 비밀번호

소수(1~10명)에게 **링크 + 공유 비밀번호**로만 접속을 허용하는 가장 단순한 방법.

```
[유저] ──https──▶ [Cloudflare] ──터널──▶ [내 PC: Caddy :8080] ──▶ Reflex(프론트3000 + 백엔드8000)
                                                                     ▲ 앱 내 '공유 비밀번호' 로그인 게이트
```
- **접근 제한**: 앱에 들어오면 비밀번호 화면이 먼저 뜸 → 비밀번호 아는 사람만 입장.
- **데이터**: 모두 같은 Supabase 를 공유(공동 지식베이스). PC 가 켜져 있어야 접속 가능.

---

## 0. 준비물 (1회)
- Cloudflare 계정(무료) + **Cloudflare 에 등록된 도메인 1개** (안정적인 고정 링크용)
  - 도메인이 없으면 → 맨 아래 "도메인 없이(임시 링크)" 참고.
- 설치: **Caddy** (`https://caddyserver.com/download`), **cloudflared** (`https://github.com/cloudflare/cloudflared/releases`)

## 1. 비밀키 설정
`.streamlit/secrets.toml` (이미 .gitignore 됨) 에:
```toml
ANTHROPIC_API_KEY    = "sk-ant-..."
SUPABASE_DB_PASSWORD = "실제_DB_비밀번호"
SUPABASE_PROJECT_REF = "izgdypalbnauzidoffyx"
SUPABASE_POOLER_HOST = "aws-1-ap-northeast-2.pooler.supabase.com"
APP_PASSWORD         = "공유할_강력한_비밀번호"   # ← 이걸 아는 사람만 입장
```
> `APP_PASSWORD` 가 비어 있으면 게이트가 꺼집니다(로컬 개발용). 배포 땐 반드시 채우세요.

## 2. Reflex 앱을 prod 로 실행 (터미널 A)
```powershell
cd D:\Projects\Project_PDB\protein-construct-builder
pip install -r requirements-reflex.txt
$env:API_URL = "https://pdb.example.com"   # ← 본인 공개 도메인
python -X utf8 -m reflex run --env prod
```
→ 프론트 `:3000`, 백엔드 `:8000` 가동.

## 3. Caddy 실행 (터미널 B) — 두 포트를 8080 하나로 합침
```powershell
cd D:\Projects\Project_PDB\protein-construct-builder
caddy run --config deploy/Caddyfile
```
→ `http://localhost:8080` 에서 앱 전체가 보이면 성공.

## 4. Cloudflare Tunnel (터미널 C)
```powershell
cloudflared login
cloudflared tunnel create pdb-app
# 생성된 <TUNNEL_ID> 로 deploy/cloudflared-config.example.yml 을 채워 ~/.cloudflared/config.yml 로 저장
cloudflared tunnel route dns pdb-app pdb.example.com
cloudflared tunnel run pdb-app
```
→ 이제 `https://pdb.example.com` 으로 외부에서 접속 가능.

## 5. 공유
협업자에게 **링크(`https://pdb.example.com`) + 공유 비밀번호** 를 전달. 그 둘을 아는 사람만 입장.

---

## 도메인 없이 (임시 링크, 0설정)
2·3번까지 한 뒤:
```powershell
cloudflared tunnel --url http://localhost:8080
```
→ `https://랜덤문자.trycloudflare.com` 링크가 출력됨. 재시작하면 주소가 바뀝니다(임시용).
이때 2번의 `$env:API_URL` 을 출력된 그 주소로 맞춰 다시 실행해야 합니다.

## 보안 메모
- `secrets.toml` 은 절대 깃에 올리지 않음(이미 .gitignore). 비밀번호 유출 시 `APP_PASSWORD` 교체.
- 공유 비밀번호 1개라 "유출되면 누구나" 들어옵니다. 더 강한 통제가 필요해지면 Cloudflare Access(이메일 허용목록)나 Supabase Auth 로 올릴 수 있음.
- PC 가 꺼지면 접속 불가 → 24시간 필요하면 작은 VPS 로 옮기는 걸 권장(같은 구성 그대로).
