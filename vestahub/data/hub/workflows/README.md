# Vesta Hub Workflows

Workflows are registry entries in `hub/registry/workflows.yaml`.

Each workflow declares:

- Trigger
- Inputs
- Deterministic/local-first steps
- Agents used
- Tools used
- Cost policy
- Permission policy
- Output artifacts
- Failure handling

Phase 1 does not execute workflows automatically. Use:

```powershell
python -m vestahub workflow run feature_plan
```

to inspect the workflow plan. Phase 2 will add safe execution overlays.
