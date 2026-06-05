# Security Policy

## Important Rules

- Never commit or publish `api_config.json`.
- Never publish customer PDFs, extracted JSON, logs, or schema reports.
- Never expose the agent directly to the internet.
- Keep `enable_docs` disabled in production.
- Use a separate API key and approval secret for every installation.
- Use a dedicated SQL login with only the permissions required by the agent
  whenever the ERP environment supports it.

## Database Permissions

The agent requires:

- `SELECT` on configured supplier/item lookup tables.
- `SELECT` and `INSERT` on `trnidmst`, `purchasemain`, `purchasedetail`, and
  `PurchaseTaxDetail`.
- Permission to execute `sys.sp_getapplock`.

It does not require write access to item masters, supplier masters,
`TransactionMain`, `TransactionDetail`, or `TransactionMatch`.

## Reporting A Vulnerability

Do not create a public issue containing credentials, invoice data, or database
details. Contact the repository owner privately with a minimal reproduction and
remove all customer data.
