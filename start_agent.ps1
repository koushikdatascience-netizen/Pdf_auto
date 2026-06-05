$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$config = Join-Path $root "api_config.json"

if (-not (Test-Path -LiteralPath $python)) {
  throw "Python environment not found. Run .\install.ps1 first."
}

if (-not (Test-Path -LiteralPath $config)) {
  throw "api_config.json not found. Run .\setup_agent.ps1 first."
}

Set-Location -LiteralPath $root
& $python run_agent.py
