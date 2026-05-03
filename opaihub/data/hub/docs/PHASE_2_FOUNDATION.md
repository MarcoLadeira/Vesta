# Phase 2 Foundation

The second implementation pass adds project-specific state and safe execution plumbing.

## Added

- `.opaihub/project.json` project overlays.
- Tool enable/disable state.
- MCP enable/disable state.
- Effective tool and MCP views.
- Registry validation.
- MCP config rendering.
- Workflow planning with optional execution of mapped safe local steps.
- Health history in `.opaihub/health/history.jsonl`.
- Local model discovery without downloads or startup.
- Dashboard generation at `.opaihub/dashboard.md`.
- CLI-backed tool registry additions through `op-hub tool add`.

## Commands

```powershell
python -m opaihub project attach
python -m opaihub project status
python -m opaihub validate
python -m opaihub tool enable playwright-optional
python -m opaihub tool disable morph-fast-apply
python -m opaihub mcp enable testing
python -m opaihub mcp render --write
python -m opaihub workflow run new_project_onboarding
python -m opaihub workflow run new_project_onboarding --execute
python -m opaihub models discover-local
python -m opaihub dashboard
python -m opaihub health history
python -m opaihub tool add --id my-tool --description "What it does"
```

## Safety

- Workflow execution only runs steps with explicit safe command mappings.
- Cloud and paid tools stay disabled unless explicitly enabled in project state.
- Local model discovery never downloads or starts models.
- MCP config rendering writes generated config only under `.opaihub/generated/`.
