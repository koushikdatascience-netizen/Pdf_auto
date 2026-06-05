$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
  throw "Python environment not found. Run .\install.ps1 first."
}

& $python -m pip install "pyinstaller>=6,<7"
& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name ERP-Purchase-Agent `
  --hidden-import integration_api.main `
  --collect-all pymupdf `
  run_agent.py

Write-Host "Release executable: dist\ERP-Purchase-Agent.exe" -ForegroundColor Green
