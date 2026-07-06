<#
  PDB database — 내 PC 자체 호스팅 + Cloudflare Tunnel (무료, 항상-켜짐 방식).
  프론트(3000)+백엔드(8000)를 Caddy로 8080에 합치고, cloudflared 터널로 공개한다.
  ★핵심: API_URL 을 공개 터널 주소로 잡아야 프론트가 그 주소로 백엔드(websocket)에 붙는다.

  사용법 (PowerShell, 프로젝트 루트에서):
    # (A) Quick tunnel — 도메인 불필요, 임시 URL (가장 쉬움):
    powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1

    # (B) 고정 도메인(내 Cloudflare 도메인, named tunnel 이 config.yml 에 구성됨):
    powershell -ExecutionPolicy Bypass -File deploy\start_selfhost.ps1 -PublicUrl "https://pdb.mydomain.com"

  사전 설치: caddy, cloudflared  (deploy/SELFHOST.md 참고)
#>
param(
  [string]$PublicUrl = "",
  [int]$FrontendPort = 3000,
  [int]$BackendPort  = 8000,
  [int]$CaddyPort    = 8080
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = "C:\Users\jk941\miniconda3\python.exe"
if(-not (Test-Path $py)){ $py = "python" }

$logdir = Join-Path $root "deploy\_logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null

function Need($cmd){
  if(-not (Get-Command $cmd -ErrorAction SilentlyContinue)){
    Write-Host "[!] '$cmd' 가 설치되어 있지 않습니다 — deploy\SELFHOST.md 의 설치 안내를 보세요." -ForegroundColor Yellow
    exit 1
  }
}
Need caddy
Need cloudflared

$procs = @()
$quick = $false

# 1) 공개 URL 결정 --------------------------------------------------------------
if(-not $PublicUrl){
  $quick = $true
  Write-Host "[*] Quick tunnel 시작 (도메인 불필요 · 임시 URL)..." -ForegroundColor Cyan
  $cfErr = Join-Path $logdir "cloudflared.err"
  Remove-Item $cfErr -ErrorAction SilentlyContinue
  $cf = Start-Process cloudflared `
      -ArgumentList @("tunnel","--url","http://localhost:$CaddyPort") `
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

# 2) API_URL — 프론트가 이 주소로 백엔드 websocket 에 붙도록 빌드에 주입 -----------
$env:API_URL = $PublicUrl

# 3) Reflex 프로덕션 실행 (프론트 $FrontendPort + 백엔드 $BackendPort) ------------
Write-Host "[*] Reflex(prod) 빌드·실행 중... (첫 빌드는 수 분 걸릴 수 있음)" -ForegroundColor Cyan
$rx = Start-Process $py `
    -ArgumentList @("-X","utf8","-m","reflex","run","--env","prod","--frontend-port","$FrontendPort","--backend-port","$BackendPort") `
    -RedirectStandardOutput (Join-Path $logdir "reflex.out") `
    -RedirectStandardError  (Join-Path $logdir "reflex.err") `
    -PassThru -WindowStyle Hidden
$procs += $rx

# 4) 포트 기동 대기 -------------------------------------------------------------
function WaitPort($p,$timeout){
  $d=(Get-Date).AddSeconds($timeout)
  while((Get-Date) -lt $d){
    try { if((Test-NetConnection -ComputerName "localhost" -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue)){ return $true } } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}
Write-Host "[*] 백엔드($BackendPort) 기동 대기..." -ForegroundColor Cyan
if(-not (WaitPort $BackendPort 360)){ Write-Host "[!] 백엔드 미기동 — deploy\_logs\reflex.err 확인" -ForegroundColor Red }
Write-Host "[*] 프론트($FrontendPort) 기동 대기..." -ForegroundColor Cyan
if(-not (WaitPort $FrontendPort 180)){ Write-Host "[!] 프론트 미기동 — deploy\_logs\reflex.err 확인" -ForegroundColor Red }

# 5) Caddy: 프론트+백엔드 → :$CaddyPort 로 병합 ---------------------------------
Write-Host "[*] Caddy 리버스 프록시 시작 (:$CaddyPort)" -ForegroundColor Cyan
$cad = Start-Process caddy `
    -ArgumentList @("run","--config","deploy/Caddyfile") `
    -RedirectStandardOutput (Join-Path $logdir "caddy.out") `
    -RedirectStandardError  (Join-Path $logdir "caddy.err") `
    -PassThru -WindowStyle Hidden
$procs += $cad
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "  접속 주소:  $PublicUrl" -ForegroundColor Green
if($quick){ Write-Host "  (Quick tunnel — 이 창을 닫으면 URL 소멸. 재시작하면 URL이 바뀜)" -ForegroundColor Yellow }
Write-Host "  이 창(PowerShell)을 열어두면 서비스가 유지됩니다.  종료: Ctrl+C" -ForegroundColor Green
Write-Host "  로그: deploy\_logs\  (reflex.err / caddy.err / cloudflared.err)" -ForegroundColor DarkGray
Write-Host "==================================================================" -ForegroundColor Green

# 6) 유지 + 종료 시 자식 프로세스 정리 ------------------------------------------
try{
  while($true){
    Start-Sleep -Seconds 5
    foreach($p in $procs){ if($p.HasExited){ Write-Host "[!] 프로세스(PID $($p.Id)) 종료됨 — 해당 로그 확인" -ForegroundColor Red } }
  }
} finally {
  Write-Host "[*] 종료 — 자식 프로세스 정리 중..." -ForegroundColor Cyan
  foreach($p in $procs){ try{ if(-not $p.HasExited){ Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }catch{} }
}
