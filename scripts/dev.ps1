# Starts the whole Northstar stack locally: P1 (8000), P2 (8001), P3 (8002) and the console (5173).
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            # start everything
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Stop      # stop what this script started
#
# Sibling repos are expected at ..\operations-performance, ..\operations-assistant and ..\..\0_Project3\llm-security-gateway
# (override with -P1Dir / -P2Dir / -P3Dir). Everything runs on this machine with local, throw-away secrets: nothing here is a real
# credential. P1 is pointed at a closed database port on purpose, so it serves its committed snapshot (the "SNAPSHOT" pill):
# set P1_REAL_DB=1 to use your own .env database settings instead. P2 runs without a language model (template answers) unless
# ANTHROPIC_API_KEY is already set in your environment.
param(
  [string]$P1Dir = (Join-Path $PSScriptRoot '..\..\operations-performance'),
  [string]$P2Dir = (Join-Path $PSScriptRoot '..\..\operations-assistant'),
  [string]$P3Dir = 'D:\0_Project3\llm-security-gateway',
  [switch]$Stop
)
$pidFile = Join-Path $PSScriptRoot '..\.dev-pids.json'
if ($Stop) {
  if (Test-Path $pidFile) { (Get-Content $pidFile | ConvertFrom-Json) | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Remove-Item $pidFile }
  Write-Host 'Stopped.'; return
}
$env:PYTHONUTF8 = '1'
$serviceKey = 'dev-service-key'          # P3 -> P2 service key (local only)
$approver = 'dev-approver-token'         # P3 approval token (local only)
$procs = @()

function Start-Svc($name, $dir, $env, $args) {
  Write-Host "Starting $name ..."
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = 'python'; $psi.Arguments = $args; $psi.WorkingDirectory = (Resolve-Path $dir).Path
  $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
  foreach ($k in $env.Keys) { $psi.EnvironmentVariables[$k] = $env[$k] }
  $p = [System.Diagnostics.Process]::Start($psi); $script:procs += $p.Id
}

$p1env = @{ API_ALLOWED_ORIGINS = 'http://localhost:5173' }
if (-not $env:P1_REAL_DB) { $p1env += @{ DB_HOST = '127.0.0.1'; DB_PORT = '1'; DB_NAME = 'x'; DB_USER = 'x'; DB_PASSWORD = 'x' } }
Start-Svc 'P1 analytics (8000)' $P1Dir $p1env '-m uvicorn src.api.main:app --host 127.0.0.1 --port 8000'
Start-Svc 'P2 assistant (8001)' $P2Dir @{ API_KEYS = "gateway:${serviceKey}:admin"; OPS_PERFORMANCE_API_URL = 'http://127.0.0.1:8000'; GATEWAY_URL = 'http://127.0.0.1:8002'; GATEWAY_APPROVER_TOKEN = $approver; P2_LLM_PROVIDER = $(if ($env:ANTHROPIC_API_KEY) { 'anthropic' } else { 'none' }) } '-m uvicorn src.api.main:app --host 127.0.0.1 --port 8001'
Start-Svc 'P3 gateway (8002)' $P3Dir @{ GATEWAY_DEMO_MODE = '1'; P2_URL = 'http://127.0.0.1:8001'; P2_API_KEY = $serviceKey; GATEWAY_APPROVER_TOKEN = $approver; GATEWAY_IP_RATE_LIMIT = '0'; GATEWAY_CORS_ORIGINS = 'http://localhost:5173'; GATEWAY_LOG_STDOUT = '0' } '-m uvicorn gateway.app:app --host 127.0.0.1 --port 8002'
$procs | ConvertTo-Json | Set-Content $pidFile
Write-Host 'Waiting for health ...'
foreach ($u in 'http://127.0.0.1:8000/health', 'http://127.0.0.1:8001/health', 'http://127.0.0.1:8002/health') {
  for ($i = 0; $i -lt 90; $i++) { try { Invoke-RestMethod $u -TimeoutSec 2 | Out-Null; Write-Host "  up: $u"; break } catch { Start-Sleep 2 } }
}
Write-Host 'Console: npm run dev  ->  http://localhost:5173'
