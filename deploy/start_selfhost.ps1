<#
  PDB database — 내 PC 자체 호스팅 + Cloudflare Tunnel (무료, 항상-켜짐).

  ★Reflex 프로덕션은 프론트+백엔드+websocket 을 "한 포트(Fullstack port)"로 서빙한다.
    → Caddy 불필요. reflex(prod)를 단일 포트로 띄우고 cloudflared 로 그 포트만 공개하면 끝.
  ★API_URL 을 공개 터널 주소로 잡아야 프론트가 그 주소로 백엔드에 붙는다.

  사용법 (PowerShell, 프로젝트 루트에서):
    # (A) Quick tunnel — 도메인 불필요, 임시 URL (가장 쉬움):
    powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1

    # (B) 고정 도메인(내 Cloudflare 도메인, config.yml 로 named tunnel 구성):
    powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1 -PublicUrl "https://pdb.mydomain.com"

  사전 설치: cloudflared  (deploy/SELFHOST.md 참고. Caddy 는 더 이상 필요 없음)
#>
param(
  [string]$PublicUrl = "",
  [int]$Port = 8080          # Fullstack 포트(프론트+백엔드 통합). 터널이 이 포트로 붙는다.
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = "C:\Users\jk941\miniconda3\python.exe"
if(-not (Test-Path $py)){ $py = "python" }

$logdir = Join-Path $root "deploy\_logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null

if(-not (Get-Command cloudflared -ErrorAction SilentlyContinue)){
  Write-Host "[!] 'cloudflared' 미설치 — deploy\SELFHOST.md 의 설치 안내를 보세요." -ForegroundColor Yellow
  exit 1
}

$procs = @()
$quick = $false

# 1) 공개 URL 결정 --------------------------------------------------------------
if(-not $PublicUrl){
  $quick = $true
  Write-Host "[*] Quick tunnel 시작 (도메인 불필요 · 임시 URL)..." -ForegroundColor Cyan
  $cfErr = Join-Path $logdir "cloudflared.err"
  Remove-Item $cfErr -ErrorAction SilentlyContinue
  $cf = Start-Process cloudflared `
      -ArgumentList @("tunnel","--url","http://localhost:$Port") `
      -RedirectStandardError $cfErr `
      -RedirectStandardOutput (Join-Path $logdir "cloudflared.out") `
      -PassThru -WindowStyle Hidden
  $procs += $cf
  $deadline=(Get-Date).AddSeconds(45); $url=$null
  while((Get-Date) -lt $deadline){
    Start-Sleep -Milliseconds 800
    if(Test-Path $cfErr){
      $m = Select-String -Path $cfErr -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
      if($m){ $url = $m.Matches[0].Value; break }
    }
  }
  if(-not $url){ Write-Host "[!] Quick tunnel URL 캡처 실패 — $cfErr 확인" -ForegroundColor Red; exit 1 }
  $PublicUrl = $url
}
Write-Host "[*] 공개 URL: $PublicUrl" -ForegroundColor Green

# 2) API_URL — 프론트가 이 주소로 백엔드에 붙도록 빌드에 주입 --------------------
$env:API_URL = $PublicUrl

# 3) Reflex 프로덕션 실행 (단일 Fullstack 포트) ---------------------------------
Write-Host "[*] Reflex(prod) 빌드·실행 중 (포트 $Port)... 첫 빌드는 수 분 걸릴 수 있음." -ForegroundColor Cyan
$rx = Start-Process $py `
    -ArgumentList @("-X","utf8","-m","reflex","run","--env","prod","--backend-port","$Port") `
    -RedirectStandardOutput (Join-Path $logdir "reflex.out") `
    -RedirectStandardError  (Join-Path $logdir "reflex.err") `
    -PassThru -WindowStyle Hidden
$procs += $rx

# 4) Fullstack 포트 기동 대기 ---------------------------------------------------
function WaitPort($p,$timeout){
  $d=(Get-Date).AddSeconds($timeout)
  while((Get-Date) -lt $d){
    try { if((Test-NetConnection -ComputerName "localhost" -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue)){ return $true } } catch {}
    if($rx.HasExited){ return $false }
    Start-Sleep -Seconds 2
  }
  return $false
}
Write-Host "[*] 앱 기동 대기 (포트 $Port)..." -ForegroundColor Cyan
if(-not (WaitPort $Port 420)){
  Write-Host "[!] 앱이 포트 $Port 에 뜨지 않음 — deploy\_logs\reflex.err 확인" -ForegroundColor Red
  Write-Host "----- reflex.err (마지막 부분) -----" -ForegroundColor DarkGray
  Get-Content (Join-Path $logdir "reflex.err") -Tail 20 -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "  접속 주소:  $PublicUrl" -ForegroundColor Green
if($quick){ Write-Host "  (Quick tunnel — 이 창을 닫으면 URL 소멸. 재시작하면 URL 이 바뀜)" -ForegroundColor Yellow }
Write-Host "  이 PowerShell 창을 열어두면 서비스 유지.  종료: Ctrl+C" -ForegroundColor Green
Write-Host "  로그: deploy\_logs\  (reflex.err / cloudflared.err)" -ForegroundColor DarkGray
Write-Host "==================================================================" -ForegroundColor Green

# 5) 유지 + 종료 시 자식 프로세스 정리 ------------------------------------------
try{
  while($true){
    Start-Sleep -Seconds 5
    if($rx.HasExited){ Write-Host "[!] Reflex 프로세스 종료됨 — deploy\_logs\reflex.err 확인" -ForegroundColor Red; break }
  }
} finally {
  Write-Host "[*] 종료 — 자식 프로세스 정리 중..." -ForegroundColor Cyan
  foreach($p in $procs){ try{ if(-not $p.HasExited){ Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }catch{} }
}
