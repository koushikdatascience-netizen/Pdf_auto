# Changelog

## 1.1.0 - 2026-06-05

- Added the required `trnidmst` parent transaction insert.
- Generate `trnid` safely from `trnidmst`.
- Keep `trnidmst`, purchase main, details, and tax in one commit/rollback unit.
- Added signed preview approval and idempotent insertion API.
- Added customer-safe configuration generation and schema verification.
- Added Windows install, start, health-check, test, and release scripts.
- Added GitHub Actions unit tests and deployment/security documentation.
