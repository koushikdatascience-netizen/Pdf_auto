$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$configPath = Join-Path $root "api_config.json"

if (-not (Test-Path -LiteralPath $python)) {
  throw "Python environment not found. Run .\install.ps1 first."
}

if (-not (Test-Path -LiteralPath $configPath)) {
  throw "api_config.json not found. Run .\setup_agent.ps1 first."
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$env:SQLSERVER_CONNECTION_STRING = $config.connection_string

Write-Host "Running automated tests..." -ForegroundColor Cyan
& $python -B -m unittest discover -v
if ($LASTEXITCODE -ne 0) {
  throw "Automated tests failed. Fix the errors above before using the agent."
}

Write-Host "Checking live ERP schema (read-only)..." -ForegroundColor Cyan
& $python -B schema_check.py --profile $config.erp_profile --output schema_report.json
if ($LASTEXITCODE -ne 0) {
  throw "Live ERP schema check could not complete."
}
$report = Get-Content -LiteralPath (Join-Path $root "schema_report.json") -Raw | ConvertFrom-Json

if (-not $report.compatible) {
  $report.blocking_issues | ForEach-Object { Write-Host $_ -ForegroundColor Red }
  throw "ERP schema check failed."
}

Write-Host "Installation and ERP schema are compatible." -ForegroundColor Green
