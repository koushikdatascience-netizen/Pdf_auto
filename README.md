# ERP Purchase Integration Agent

A local FastAPI service that validates normalized purchase-invoice JSON and
inserts approved purchases into a Microsoft SQL Server ERP database.

The agent is designed to run beside an existing Windows ERP installation:

```text
ERP EXE -> http://127.0.0.1:47831 -> local SQL Server
```

## What It Does

- Accepts normalized invoice JSON produced by a PDF/OCR/AI pipeline.
- Validates required fields, totals, supplier, and item-master records.
- Provides a preview before any database write.
- Requires a signed, expiring approval token before insertion.
- Prevents duplicate supplier invoice numbers.
- Supports multiple line items.
- Uses SQL Server transactions, locking, commit, and rollback.
- Returns generated `trnid` and `trnno`.

The production service writes only to:

```text
trnidmst
purchasemain
purchasedetail
PurchaseTaxDetail
```

It never writes to transaction reporting tables, supplier masters, or item
masters.

## Requirements

- Windows 10/11 or Windows Server
- Python 3.9 or newer
- Microsoft SQL Server
- Microsoft ODBC Driver 11 for SQL Server or newer
- An ERP database matching the supported schema

## Quick Start

Clone the repository and open PowerShell inside it:

```powershell
git clone https://github.com/YOUR-ORG/erp-purchase-agent.git
cd erp-purchase-agent
```

Install dependencies and create a private local configuration:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -Server ".\SQLEXPRESS" -Database "barmanager"
```

Verify the installation and ERP schema. This performs read-only schema checks:

```powershell
.\verify_install.ps1
```

Start the agent:

```powershell
.\start_agent.ps1
```

The API will listen on:

```text
http://127.0.0.1:47831
```

Check a running agent:

```powershell
.\health_check.ps1
```

## Complete PDF Flow

The normal user input is a PDF. One API call performs:

```text
PDF upload
-> PyMuPDF extraction
-> structured product/supplier association
-> normalized purchase JSON
-> invoice validation
-> ERP supplier/item-master validation
-> approval-ready purchase preview
```

Preview a PDF without inserting:

```powershell
.\test_pdf_flow.ps1 -PdfFile "invoice.pdf"
```

After reviewing every generated purchase:

```powershell
.\test_pdf_flow.ps1 -PdfFile "invoice.pdf" -Insert
```

The user must type `INSERT` before database writes occur.

## Test With Normalized JSON

In a second PowerShell window, preview the included example invoice:

```powershell
.\test_api_flow.ps1
```

Preview performs no inserts. After reviewing it, explicitly test insertion on a
test database:

```powershell
.\test_api_flow.ps1 -Insert
```

The script requires typing `INSERT` before writing to SQL Server.

## API Workflow

1. Upload the PDF to `POST /api/v1/purchases/from-pdf/preview`.
2. Show the resolved supplier, items, tax, and totals to the user.
3. Require explicit user approval.
4. Call `POST /api/v1/purchases/insert` using the approval token.
5. Save the returned `trnid` and `trnno`.

Every request requires the `X-API-Key` header. See
[EXE_INTEGRATION_GUIDE.md](EXE_INTEGRATION_GUIDE.md) for request/response
examples and ERP EXE integration guidance.

## Configuration

`install.ps1` creates `api_config.json` with random secrets. This file is
ignored by Git and must remain private.

Important settings:

```json
{
  "connection_string": "DRIVER={ODBC Driver 11 for SQL Server};SERVER=.\\SQLEXPRESS;DATABASE=barmanager;Trusted_Connection=yes",
  "transaction_type": "Purchase_Add",
  "usercode": "A00001",
  "sync": "N"
}
```

Use `api_config.example.json` as the public template. Supplier aliases and item
mappings can be configured under `mappings`.

## Missing Master Data

The service does not create suppliers or items. When a supplier or item is
missing, preview returns HTTP `409`. Add the record through the ERP master-data
screen, then preview the invoice again.

## Build A Standalone EXE

After installation:

```powershell
.\build_release.ps1
```

The executable is created at:

```text
dist\ERP-Purchase-Agent.exe
```

Deploy the executable together with a customer-specific `api_config.json`.
Never include a real configuration file in a GitHub release.

## Development

Run all tests:

```powershell
python -B -m unittest discover -v
```

Main modules:

- `integration_api/`: FastAPI application, security, and idempotency state.
- `validation.py`: invoice validation and normalization.
- `mapping_service.py`: supplier/item resolution.
- `db.py`: approved SQL statements and concurrency-safe ID generation.
- `purchase_service.py`: preview and transactional insertion.
- `pdf_purchase_adapter.py`: extracted PDF to normalized purchase conversion.
- `schema_check.py`: read-only ERP compatibility checker.

## Security

- The API binds to localhost only.
- API keys and approval secrets are generated per installation.
- SQL lookup statements come only from local configuration.
- Approval tokens are signed, expiring, and payload-bound.
- Customer documents, logs, configuration, and database reports are ignored by
  Git.

Review [SECURITY.md](SECURITY.md) and [DEPLOYMENT.md](DEPLOYMENT.md) before a
production rollout.

## Publish To GitHub

Create a clean, allowlisted source folder that excludes all customer/runtime
data:

```powershell
.\export_github_source.ps1
cd github_source
```

Then follow [PUBLISH_CHECKLIST.md](PUBLISH_CHECKLIST.md). Choose the repository
license before making the project public.
