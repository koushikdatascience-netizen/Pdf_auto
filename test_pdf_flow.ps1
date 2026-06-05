param(
  [Parameter(Mandatory = $true)]
  [string]$PdfFile,
  [string]$CompanyCode = "2",
  [string]$YearCode = "8",
  [string]$BaseUrl = "http://127.0.0.1:47831",
  [switch]$Insert
)

$ErrorActionPreference = "Stop"
$config = Get-Content -LiteralPath "api_config.json" -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}

Write-Host "Uploading PDF, extracting products, and validating ERP masters..."
$resolvedPdf = (Resolve-Path -LiteralPath $PdfFile).Path
$responseText = & curl.exe --silent --show-error --fail-with-body `
  -X POST `
  -H "X-API-Key: $($config.api_key)" `
  -F "companycode=$CompanyCode" `
  -F "yearcode=$YearCode" `
  -F "strict_total=true" `
  -F "pdf=@$resolvedPdf;type=application/pdf" `
  "$BaseUrl/api/v1/purchases/from-pdf/preview"
if ($LASTEXITCODE -ne 0) {
  throw "PDF preview request failed."
}
$preview = $responseText | ConvertFrom-Json

$preview | ConvertTo-Json -Depth 30

if (-not $Insert) {
  Write-Host "PDF preview completed. No database rows were inserted." -ForegroundColor Green
  Write-Host "Review every purchase, then rerun with -Insert."
  exit 0
}

$confirmation = Read-Host "Type INSERT to write all displayed purchases to SQL Server"
if ($confirmation -cne "INSERT") {
  Write-Host "Insert cancelled. No database rows were inserted."
  exit 0
}

$results = @()
foreach ($purchase in $preview.purchases) {
  $body = @{approval_token = $purchase.approval_token} | ConvertTo-Json
  $results += Invoke-RestMethod `
    -Method POST `
    -Uri "$BaseUrl/api/v1/purchases/insert" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
}

$results | ConvertTo-Json -Depth 20
