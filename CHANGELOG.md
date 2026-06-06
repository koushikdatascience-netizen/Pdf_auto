# Changelog

## 1.5.0 - 2026-06-06

- Detect existing supplier/document-number purchases during PDF preview.
- Detect exact previously inserted PDFs using SHA-256.
- Add reusable resolution sessions so mappings can be fixed without re-uploading.
- Prefer stable normalized item-name plus ML mappings, with legacy batch mappings
  retained for compatibility.

## 1.4.1 - 2026-06-06

- Populate sequential `purchasedetail.slno` values so ERP screens can display
  inserted purchase line items.

## 1.4.0 - 2026-06-06

- Return all unresolved PDF supplier/item mappings together.
- Include live ERP master suggestions and operator resolution actions.
- Add a configurable, read-only live item-stock endpoint.
- Keep new-item creation inside the ERP-approved item-master workflow.

## 1.3.0 - 2026-06-06

- Added persistent supplier and item mappings managed through API endpoints.
- Added ERP supplier/item search endpoints for EXE selection screens.
- Added structured unresolved-master responses.
- Added verified mapping save/delete operations with no restart required.
- Removed manual `api_config.json` edits for each new supplier/item.

## 1.2.0 - 2026-06-05

- Added complete PDF-to-purchase-preview orchestration endpoint.
- Added structured PDF extraction to normalized invoice adapter.
- Added ERP supplier/item-master validation directly after PDF upload.
- Added multi-manufacturer PDF support and `test_pdf_flow.ps1`.
- Added the required `httpx` integration-test dependency.
- Made installation verification stop immediately when tests or schema checks fail.

## 1.1.0 - 2026-06-05

- Added the required `trnidmst` parent transaction insert.
- Generate `trnid` safely from `trnidmst`.
- Keep `trnidmst`, purchase main, details, and tax in one commit/rollback unit.
- Added signed preview approval and idempotent insertion API.
- Added customer-safe configuration generation and schema verification.
- Added Windows install, start, health-check, test, and release scripts.
- Added GitHub Actions unit tests and deployment/security documentation.
