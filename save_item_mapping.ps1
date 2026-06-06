param(
  [Parameter(Mandatory = $true)]
  [string]$SourceName,
  [string]$Batch = "",
  [Nullable[double]]$Ml,
  [Parameter(Mandatory = $true)]
  [string]$ItemCode,
  [string]$BaseUrl = "http://127.0.0.1:47831"
)

$ErrorActionPreference = "Stop"
$config = Get-Content -LiteralPath "api_config.json" -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}
$body = @{
  source_name = $SourceName
  batch = $Batch
  item_code = $ItemCode
}
if ($null -ne $Ml) {
  $body.ml = $Ml
}
$body = $body | ConvertTo-Json

Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/v1/mappings/items" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body |
  ConvertTo-Json -Depth 10
