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
7. Use `.\test_api_flow.ps1 -Insert` only against an approved test database.
8. Confirm the inserted purchase appears correctly inside the ERP EXE and its
   reports before production use.

## Configuration Per Customer

Each customer must have a separate `api_config.json`, API key, approval secret,
and database connection. Never copy one customer's configuration to another.

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
