$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$configPath = Join-Path $root "api_config.json"

if (-not (Test-Path -LiteralPath $configPath)) {
  throw "api_config.json not found. Run .\install.ps1 first."
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$headers = @{"X-API-Key" = $config.api_key}
$url = "http://$($config.host):$($config.port)/api/v1/health"

Invoke-RestMethod -Method GET -Uri $url -Headers $headers |
  ConvertTo-Json -Depth 10
