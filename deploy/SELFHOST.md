# 내 PC 자체 호스팅 + Cloudflare Tunnel (무료)

Reflex Cloud 무료 티어가 유휴 시 꺼져(콜드스타트) "Cannot connect: timeout" 이 나므로,
**내 PC에서 앱을 항상 켜두고 Cloudflare Tunnel 로 공개**한다. 비용 0원, PC가 켜져 있는 동안 안정적.

```
[사용자] ─https→ [Cloudflare Tunnel] ─→ [내 PC: Reflex(prod) 단일 포트 8080]
                                                    └→ 데이터는 Supabase(서울)
```

★ Reflex 프로덕션 모드는 **프론트+백엔드+websocket 을 한 포트로** 서빙한다(검증됨:
  `App running at http://0.0.0.0:8080/`). 따라서 **Caddy 같은 리버스 프록시가 필요 없다.**
DB·Anthropic 키는 로컬 `.streamlit/secrets.toml` 에서 자동으로 읽으므로 별도 설정 불필요.

---

## 1. 사전 설치 (1회)

PowerShell 에서:

```powershell
winget install Cloudflare.cloudflared
```

설치 후 새 PowerShell 창에서 확인:

```powershell
cloudflared --version
```

(winget 이 없으면 https://github.com/cloudflare/cloudflared/releases 에서 `cloudflared-windows-amd64.exe`
를 받아 이름을 `cloudflared.exe` 로 바꾸고 PATH 폴더에 두면 된다. Caddy 는 필요 없다.)

---

## 2. 실행 — 방법 A: Quick tunnel (도메인 불필요, 가장 쉬움)

프로젝트 루트에서:

```powershell
cd D:\Projects\Project_PDB\protein-construct-builder
git pull origin supabase-migration
powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1
```

스크립트가 자동으로:
1. Quick tunnel 을 열어 **공개 URL**(`https://xxxx.trycloudflare.com`)을 만들고,
2. 그 URL 을 `API_URL` 로 잡아 **Reflex(prod)를 단일 포트(8080)로 빌드·실행**하고,
3. 터널을 그 포트에 연결한다.

콘솔에 뜬 **접속 주소**를 공유하면 된다. **이 PowerShell 창을 열어두는 동안** 서비스가 유지된다
(종료: `Ctrl+C`). 첫 실행은 프론트 프로덕션 빌드로 **수 분** 걸린다 — 로그에 `App Running` 이 뜰 때까지 기다린다.

> ⚠️ Quick tunnel 은 창을 닫거나 재시작하면 **URL 이 바뀐다.** 고정 주소가 필요하면 아래 방법 B.

---

## 3. 실행 — 방법 B: 고정 도메인 (URL 이 안 바뀜)

본인 도메인을 Cloudflare 에 추가(무료)했다면 항상 같은 URL 을 쓸 수 있다.

```powershell
# (1회) Cloudflare 로그인 + 터널 생성 + DNS 연결
cloudflared tunnel login
cloudflared tunnel create pdb
cloudflared tunnel route dns pdb pdb.mydomain.com
```

`~/.cloudflared/config.yml` 을 아래처럼(터널 id·hostname 교체). **service 는 로컬 8080 을 가리킨다:**

```yaml
tunnel: <TUNNEL_ID>
credentials-file: C:\Users\jk941\.cloudflared\<TUNNEL_ID>.json
ingress:
  - hostname: pdb.mydomain.com
    service: http://localhost:8080
  - service: http_status:404
```

그리고 두 창에서:

```powershell
# 터미널 1: 터널
cloudflared tunnel run pdb
# 터미널 2: 앱 (고정 URL 지정 — 스크립트는 quick tunnel 을 안 열고 이 URL 로 API_URL 만 잡음)
powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1 -PublicUrl "https://pdb.mydomain.com"
```

---

## 4. 확인 · 문제 해결

- 첫 실행은 프론트 빌드로 **수 분** 걸린다. `deploy\_logs\reflex.err` 에 에러가 없고,
  `reflex.out`(또는 콘솔)에 `App Running` 이 뜨면 준비된 것.
- 접속 안 되면 로그 확인: `deploy\_logs\reflex.err`, `deploy\_logs\cloudflared.err`.
- 포트 8080 이 이미 쓰이면 스크립트에 `-Port 8090` 처럼 다른 포트 지정(터널도 자동으로 그 포트에 붙음).
- PC 를 끄면 접속 불가(자체 호스팅 특성). 24시간 필요하면 PC 를 켜두거나 소형 VPS 로 이전.

---

## 5. ⚠️ 보안 — 현재 인증 없음

지금 앱에는 **비밀번호 게이트가 없다**(이전에 제거됨). 공개 URL 을 아는 사람은 누구나 접속해
데이터를 보고 Claude(유료 API)를 쓸 수 있다. Quick tunnel 은 URL 이 무작위라 노출 위험은 낮지만
안전하진 않다. 소수 공유라면 **공유 비밀번호 게이트 복원을 권장**한다(APP_PASSWORD 는 secrets 에 이미 있음).
필요하면 게이트 복원을 요청하면 바로 추가한다.
