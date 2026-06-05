param(
  [string]$Server = ".\SQLEXPRESS",
  [string]$Database = "barmanager",
  [int]$Port = 47831,
  [string]$Driver = "ODBC Driver 11 for SQL Server",
  [string]$TransactionType = "Purchase_Add",
  [string]$UserCode = "A00001",
  [string]$Sync = "N",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "api_config.json"

if ((Test-Path -LiteralPath $configPath) -and -not $Force) {
  throw "api_config.json already exists. Use -Force only when you intentionally want to replace it."
}

if ($Port -lt 1024 -or $Port -gt 65535) {
  throw "Port must be between 1024 and 65535."
}

if ($UserCode.Length -gt 6) {
  throw "UserCode must be 6 characters or fewer for this ERP schema."
}

if ($Sync.Length -ne 1) {
  throw "Sync must be exactly one character."
}

function New-Secret {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes)
}

$config = @{
  host = "127.0.0.1"
  port = $Port
  enable_docs = $false
  api_key = New-Secret
  approval_secret = New-Secret
  max_pdf_bytes = 15728640
  approval_ttl_seconds = 900
  data_dir = "agent_data"
  connection_string = "DRIVER={$Driver};SERVER=$Server;DATABASE=$Database;Trusted_Connection=yes"
  supplier_lookup_sql = "SELECT ledgerCode FROM dbo.MasterAccountsLedger WHERE ledgerName=? AND companyCode=?"
  item_lookup_sql = "SELECT itemcode FROM dbo.itemmst WHERE itemname=? AND (? IS NULL OR ml=?) AND (? IS NULL OR packing=?) AND (? IS NULL OR strengthname=?)"
  item_code_verify_sql = "SELECT itemcode FROM dbo.itemmst WHERE itemcode=?"
  transaction_type = $TransactionType
  usercode = $UserCode
  sync = $Sync
  mappings = @{
    supplier_aliases = @{}
    item_mappings = @{}
  }
}

$config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
Write-Host "Created $configPath" -ForegroundColor Green
Write-Host "Give the api_key value securely to the ERP EXE team."
Write-Host "Do not commit or email api_config.json."
