# Starts the HermesWoW companion with Hermes's own Python environment.
#   .\start.ps1            run the companion (restarts it if it exits; Ctrl+C to stop)
#   .\start.ps1 -Install   install/refresh the addon, slots and signal files, then exit
# Set HERMES_PYTHON to override the interpreter, HERMES_AGENT_DIR to override the Hermes checkout.
param([switch]$Install, [string]$AddOns = "")

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$hermesRoot = Join-Path $env:LOCALAPPDATA "hermes"
$agentDir = if ($env:HERMES_AGENT_DIR) { $env:HERMES_AGENT_DIR } else { Join-Path $hermesRoot "hermes-agent" }

function Find-HermesPython {
  if ($env:HERMES_PYTHON) { return $env:HERMES_PYTHON }
  $candidates = @()
  foreach ($facts in Get-ChildItem (Join-Path $hermesRoot "installs\*\facts.json") -ErrorAction SilentlyContinue) {
    try {
      $env_dir = (Get-Content $facts.FullName -Raw | ConvertFrom-Json).packages.venv.environment
      if ($env_dir) { $candidates += (Join-Path $env_dir "Scripts\python.exe") }
    } catch {}
  }
  foreach ($py in $candidates) {
    if (-not (Test-Path $py)) { continue }
    & $py -c "import sys; sys.path.insert(0, r'$agentDir'); import hermes_cli.gateway_client, websockets" 2>$null
    if ($LASTEXITCODE -eq 0) { return $py }
  }
  throw "Could not find Hermes's Python environment under $hermesRoot\installs. Set HERMES_PYTHON to its python.exe."
}

$py = Find-HermesPython
$extra = @()
if ($AddOns) { $extra += @("--addons", $AddOns) }

if ($Install) {
  & $py (Join-Path $here "install.py") @extra
  exit $LASTEXITCODE
}

Write-Host "HermesWoW companion (Python: $py). Ctrl+C to stop."
while ($true) {
  & $py (Join-Path $here "hermeswow.py") --hermes-agent $agentDir @extra
  if ($LASTEXITCODE -eq 2) { exit 2 }   # configuration problem: restarting will not help
  Write-Host "Companion exited ($LASTEXITCODE); restarting in 3 s..."
  Start-Sleep -Seconds 3
}
