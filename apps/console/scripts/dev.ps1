# Starts the whole Northstar stack locally: P1 (8000), P2 (8001), P3 (8002); the console is `npm run dev` (5173).
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1                 # start (P2 answers with labelled templates, no model)
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Ollama         # P2 uses a local Ollama model (free, weak, labelled "ollama:<model>")
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Fresh          # start with empty state (ledger, approvals, audit, traces)
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Stop           # stop what this script started
#
# Services live in the monorepo at ..\..\services\{performance,assistant,gateway}; the gateway is at $env:NORTHSTAR_P3_DIR or
# (override any with -P1Dir / -P2Dir / -P3Dir). Everything runs on this machine with local, throw-away
# secrets: nothing here is a real credential. P1 is pointed at a closed database port on purpose, so it serves its committed snapshot
# (the "SNAPSHOT" pill): set P1_REAL_DB=1 to use your own .env database settings instead. If ANTHROPIC_API_KEY is already in your
# environment P2 uses it; otherwise -Ollama, otherwise templates.
#
# State of a run (P2's ledger, traces and spend; the gateway's approvals, audit logs and governance store) lives in .dev-state\ and logs in
# .dev-logs\, both ignored by git, so a run never touches the other repositories' own data. -Fresh empties .dev-state first.
param(
  [string]$P1Dir = (Join-Path $PSScriptRoot '..\..\..\services\performance'),
  [string]$P2Dir = (Join-Path $PSScriptRoot '..\..\..\services\assistant'),
  [string]$P3Dir = $(if ($env:NORTHSTAR_P3_DIR) { $env:NORTHSTAR_P3_DIR } else { Join-Path $PSScriptRoot '..\..\..\services\gateway' }),
  [switch]$Ollama,
  [switch]$Fresh,
  [switch]$Stop
)
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $root '.dev-pids.json'
$stateDir = Join-Path $root '.dev-state'
$logDir = Join-Path $root '.dev-logs'

if ($Stop) {
  if (Test-Path $pidFile) {
    foreach ($id in (Get-Content $pidFile | ConvertFrom-Json)) {
      # A PID file outlives its processes and Windows reuses PIDs: only stop a process that is still one of ours (a uvicorn).
      $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue).CommandLine
      if ($cmd -and $cmd -match 'uvicorn') { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; Write-Host "stopped $id" }
      else { Write-Host "skipped $id (not a uvicorn process any more)" }
    }
    Remove-Item $pidFile
  }
  Write-Host 'Stopped.'; return
}

$env:PYTHONUTF8 = '1'
if ($Fresh -and (Test-Path $stateDir)) { Remove-Item -Recurse -Force $stateDir }
New-Item -ItemType Directory -Force -Path $stateDir, $logDir | Out-Null
$serviceKey = 'dev-service-key'          # P3 -> P2 service key (local only)
$approver = 'dev-approver-token'         # P3 approval token (local only)
$provider = if ($env:ANTHROPIC_API_KEY) { 'anthropic' } elseif ($Ollama) { 'ollama' } else { 'none' }
if ($provider -eq 'ollama') {
  try { Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 | Out-Null } catch { Write-Warning 'Ollama is not answering on 127.0.0.1:11434: P2 will fall back to template answers until it is started.' }
}
$procs = @()

function Start-Svc($name, $dir, $envVars, $uvicornArgs) {
  Write-Host "Starting $name ..."
  $saved = @{}
  foreach ($k in $envVars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $envVars[$k]) }
  $slug = ($name -split ' ')[0].ToLower()
  try {
    $p = Start-Process -FilePath python -ArgumentList $uvicornArgs -WorkingDirectory (Resolve-Path $dir).Path -WindowStyle Hidden -PassThru `
         -RedirectStandardOutput (Join-Path $logDir "$slug.out.log") -RedirectStandardError (Join-Path $logDir "$slug.err.log")
    $script:procs += $p.Id
  } finally {
    foreach ($k in $envVars.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
}

$p1env = @{ API_ALLOWED_ORIGINS = 'http://localhost:5173' }
if (-not $env:P1_REAL_DB) { $p1env += @{ DB_HOST = '127.0.0.1'; DB_PORT = '1'; DB_NAME = 'x'; DB_USER = 'x'; DB_PASSWORD = 'x' } }
$p2env = @{ API_KEYS = "gateway:${serviceKey}:admin"; OPS_PERFORMANCE_API_URL = 'http://127.0.0.1:8000'; GATEWAY_URL = 'http://127.0.0.1:8002'
            GATEWAY_APPROVER_TOKEN = $approver; P2_LLM_PROVIDER = $provider
            P2_LEDGER_DB = (Join-Path $stateDir 'ledger.db'); P2_TRACE_DB = (Join-Path $stateDir 'traces.db')
            P2_INVESTIGATIONS_DB = (Join-Path $stateDir 'investigations.db'); P2_SPEND_DB = (Join-Path $stateDir 'spend.db') }
$p3env = @{ GATEWAY_DEMO_MODE = '1'; P2_URL = 'http://127.0.0.1:8001'; P2_API_KEY = $serviceKey; GATEWAY_APPROVER_TOKEN = $approver
            GATEWAY_IP_RATE_LIMIT = '0'; GATEWAY_CORS_ORIGINS = 'http://localhost:5173'; GATEWAY_LOG_STDOUT = '0'
            GATEWAY_LOG_PATH = (Join-Path $stateDir 'gateway.jsonl'); GATEWAY_ACTIONS_AUDIT = (Join-Path $stateDir 'actions.jsonl')
            GATEWAY_APPROVALS_DB = (Join-Path $stateDir 'approvals.db'); GATEWAY_GOVERNANCE_DB = (Join-Path $stateDir 'governance.db') }
Start-Svc 'P1 analytics (8000)' $P1Dir $p1env '-m uvicorn src.api.main:app --host 127.0.0.1 --port 8000'
Start-Svc 'P2 assistant (8001)' $P2Dir $p2env '-m uvicorn src.api.main:app --host 127.0.0.1 --port 8001'
Start-Svc 'P3 gateway (8002)' $P3Dir $p3env '-m uvicorn gateway.app:app --host 127.0.0.1 --port 8002'
ConvertTo-Json -InputObject @($procs) | Set-Content $pidFile
Write-Host "Waiting for health (assistant model: $provider) ..."
foreach ($u in 'http://127.0.0.1:8000/health', 'http://127.0.0.1:8001/health', 'http://127.0.0.1:8002/health') {
  $up = $false
  for ($i = 0; $i -lt 90 -and -not $up; $i++) { try { Invoke-RestMethod $u -TimeoutSec 5 | Out-Null; $up = $true; Write-Host "  up: $u" } catch { Start-Sleep 2 } }
  if (-not $up) { Write-Warning "not healthy after 3 minutes: $u (see $logDir)" }
}
Write-Host 'Console: npm run dev  ->  http://localhost:5173'
