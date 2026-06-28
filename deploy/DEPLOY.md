# 배포 가이드 (간단) — Reflex Cloud + 공유 비밀번호

내 PC를 켜둘 필요도, Caddy·Cloudflare·터미널 3개도 **전혀 필요 없음.**
Reflex Cloud가 호스팅·HTTPS·고정 URL을 다 해주고, 우리가 만든 **비밀번호 게이트**로 접근을 막는다.

```
[유저] ──https──▶ [Reflex Cloud (고정 URL)] ──▶ 앱
                                                 ▲ 공유 비밀번호 입력해야 입장
```

---

## 준비 (1회)
- `APP_PASSWORD`(공유 비밀번호)가 `.streamlit/secrets.toml` 에 채워져 있어야 함.
  - 보안 팁: **DB 비밀번호와는 다른 값**으로 두세요(이 값은 협업자에게 공유되니까).

## 딱 3단계

### 1) Reflex 계정 로그인 (무료, 1회)
```powershell
cd D:\Projects\Project_PDB\protein-construct-builder
python -X utf8 -m reflex login
```
→ 브라우저가 열리면 가입/로그인. (무료)

### 2) 비밀값을 배포용 파일로 변환 (자동)
```powershell
python deploy/make_env.py
```
→ `secrets.toml` 의 APP_PASSWORD·Supabase·Anthropic 키를 `prod.env` 로 모아줌. (깃에 안 올라감)

### 3) 배포
```powershell
python -X utf8 -m reflex deploy --app-name pdb-builder --envfile prod.env
```
→ 빌드 후 **고정 URL** 을 출력합니다 (예: `https://pdb-builder-....reflex.run`).
   처음엔 지역(region) 등을 한 번 물어볼 수 있어요 — 적당히 선택하면 됩니다.

> 코드를 고친 뒤 다시 배포하려면 같은 3) 명령만 다시 실행하면 됩니다 (URL 그대로 유지).

## 공유
출력된 **고정 URL + 공유 비밀번호(APP_PASSWORD)** 를 협업자에게 전달.
→ 둘 다 아는 사람만 입장. 내 PC가 꺼져 있어도 24시간 접속됩니다.

---

## 참고 / 한계
- **무료 한도**: Reflex Cloud 무료 티어로 시작 가능. 사용량이 많아지면 유료로 올려야 할 수 있음(대시보드에서 확인).
- **메모리**: 지식베이스 의미검색은 임베딩 모델(약 120MB)을 메모리에 올립니다. 무료 인스턴스가 작아 느리거나 버벅이면, 그 기능만 영향(나머지는 정상). 필요 시 인스턴스 크기를 올리세요.
- **비밀번호 1개** 방식이라 유출되면 누구나 들어옵니다. 더 강한 통제가 필요하면 이메일 허용목록(Cloudflare Access)·Supabase Auth 로 확장 가능.

## (고급) 내 PC에서 직접 호스팅하고 싶다면
Reflex Cloud 대신 자체 호스팅을 원하면 Caddy + Cloudflare Tunnel 방식도 가능합니다(더 복잡). 필요하면 별도 안내드립니다.
