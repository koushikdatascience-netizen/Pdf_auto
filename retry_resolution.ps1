param(
  [Parameter(Mandatory = $true)]
  [string]$ResolutionId,
  [string]$BaseUrl = "http://127.0.0.1:47831"
)

$ErrorActionPreference = "Stop"
$config = Get-Content -LiteralPath "api_config.json" -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}

Invoke-RestMethod `
  -Method POST `
  -Uri "$BaseUrl/api/v1/resolutions/$ResolutionId/retry" `
  -Headers $headers | ConvertTo-Json -Depth 30
