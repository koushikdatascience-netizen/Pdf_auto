param(
  [Parameter(Mandatory = $true)]
  [string]$Server,
  [Parameter(Mandatory = $true)]
  [string]$Database,
  [string]$Driver = "ODBC Driver 17 for SQL Server",
  [string]$Output = "new_database_audit.json"
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "python"
}

$previousConnection = $env:SQLSERVER_CONNECTION_STRING
try {
  $env:SQLSERVER_CONNECTION_STRING = "DRIVER={$Driver};SERVER=$Server;DATABASE=$Database;Trusted_Connection=yes;TrustServerCertificate=yes"
  Write-Host "Running read-only audit against $Server / $Database..." -ForegroundColor Cyan
  & $python -B (Join-Path $PSScriptRoot "schema_check.py") --output $Output
  if ($LASTEXITCODE -ne 0) {
    throw "Database audit failed."
  }
  $report = Get-Content -LiteralPath $Output -Raw | ConvertFrom-Json
  Write-Host "Database: $($report.database.name)" -ForegroundColor Cyan
  Write-Host "Compatible: $($report.compatible)"
  if ($report.blocking_issues.Count -gt 0) {
    Write-Host "Blocking issues:" -ForegroundColor Red
    $report.blocking_issues | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
  }
  if ($report.manual_review.Count -gt 0) {
    Write-Host "Manual review required:" -ForegroundColor Yellow
    $report.manual_review | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
  }
  Write-Host "Full read-only report: $Output" -ForegroundColor Green
}
finally {
  $env:SQLSERVER_CONNECTION_STRING = $previousConnection
}
