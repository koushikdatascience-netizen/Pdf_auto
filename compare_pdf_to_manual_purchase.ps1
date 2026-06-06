param(
  [Parameter(Mandatory = $true)]
  [string]$PdfFile,
  [Parameter(Mandatory = $true)]
  [int]$ManualTrnno,
  [string]$CompanyCode = "2",
  [string]$YearCode = "8",
  [string]$Output = "purchase_comparison.json"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$resolvedPdf = (Resolve-Path -LiteralPath $PdfFile).Path

Write-Host "Comparing PDF preview with manual ERP purchase (read-only)..." -ForegroundColor Cyan
& $python -B (Join-Path $root "compare_pdf_to_manual_purchase.py") `
  $resolvedPdf `
  --manual-trnno $ManualTrnno `
  --company $CompanyCode `
  --year $YearCode `
  --output (Join-Path $root $Output)
if ($LASTEXITCODE -ne 0) {
  throw "Purchase comparison failed."
}
