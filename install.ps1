param(
  [string]$Server = ".\SQLEXPRESS",
  [string]$Database = "barmanager",
  [int]$Port = 47831,
  [string]$Driver = "ODBC Driver 11 for SQL Server",
  [string]$UserCode = "A00001",
  [switch]$ForceConfig
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "Checking Python..." -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python 3.9 or newer is required and must be available as 'python'."
}

$version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$version -lt [version]"3.9") {
  throw "Python 3.9 or newer is required. Found $version."
}

Write-Host "Creating isolated Python environment..." -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath $venvPython)) {
  & python -m venv (Join-Path $root ".venv")
}

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root "requirements-api.txt")

Write-Host "Creating private agent configuration..." -ForegroundColor Cyan
$setupArgs = @{
  Server = $Server
  Database = $Database
  Port = $Port
  Driver = $Driver
  UserCode = $UserCode
}
if ($ForceConfig) {
  $setupArgs.Force = $true
}
& (Join-Path $root "setup_agent.ps1") @setupArgs

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Next: .\verify_install.ps1"
Write-Host "Then: .\start_agent.ps1"
