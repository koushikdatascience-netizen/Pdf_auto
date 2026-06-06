# Changelog

## 1.9.2 - 2026-06-06

- Show each existing ERP duplicate purchase only once when a PDF contains
  multiple manufacturer groups linked to the same document.

## 1.9.1 - 2026-06-06

- Remove the API-key prompt from the localhost operator screen.
- Authenticate browser users automatically with a signed HttpOnly same-site session.
- Keep API-key authentication unchanged for the ERP EXE and external API clients.

## 1.9.0 - 2026-06-06

- Add an operator workflow tracker from upload through committed insertion.
- Show mapping completion progress and master-resolution statistics.
- Show the exact database row changes and validated net value before approval.
- Add a professional insertion receipt with generated ERP transaction IDs.
- Improve session-security messaging, responsive layout, and status feedback.

## 1.8.0 - 2026-06-06

- Add a local Purchase Import operator UI at `http://127.0.0.1:47831`.
- Support PDF upload, duplicate display, ERP master search and verified mapping.
- Retry saved resolution sessions without uploading the PDF again.
- Show complete validated purchase, item, tax, and total previews before insert.
- Require typed approval before transactional insertion.
- Keep item creation exclusively inside the ERP Item Master screen.

## 1.7.0 - 2026-06-06

- Keep item-master creation exclusively inside the ERP EXE.
- Rank ERP item suggestions using normalized names, known abbreviations, ML,
  packing, and strength.
- Return match score, confidence, reasons, and mandatory user confirmation.
- Add read-only PDF-preview versus manual-purchase comparison tooling.
- Add read-only item-creation procedure, trigger, and dependency auditing.

## 1.6.0 - 2026-06-06

- Add a strict Mandai ERP purchase profile derived from real manual purchases.
- Convert cases to bottle quantities using verified `itemmst.packing`.
- Use the configured warehouse supplier and `itemmst` per-case T3/T4 amounts,
  matching manually entered Mandai purchases.
- Populate Mandai header, detail tax/charge, duty, box-rate, MRP, and account rows.
- Validate Mandai purchase account and shop before preview or insertion.
- Keep `tppassno` blank/configurable when it is not present in the source PDF.

## 1.5.1 - 2026-06-06

- Add a separate read-only audit command for onboarding different ERP databases.
- Audit target/master tables, columns, lengths, precision, keys, foreign keys,
  checks, triggers, and referencing SQL modules.
- Prevent new-database auditing from changing the active agent configuration.

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
