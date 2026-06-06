# ERP EXE Integration Guide

## Deployment Model

Install one local integration agent beside each ERP installation:

```text
ERP.exe -> http://127.0.0.1:47831 -> local SQL Server barmanager
```

The agent never accepts database credentials or SQL from API requests. Local
configuration controls database access and master lookup queries.

## Installation

1. Install Microsoft SQL Server ODBC Driver 11 or newer.
2. Copy `ERP-Purchase-Agent.exe` and `api_config.json` to the ERP installation.
3. Generate two different long random secrets for `api_key` and
   `approval_secret`. During source-based installation, run:

```powershell
.\setup_agent.ps1 -Server ".\SQLEXPRESS" -Database "barmanager"
```
4. Run `ERP-Purchase-Agent.exe`.
5. Call `/api/v1/health` and `/api/v1/schema-check`.

The API binds only to `127.0.0.1`.

## Authentication

Every request must include:

```http
X-API-Key: configured-local-api-key
```

## Required Workflow

The recommended endpoint accepts a PDF and performs extraction, normalization,
invoice validation, and ERP master validation internally. Purchase insertion is
intentionally never performed directly after extraction; the EXE must display
the resulting previews and obtain explicit user approval.

### Recommended: Complete PDF Preview

```http
POST /api/v1/purchases/from-pdf/preview
Content-Type: multipart/form-data
X-API-Key: ...
```

Form fields:

```text
pdf=<invoice.pdf>
companycode=2
yearcode=8
strict_total=true
```

The response contains one approval-ready purchase for each manufacturer found
in the PDF. The EXE displays all purchases and calls `/purchases/insert` once
for each approved token.

### 1. Extract PDF

```http
POST /api/v1/extract
Content-Type: multipart/form-data
X-API-Key: ...
```

Form field:

```text
pdf=<invoice.pdf>
```

The endpoint validates the PDF signature and size, extracts structured source
JSON, and performs no database writes.

### 2. Normalize and Preview Purchase

The EXE or AI normalization layer converts extracted source data into:

```json
{
  "companycode": "2",
  "yearcode": "8",
  "strict_total": true,
  "invoice": {
    "supplier": "BEVCO (FL)",
    "invoice_no": "INV-123",
    "date": "2026-06-05",
    "narration": "Imported from PDF",
    "items": [
      {
        "item_name": "BAGPIPER 375 ML",
        "item_code": "B00025",
        "batch": "B001",
        "ml": 375,
        "packing": "24",
        "strength_name": "25 UP",
        "quantity": 2,
        "rate": 100
      }
    ],
    "tax": {
      "code": "VAT",
      "rate": 20,
      "amount": 40
    },
    "total": 240
  }
}
```

Call:

```http
POST /api/v1/purchases/preview
Content-Type: application/json
X-API-Key: ...
```

Successful response includes:

```json
{
  "ready_for_insert": true,
  "preview_id": "...",
  "approval_token": "...",
  "expires_at": 1780000000,
  "preview": {}
}
```

If supplier or item master data is missing, the API returns every unresolved
record together in HTTP `409`:

```json
{
  "ready_for_insert": false,
  "resolution_required": true,
  "resolution_id": "...",
  "unresolved_count": 1,
  "action": "Resolve each master-data issue, save its mapping, then retry preview.",
  "unresolved": [{
    "type": "item",
    "source": "PDF ITEM NAME",
    "mapping_key": "PDF ITEM NAME|ML:650.00",
    "suggestions": [],
    "actions": {
      "search_master": true,
      "save_mapping": true,
      "check_live_stock": true,
      "create_in_erp": true,
      "instant_create_available": false
    }
  }]
}
```

The EXE should display a Resolve Issues screen containing all records. The user
can select an existing master and save a mapping, check configured live stock,
or open the ERP's existing item-master screen. Then call
`POST /api/v1/resolutions/{resolution_id}/retry`; the PDF does not need to be
uploaded again. Direct instant creation stays disabled until the ERP team supplies
its approved item-creation procedure and required fields.

### Master Search And Mapping

```http
GET /api/v1/masters/suppliers?companycode=2&query=Mount%20Everest
GET /api/v1/masters/items?query=BAGPIPER
GET /api/v1/masters/items/B00025/stock?companycode=2&yearcode=8
```

Save the user's selected supplier:

```http
POST /api/v1/mappings/suppliers

{
  "companycode": "2",
  "source_name": "PDF EXTRACTED SUPPLIER",
  "target_name": "EXACT ERP SUPPLIER NAME"
}
```

Save the user's selected item:

```http
POST /api/v1/mappings/items

{
  "source_name": "PDF EXTRACTED ITEM",
  "ml": 650,
  "item_code": "B00025"
}
```

Duplicate PDF previews return HTTP `409` with `duplicate: true` and the existing
ERP `trnid`/`trnno` when available. The EXE must offer **Open Existing Purchase**
and must not allow insertion.

Mappings are verified before saving, persist in `agent_data/mappings.json`, and
work immediately without restarting the agent.

### 3. User Approval

Display the complete preview. Require explicit user approval. Never insert
automatically after PDF upload.

### 4. Insert

```http
POST /api/v1/purchases/insert
Content-Type: application/json
X-API-Key: ...

{
  "approval_token": "token returned by preview"
}
```

The approval token is signed, expires, and identifies the exact previewed
payload. The endpoint is idempotent: retrying the same token returns the
original result instead of inserting again.

### 5. Recover Status

```http
GET /api/v1/approvals/{preview_id}
X-API-Key: ...
```

Use this when the EXE loses connection after requesting an insert.

## Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Agent and database connectivity |
| GET | `/api/v1/schema-check` | Read-only ERP compatibility check |
| POST | `/api/v1/extract` | PDF extraction only |
| POST | `/api/v1/purchases/from-pdf/preview` | Complete PDF-to-validated-preview flow |
| POST | `/api/v1/purchases/preview` | Validate and resolve masters |
| POST | `/api/v1/purchases/insert` | Approved transactional insert |
| GET | `/api/v1/approvals/{preview_id}` | Status/retry recovery |
| GET | `/api/v1/masters/suppliers` | Search ERP suppliers |
| GET | `/api/v1/masters/items` | Search ERP items |
| GET | `/api/v1/mappings` | List saved mappings |
| POST/DELETE | `/api/v1/mappings/suppliers` | Save/remove supplier mapping |
| POST/DELETE | `/api/v1/mappings/items` | Save/remove item mapping |

Interactive OpenAPI documentation can be enabled for development by setting
`enable_docs` to `true`. It is then available locally at:

```text
http://127.0.0.1:47831/docs
```

Keep documentation disabled in customer production installations.

## Database Writes

The service writes only to:

```text
trnidmst
purchasemain
purchasedetail
PurchaseTaxDetail
```

It never writes to:

```text
TransactionMain
TransactionDetail
TransactionMatch
itemmst
MasterAccountsLedger
```

`trnidmst` is the required parent transaction record for `purchasemain`. The
agent creates it first and writes all four rows/groups inside one SQL
transaction. Any failure rolls back the `trnidmst` row and every purchase row.

## EXE Error Handling

- `401`: API key/configuration error.
- `409`: supplier/item missing, duplicate invoice, or ERP mapping conflict.
- `413`: PDF exceeds configured size.
- `415`: invalid/non-PDF upload.
- `422`: invalid invoice JSON or total.
- `503`: database connection unavailable.

Do not retry `409` or `422` automatically. Show the error to the user. It is
safe to retry `/insert` with the same approval token after a network timeout.

A ready-to-adapt .NET client is included in `ExeClientExample.cs`.

## Logging and Local State

The agent stores:

```text
agent_data/audit.log
agent_data/state/*.json
agent_data/mappings.json
```

The local state files store signed-preview status and idempotency results. They
do not replace the ERP SQL Server database.

## Starting From ERP.exe

The ERP installer should place the agent in its own installation folder. The
ERP can start the agent once using a hidden process and then poll `/api/v1/health`.
Do not start a new agent process for every request.

Example .NET startup:

```csharp
Process.Start(new ProcessStartInfo
{
    FileName = Path.Combine(agentFolder, "ERP-Purchase-Agent.exe"),
    WorkingDirectory = agentFolder,
    UseShellExecute = false,
    CreateNoWindow = true
});
```

For shared-network installations, run one agent on the computer hosting the
shared SQL Server/database. The current production configuration intentionally
binds only to localhost; network access requires a separate authenticated TLS
deployment design.
