# Changelog

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
