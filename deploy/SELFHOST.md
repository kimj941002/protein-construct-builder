# 내 PC 자체 호스팅 + Cloudflare Tunnel (무료)

Reflex Cloud 무료 티어가 유휴 시 꺼져(콜드스타트) "Cannot connect: timeout" 이 나므로,
**내 PC에서 앱을 항상 켜두고 Cloudflare Tunnel 로 공개**한다. 비용 0원, PC가 켜져 있는 동안 안정적.

```
[사용자] ─https→ [Cloudflare Tunnel] ─→ [내 PC: Caddy :8080] ─→ Reflex 프론트(3000)+백엔드(8000)
                                                              └→ 데이터는 Supabase(서울)
```

DB·Anthropic 키는 로컬 `.streamlit/secrets.toml` 에서 자동으로 읽으므로 별도 설정 불필요.

---

## 1. 사전 설치 (1회)

PowerShell 에서:

```powershell
winget install Cloudflare.cloudflared
winget install CaddyServer.Caddy
```

설치 후 새 PowerShell 창을 열어 확인:

```powershell
cloudflared --version
caddy version
```

둘 다 버전이 나오면 준비 완료. (winget 이 없으면 각 사이트에서 exe 를 받아 PATH 에 두어도 됨.)

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
2. 그 URL 을 `API_URL` 로 잡아 Reflex(prod) 를 빌드·실행하고,
3. Caddy 로 프론트+백엔드를 합쳐 터널에 연결한다.

콘솔에 뜬 **접속 주소**를 협업자에게 공유하면 된다. **이 PowerShell 창을 열어두는 동안** 서비스가 유지된다(종료: `Ctrl+C`).

> ⚠️ Quick tunnel 은 창을 닫거나 재시작하면 **URL 이 바뀐다.** 매번 새 URL 을 공유해야 하므로,
> 고정 주소가 필요하면 아래 **방법 B**(도메인)를 쓴다.

---

## 3. 실행 — 방법 B: 고정 도메인 (URL 이 안 바뀜)

본인 도메인을 Cloudflare 에 추가(무료)했다면 고정 주소로 항상 같은 URL 을 쓸 수 있다.

```powershell
# (1회) Cloudflare 로그인 + 터널 생성 + DNS 연결
cloudflared tunnel login
cloudflared tunnel create pdb
cloudflared tunnel route dns pdb pdb.mydomain.com
```

`~/.cloudflared/config.yml` 을 `deploy/cloudflared-config.example.yml` 참고해 작성(tunnel id·hostname 교체).
그리고 config 로 터널을 띄운 뒤, 고정 URL 을 넘겨 실행:

```powershell
# 터미널 1: 터널
cloudflared tunnel run pdb
# 터미널 2: 앱 (고정 URL 지정)
powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1 -PublicUrl "https://pdb.mydomain.com"
```

(방법 B 에서는 스크립트가 quick tunnel 을 열지 않고, 지정한 URL 로 API_URL 만 잡아 앱+Caddy 를 띄운다.)

---

## 4. 확인 · 문제 해결

- 접속했는데 흰 화면/연결 안 됨 → `deploy\_logs\reflex.err`, `caddy.err`, `cloudflared.err` 확인.
- 첫 실행은 Reflex 프론트 빌드로 **수 분** 걸린다. 로그에 `App running` 이 뜨면 준비된 것.
- 포트 충돌 시: 스크립트 인자로 `-FrontendPort`, `-BackendPort`, `-CaddyPort` 변경 가능.
- PC 를 끄면 접속 불가(자체 호스팅 특성). 24시간 필요하면 PC 를 켜두거나 소형 VPS 로 이전.

---

## 5. ⚠️ 보안 — 현재 인증 없음

지금 앱에는 **비밀번호 게이트가 없다**(이전에 제거됨). 공개 URL 을 아는 사람은 누구나 접속해
데이터를 보고 Claude(유료 API)를 쓸 수 있다. Quick tunnel 은 URL 이 무작위라 노출 위험은 낮지만
안전하진 않다.

소수에게만 공유하려면 **공유 비밀번호 게이트를 다시 넣는 것을 권장**한다(APP_PASSWORD 는
secrets 에 이미 있음). 필요하면 게이트 복원을 요청하면 바로 추가한다.
