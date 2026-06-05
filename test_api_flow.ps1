param(
  [string]$InvoiceFile = "purchase_invoice.example.json",
  [string]$CompanyCode = "2",
  [string]$YearCode = "8",
  [string]$BaseUrl = "http://127.0.0.1:47831",
  [switch]$Insert
)

$ErrorActionPreference = "Stop"

function Invoke-AgentRequest {
  param(
    [string]$Method,
    [string]$Uri,
    [hashtable]$Headers,
    [string]$Body
  )

  try {
    if ($Body) {
      return Invoke-RestMethod `
        -Method $Method `
        -Uri $Uri `
        -Headers $Headers `
        -ContentType "application/json" `
        -Body $Body
    }
    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $Headers
  }
  catch {
    $response = $_.Exception.Response
    if ($response) {
      $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
      $responseBody = $reader.ReadToEnd()
      $reader.Close()
      Write-Host "Agent error response:" -ForegroundColor Red
      Write-Host $responseBody -ForegroundColor Red
    }
    throw
  }
}

$config = Get-Content -LiteralPath "api_config.json" -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}

Write-Host "Checking agent health..."
$health = Invoke-AgentRequest -Method GET -Uri "$BaseUrl/api/v1/health" -Headers $headers
$health | ConvertTo-Json -Depth 10

Write-Host "Checking ERP schema..."
$schema = Invoke-AgentRequest -Method GET -Uri "$BaseUrl/api/v1/schema-check" -Headers $headers
$schema | ConvertTo-Json -Depth 10
if (-not $schema.compatible) {
  throw "ERP schema is not compatible. Purchase preview stopped."
}

$invoice = Get-Content -LiteralPath $InvoiceFile -Raw | ConvertFrom-Json
$previewBody = @{
  companycode = $CompanyCode
  yearcode = $YearCode
  strict_total = $true
  invoice = $invoice
} | ConvertTo-Json -Depth 30

Write-Host "Requesting purchase preview..."
$preview = Invoke-AgentRequest `
  -Method POST `
  -Uri "$BaseUrl/api/v1/purchases/preview" `
  -Headers $headers `
  -Body $previewBody

$preview | ConvertTo-Json -Depth 30

if (-not $Insert) {
  Write-Host "Preview completed. No database rows were inserted." -ForegroundColor Green
  Write-Host "Review the preview, then rerun with -Insert to perform the real insert."
  exit 0
}

$confirmation = Read-Host "Type INSERT to confirm writing this purchase to SQL Server"
if ($confirmation -cne "INSERT") {
  Write-Host "Insert cancelled. No database rows were inserted."
  exit 0
}

$insertBody = @{approval_token = $preview.approval_token} | ConvertTo-Json
Write-Host "Inserting approved purchase..."
$result = Invoke-AgentRequest `
  -Method POST `
  -Uri "$BaseUrl/api/v1/purchases/insert" `
  -Headers $headers `
  -Body $insertBody

$result | ConvertTo-Json -Depth 20
