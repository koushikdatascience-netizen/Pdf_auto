# GitHub Publishing Checklist

Before the first push:

```powershell
python -B -m unittest discover -v
.\export_github_source.ps1
cd github_source
```

Confirm these are not staged:

```text
api_config.json
agent_data/
uploads/
app_output/
json_output/
schema_report.json
*.pdf
dist/
build/
```

Recommended first publish:

```powershell
git init
git add .
git status
git commit -m "Initial ERP purchase integration agent"
git branch -M main
git remote add origin https://github.com/YOUR-ORG/erp-purchase-agent.git
git push -u origin main
```

If Git initialization is blocked by permissions in the current folder, copy the
clean exported folder to a normal user-owned folder such as
`C:\Users\<you>\source\erp-purchase-agent`, then run the Git commands there.

After pushing, confirm the GitHub Actions `tests` workflow passes. Create a
release only after testing the packaged executable against an approved test
database.

Choose and add the appropriate repository license before making the repository
public. Do not assume an open-source license if the ERP integration is private.
