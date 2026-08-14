# Oracle Cloud 무료 서버 구축 프로토콜 (앱 + PostgreSQL, 서울)

> 목표: **Oracle Always Free VM(서울)** 하나에 **앱 + PostgreSQL** 을 올려
> ① 어디서나 웹 접속 ② 자동정지 없음 ③ 빠름(서울, 앱·DB 한 곳) ④ 평생 무료.
>
> **역할**: 🧑 = 사용자(웹 콘솔 클릭 + SSH 접속) / 🤖 = Claude(명령어 전부 제공 + 데이터 이전 + 문제해결)
>
> **진행 방식**: 아래를 위→아래 순서로. **각 단계에서 막히거나 에러가 나면 그 화면/출력을 저에게 붙여넣으세요.** 바로 잡아드립니다. 한 번에 다 하려 하지 말고 단계별로 확인하며 갑니다.

---

## 준비물
- 신용/체크카드 1개 (본인확인용, **과금 없음**)
- 이메일, 휴대폰
- Windows의 **PowerShell** (SSH 내장되어 있음 — 별도 설치 불필요)

---

## PHASE 1 🧑 — Oracle 가입 + 서울 리전
1. https://www.oracle.com/cloud/free/ → **Start for free**
2. 가입 진행:
   - 이메일/비밀번호, 휴대폰 인증
   - **Country/Territory: South Korea**
   - **Home Region: 반드시 `South Korea Central (Seoul)` 선택** ⚠️ (나중에 변경 불가, 속도의 핵심)
   - 카드 등록(본인확인용, 청구 안 됨)
3. 가입 완료 → **OCI 콘솔**(cloud.oracle.com) 로그인.

## PHASE 2 🧑 — VM(서버) 생성
1. 콘솔 좌상단 ☰ 메뉴 → **Compute → Instances → Create instance**
2. 설정:
   - **Name**: `pdb-server` (아무거나)
   - **Image and shape** → **Edit**:
     - Image: **Canonical Ubuntu 24.04** (또는 22.04)
     - Shape: **Change shape** → **Ampere (ARM)** → `VM.Standard.A1.Flex`
       - OCPU: `2`, Memory: `12 GB` (Always Free 한도 4 OCPU/24GB 내)
       - ⚠️ "Out of capacity" 뜨면 → OCPU 1 / 6GB 로 낮추거나, 그래도 안 되면
         **AMD `VM.Standard.E2.1.Micro`(1GB)** 로 시작(항상 가능, 다소 빡빡하지만 됨).
   - **Networking**: 기본값(새 VCN 자동 생성) + **Assign a public IPv4 address: 예** 확인
   - **Add SSH keys**: **Generate a key pair for me** →
     **Save private key** 클릭해 `.key` 파일을 **PC에 저장**(예: `C:\Users\jk941\.ssh\pdb.key`). ★분실 주의(접속에 필수)
3. **Create** → 몇 분 후 상태 **RUNNING**.
4. 인스턴스 상세에서 **Public IP address** 를 메모 (예: `152.x.x.x`). → 저에게 알려주세요.

## PHASE 3 🧑 — 방화벽(포트) 열기 (Oracle 콘솔)
앱이 쓸 포트(**8080**)를 외부에 개방합니다.
1. 인스턴스 상세 → **Virtual cloud network** 링크 클릭 → **Security Lists** → 기본 목록 클릭
2. **Add Ingress Rules**:
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: **TCP**
   - Destination Port Range: `8080`
   - **Add Ingress Rules** 저장
   - (SSH 22 는 이미 열려 있음)

## PHASE 4 🧑 — SSH 접속 (PowerShell)
PC의 PowerShell에서 (키 경로·IP 본인 것으로):
```powershell
ssh -i C:\Users\jk941\.ssh\pdb.key ubuntu@<PUBLIC_IP>
```
- 처음이면 `yes` 입력.
- "Permissions too open" 오류 시:
  ```powershell
  icacls C:\Users\jk941\.ssh\pdb.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
  ```
- 접속되면 프롬프트가 `ubuntu@pdb-server:~$` 로 바뀜. → **여기부터는 제가 드리는 명령어를 붙여넣기.**

---

## PHASE 5 🤖 — 서버 세팅 (SSH 창에 순서대로 붙여넣기)
> 아래는 표준 절차입니다. 실제 진행 시 제가 사용자님 환경(포트·비번 등)에 맞춰 확정본을 드립니다.

**(1) 시스템 + 필수 패키지 + PostgreSQL 설치**
```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 python3-venv python3-pip git postgresql postgresql-contrib
```

**(2) pgvector 확장** (의미검색용 — 현재 앱은 미사용이라, 설치 실패해도 제가 스키마에서 빼고 진행 가능)
```bash
PGVER=$(ls /usr/lib/postgresql/ | head -1)
sudo apt -y install postgresql-$PGVER-pgvector || echo "pgvector 없음 → Claude에게 알려주세요(스킵 처리)"
```

**(3) DB·사용자 생성** (비밀번호 `STRONGPASS` 는 제가 안전한 값으로 지정해 드림)
```bash
sudo -u postgres psql -c "CREATE USER pdbuser WITH PASSWORD 'STRONGPASS';"
sudo -u postgres psql -c "CREATE DATABASE pdb OWNER pdbuser;"
sudo -u postgres psql -d pdb -c "CREATE EXTENSION IF NOT EXISTS vector;" || true
```

**(4) 코드 내려받기 + 파이썬 패키지**
```bash
cd ~ && git clone https://github.com/kimj941002/protein-construct-builder.git
cd protein-construct-builder && git checkout supabase-migration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**(5) 접속 정보 파일 만들기** (`.streamlit/secrets.toml`)
- 여기에 **로컬 DB 주소(DATABASE_URL)** + **ANTHROPIC_API_KEY** + (이전용) Supabase 값을 넣습니다.
- 민감 정보라, 제가 형식을 드리면 사용자님이 값만 채워 만드시거나, 제가 안전 값으로 채운 명령을 드립니다. 예:
```bash
mkdir -p ~/protein-construct-builder/.streamlit
cat > ~/protein-construct-builder/.streamlit/secrets.toml <<'EOF'
DATABASE_URL = "postgresql://pdbuser:STRONGPASS@localhost:5432/pdb"
ANTHROPIC_API_KEY = "sk-ant-..."     # 데스크탑 secrets.toml 에서 복사
# ↓ 이전(마이그레이션) 동안만 필요, 끝나면 지워도 됨
SUPABASE_PROJECT_REF = "izgdypalbnauzidoffyx"
SUPABASE_POOLER_HOST = "aws-1-ap-northeast-2.pooler.supabase.com"
SUPABASE_DB_PASSWORD = "..."          # 데스크탑 값 복사
SUPABASE_SERVICE_KEY = "..."          # 데스크탑 값 복사
EOF
```

## PHASE 6 🤖 — Supabase 데이터 → 서버 로컬 Postgres 이전
> ⚠️ 이 단계 동안 **Supabase 를 Restore(Active)** 상태로 켜두세요(원본을 읽어와야 함).
```bash
cd ~/protein-construct-builder && source .venv/bin/activate
NEON_DATABASE_URL="postgresql://pdbuser:STRONGPASS@localhost:5432/pdb" python migrate_to_neon.py
```
- 스크립트가 스키마 생성 + 전체 데이터 복사 + **행 수 대조 검증**까지 자동으로 합니다.
- 끝나면 이제 데이터는 **서버 로컬 Postgres** 에 있습니다. (Supabase 는 더 안 씀)

## PHASE 7 🤖 — 앱을 24시간 서비스로 등록 (systemd)
공개 주소로 접속되게 `API_URL` 을 지정하고, 꺼지지 않게 서비스 등록:
```bash
sudo tee /etc/systemd/system/pdb.service >/dev/null <<EOF
[Unit]
Description=PDB database app
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/protein-construct-builder
Environment=API_URL=http://<PUBLIC_IP>:8080
ExecStart=/home/ubuntu/protein-construct-builder/.venv/bin/python -X utf8 -m reflex run --env prod --backend-port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now pdb
sudo systemctl status pdb --no-pager
```

**서버 OS 방화벽도 열기** (Oracle Ubuntu 는 iptables 로 기본 차단):
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

## PHASE 8 🧑 — 접속 확인
아무 컴퓨터 브라우저에서:
```
http://<PUBLIC_IP>:8080
```
→ 앱이 뜨고, 클릭이 빠릿하면 성공. (첫 빌드로 서비스 기동에 2~3분 걸릴 수 있음)

## PHASE 9 🤖 — 보안 (공개 노출이므로 필수)
- 지금 앱은 **인증이 없어** URL 아는 사람은 누구나 접속합니다. → **공유 비밀번호 게이트를 복원**해 드리겠습니다(간단한 코드 추가 + 재시작).
- (선택) 도메인 있으면 Caddy 로 **HTTPS** + 예쁜 주소 설정 가능.

---

## 완료 후 그림
```
[Oracle VM · 서울 · 24시간]  Reflex 앱 + PostgreSQL   ← 앱·DB 한 곳 = 빠름
        │ http://<PUBLIC_IP>:8080 (+비번)
[노트북 / 회사 / 폰 어디서나]  브라우저 접속
```
- 집 PC 안 켜도 됨 · 자동정지 없음 · 구독료 0원 · 데이터는 내 서버 소유.

## 되돌리기 / 이중 사용
- 데스크탑 로컬 실행도 계속 가능(그때는 secrets 의 DATABASE_URL 을 서버가 아닌 원하는 DB로).
- 문제 시 Supabase 로 복귀도 가능(원본 안 건드림).

---

## 자주 막히는 곳 (미리 안내)
- **ARM 인스턴스 "Out of capacity"**: 흔합니다. OCPU 낮추거나 시간 두고 재시도, 또는 AMD Micro(1GB)로 시작.
- **SSH 접속 안 됨**: 키 경로/IP 확인, `icacls` 로 키 권한 조정(위 PHASE 4).
- **8080 접속 안 됨**: Oracle Security List(PHASE 3) **와** 서버 iptables(PHASE 7) **둘 다** 열어야 함.
- **pgvector 설치 실패**: 앱이 현재 안 쓰므로, 알려주시면 스키마에서 제외하고 진행.

**진행하실 때 각 PHASE 결과(특히 IP, 에러 메시지)를 저에게 알려주시면 다음 명령을 사용자님 값에 맞춰 정확히 드리겠습니다.**
