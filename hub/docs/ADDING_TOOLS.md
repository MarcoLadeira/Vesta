# Adding Tools

Add tools to `hub/registry/tools.yaml`.

Required fields:

- `id`
- `name`
- `category`
- `description`
- `status`
- `type`
- `cost_level`
- `permission_level`
- `local_first`
- `open_source`
- `requires_api_key`
- `env_vars`
- `install_command`
- `run_command`
- `health_check`
- `inputs`
- `outputs`
- `tags`
- `docs_url`
- `notes`
- `enabled_by_default`

Before adding a tool:

1. Check for duplicates with `python -m opaihub list-tools`.
2. Prefer free/open-source/local tools.
3. Mark paid/cloud tools as optional and disabled by default.
4. Add a safe health check that does not mutate state.
5. Document required environment variables by name only.

Do not add raw API keys to the registry.

## CLI Add

Phase 2 can append a tool entry directly:

```powershell
python -m opaihub tool add `
  --id my-tool `
  --name "My Tool" `
  --category coding `
  --description "What it does" `
  --run-command "my-tool --help" `
  --health-command "my-tool --version" `
  --enabled-by-default
```

Use `python -m opaihub validate --registry tools` after adding an entry.
