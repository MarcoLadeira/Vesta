# Phase 1 MVP Build

This folder now contains the first third of OPcoding:

- reusable CLI
- project onboarding
- stack and command detection
- compressed context files
- local cost routing
- safe GitOps helpers
- test command detection and logs
- local review/fix heuristics
- agent, prompt, workflow, adapter, MCP, permission, and budget definitions

## Done Means

You can point OPcoding at a project and run:

```powershell
python -m opcoding init C:\path\to\project
python -m opcoding auto "build a settings page" --project C:\path\to\project
python -m opcoding test C:\path\to\project --dry-run
python -m opcoding git C:\path\to\project summary
```

The next third should wire these routes to actual model adapters and MCP servers.
