param(
  [string]$PurchaseAccountCode = "P00002",
  [string]$ShopCode = "S00001",
  [string]$CheckedBy = "A00001",
  [string]$PurchaseTaxAccount = "E00001",
  [string]$RoundingAccount = "IEX001",
  [string]$SupplierNameOverride = "FL WAREHOUSE JABALPUR"
)

$ErrorActionPreference = "Stop"
$path = Join-Path $PSScriptRoot "api_config.json"
$config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$config | Add-Member -Force NoteProperty erp_profile "mandai"
$config | Add-Member -Force NoteProperty data_dir "agent_data_mandai"
$config | Add-Member -Force NoteProperty erp_options ([pscustomobject]@{
  ptype = "PURCHASE"
  purchaseacccode = $PurchaseAccountCode
  shopcode = $ShopCode
  checked_by = $CheckedBy
  bill_type = "AI"
  purchase_tax_account = $PurchaseTaxAccount
  rounding_account = $RoundingAccount
  supplier_name_override = $SupplierNameOverride
  tppassno_default = ""
})
$config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path -Encoding UTF8
Write-Host "Mandai ERP profile enabled with isolated runtime data. Restart the agent, then run preview only." -ForegroundColor Green
