# Production Deployment

## Supported Deployment Model

Install one agent per local ERP database. The ERP EXE calls the agent over
localhost. Do not expose the current agent directly to a LAN or the internet.

## Customer Installation

1. Install Python 3.9+ and a Microsoft SQL Server ODBC driver.
2. Clone/download the repository into a dedicated application folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -Server ".\SQLEXPRESS" -Database "barmanager"
.\verify_install.ps1
```

4. Review `schema_report.json`.
5. Start the service with `.\start_agent.ps1`.
6. Run `.\test_api_flow.ps1` for preview-only validation.
7. Run `.\test_pdf_flow.ps1 -PdfFile "invoice.pdf"` to validate the complete
   PDF-to-ERP-preview flow.
8. Use `.\test_pdf_flow.ps1 -PdfFile "invoice.pdf" -Insert` only against an
   approved test database.
9. Confirm the inserted purchase appears correctly inside the ERP EXE and its
   reports before production use.

## Configuration Per Customer

Each customer must have a separate `api_config.json`, API key, approval secret,
and database connection. Never copy one customer's configuration to another.

Before configuring or inserting into a different ERP database, run the read-only
compatibility audit:

```powershell
.\audit_new_database.ps1 `
  -Server ".\SQLEXPRESS" `
  -Database "NEW_DATABASE" `
  -Driver "ODBC Driver 17 for SQL Server"
```

Review `new_database_audit.json`. Do not enable insertion until
`compatible` is true and every `manual_review` item has been confirmed with the
ERP team. This audit does not modify `api_config.json` and performs no writes.

For a validated Mandai installation, enable the dedicated profile:

```powershell
.\enable_mandai_profile.ps1
```

Restart and run PDF preview only. Confirm calculated case/bottle quantities,
Mandai T1/T2/T3/T4 amounts, duty, final total, account codes, and blank/configured
`tppassno` before approving the first test insert. The profile uses
`agent_data_mandai` for mappings and approval state, keeping them separate from
every previously configured ERP database.

Confirm these ERP-specific values:

```text
transaction_type = Purchase_Add
usercode          = valid dbo.users.usercode
sync              = N
```

The configured `usercode` must exist because `trnidmst.usercode` has a foreign
key to the ERP users table.

## Service Operation

The ERP installer may start `ERP-Purchase-Agent.exe` once in the background and
poll `/api/v1/health`. Do not start one process per request.

Back up:

```text
api_config.json
agent_data/audit.log
agent_data/state/
agent_data/mappings.json
```

Restrict filesystem access to the ERP user/service account.

## Upgrade

1. Stop the running agent.
2. Back up `api_config.json` and `agent_data/`.
3. Replace application source/executable files.
4. Keep the existing private configuration.
5. Run `.\verify_install.ps1`.
6. Start the agent and perform a preview-only test.

## Rollback

Stop the new version, restore the previous application files, and keep the same
configuration and state directory. Database inserts are transactional; failed
purchase attempts roll back automatically.
