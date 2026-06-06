param(
  [string]$OutputDirectory = "github_source"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$output = [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$rootPrefix = $root.TrimEnd('\') + '\'

if (-not $output.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Output directory must be inside the project folder."
}

if (Test-Path -LiteralPath $output) {
  throw "Output directory already exists: $output"
}

$files = @(
  ".gitignore",
  "README.md",
  "DEPLOYMENT.md",
  "SECURITY.md",
  "PUBLISH_CHECKLIST.md",
  "CHANGELOG.md",
  "EXE_INTEGRATION_GUIDE.md",
  "requirements-api.txt",
  "api_config.example.json",
  "install.ps1",
  "setup_agent.ps1",
  "start_agent.ps1",
  "health_check.ps1",
  "verify_install.ps1",
  "build_release.ps1",
  "build_agent.ps1",
  "export_github_source.ps1",
  "test_api_flow.ps1",
  "test_pdf_flow.ps1",
  "save_supplier_mapping.ps1",
  "save_item_mapping.ps1",
  "retry_resolution.ps1",
  "run_agent.py",
  "schema_check.py",
  "db.py",
  "validation.py",
  "mapping_service.py",
  "purchase_service.py",
  "pdf_purchase_adapter.py",
  "extract_pdf_json.py",
  "challan_adapter.py",
  "purchase_db.py",
  "example_usage.py",
  "ExeClientExample.cs",
  "purchase_invoice.example.json",
  "purchase_mapping.example.json",
  "erp_mapping.example.json",
  "invoice_example.json",
  "test_challan_adapter.py",
  "test_erp_purchase_service.py",
  "test_integration_api.py",
  "test_purchase_db.py",
  "test_pdf_purchase_adapter.py",
  "test_mapping_store.py"
)

$directories = @(
  "integration_api",
  ".github"
)

New-Item -ItemType Directory -Path $output | Out-Null

foreach ($file in $files) {
  $source = Join-Path $root $file
  if (-not (Test-Path -LiteralPath $source)) {
    throw "Required public file is missing: $file"
  }
  Copy-Item -LiteralPath $source -Destination $output
}

foreach ($directory in $directories) {
  $source = Join-Path $root $directory
  if (-not (Test-Path -LiteralPath $source)) {
    throw "Required public directory is missing: $directory"
  }
  $destinationRoot = Join-Path $output $directory
  New-Item -ItemType Directory -Path $destinationRoot | Out-Null
  Get-ChildItem -LiteralPath $source -Recurse -File |
    Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
    ForEach-Object {
      $relative = $_.FullName.Substring($source.Length).TrimStart('\', '/')
      $destination = Join-Path $destinationRoot $relative
      $destinationDirectory = Split-Path -Parent $destination
      New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}

$blocked = @(
  "api_config.json",
  "schema_report.json",
  "extracted-result.json"
)
foreach ($name in $blocked) {
  if (Get-ChildItem -LiteralPath $output -Recurse -File -Filter $name) {
    throw "Sensitive/runtime file entered export: $name"
  }
}

if (Get-ChildItem -LiteralPath $output -Recurse -File -Filter "*.pdf") {
  throw "PDF files entered the public export."
}

if (Test-Path -LiteralPath (Join-Path $output ".git")) {
  throw "A .git directory entered the public export."
}

Write-Host "Clean GitHub source created: $output" -ForegroundColor Green
Write-Host "Review it, choose a repository license, then initialize and push Git from that folder."
