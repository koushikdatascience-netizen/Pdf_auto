$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$configPath = Join-Path $root "api_config.json"

if (-not (Test-Path -LiteralPath $python)) {
  throw "Python environment not found. Run .\install.ps1 first."
}
if (-not (Test-Path -LiteralPath $configPath)) {
  throw "api_config.json not found."
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$env:SQLSERVER_CONNECTION_STRING = $config.connection_string

Write-Host "Auditing ERP item-creation rules (read-only)..." -ForegroundColor Cyan
& $python -B (Join-Path $root "audit_item_creation.py") --output (Join-Path $root "item_creation_audit.json")
if ($LASTEXITCODE -ne 0) {
  throw "Item creation audit failed."
}
