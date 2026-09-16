# Phase 2 Foundation

The second implementation pass adds project-specific state and safe execution plumbing.

## Added

- `.vestahub/project.json` project overlays.
- Tool enable/disable state.
- MCP enable/disable state.
- Effective tool and MCP views.
- Registry validation.
- MCP config rendering.
- Workflow planning with optional execution of mapped safe local steps.
- Health history in `.vestahub/health/history.jsonl`.
- Local model discovery without downloads or startup.
- Dashboard generation at `.vestahub/dashboard.md`.
- CLI-backed tool registry additions through `op-hub tool add`.

## Commands

```powershell
python -m vestahub project attach
python -m vestahub project status
python -m vestahub validate
python -m vestahub tool enable playwright-optional
python -m vestahub tool disable morph-fast-apply
python -m vestahub mcp enable testing
python -m vestahub mcp render --write
python -m vestahub workflow run new_project_onboarding
python -m vestahub workflow run new_project_onboarding --execute
python -m vestahub models discover-local
python -m vestahub dashboard
python -m vestahub health history
python -m vestahub tool add --id my-tool --description "What it does"
```

## Safety

- Workflow execution only runs steps with explicit safe command mappings.
- Cloud and paid tools stay disabled unless explicitly enabled in project state.
- Local model discovery never downloads or starts models.
- MCP config rendering writes generated config only under `.vestahub/generated/`.
