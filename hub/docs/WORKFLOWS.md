# Workflows

Workflows combine agents and tools into repeatable local-first paths.

Starter workflows live in `hub/registry/workflows.yaml`:

- `new_project_onboarding`
- `feature_plan`
- `bug_fix`
- `test_failure_debug`
- `pull_request_review`
- `dependency_update`
- `security_audit`
- `docs_generation`
- `research_brief`
- `tool_installation`
- `tool_health_check`
- `daily_hub_check`
- `local_model_setup`
- `mcp_server_setup`

Inspect a workflow:

```powershell
opai hub workflow run feature_plan
```

By default, workflows display plans only. Passing `--execute` runs only steps that have explicit safe local command mappings.

```powershell
opai hub workflow run new_project_onboarding --execute
```

Unmapped or risky steps are skipped rather than guessed.

Create a local schedule intent:

```powershell
opai hub schedule create daily_hub_check --cadence daily
```

This writes `.opaihub/schedules.json`; it does not start a daemon.
