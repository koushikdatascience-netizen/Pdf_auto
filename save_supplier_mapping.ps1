param(
  [Parameter(Mandatory = $true)]
  [string]$SourceName,
  [Parameter(Mandatory = $true)]
  [string]$TargetName,
  [string]$CompanyCode = "2",
  [string]$BaseUrl = "http://127.0.0.1:47831"
)

$ErrorActionPreference = "Stop"
$config = Get-Content -LiteralPath "api_config.json" -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}
$body = @{
  companycode = $CompanyCode
  source_name = $SourceName
  target_name = $TargetName
} | ConvertTo-Json

Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/v1/mappings/suppliers" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body |
  ConvertTo-Json -Depth 10
