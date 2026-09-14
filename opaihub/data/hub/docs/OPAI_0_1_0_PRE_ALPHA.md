# Vesta 0.1.0 Pre-Alpha

Vesta is the new brand for the OPcoding and OP AI Hub work. In 0.1.0 pre-alpha, Vesta is a local-first command layer over the existing coding workspace and hub registries.

## What Exists

- `opai` CLI for install, scan, doctor, dashboard, and hub pass-through.
- `op-hub` CLI for registry, MCP, workflow, health, discovery, scheduling, analytics, sandbox, and team commands.
- `op` CLI for coding workflows, project scans, context, tests, GitOps, review, fixes, tools, and Morph adapter.
- Registries for tools, agents, workflows, MCP servers, and models.
- Local `.opaihub/` state with dashboards, schedules, logs, generated MCP config, and project overlay.
- Cost posture that keeps cloud and expensive models disabled by default.

## What Pre-Alpha Means

- Registry-driven architecture is stable enough to extend.
- Public install polish is started, but packaging/publishing is not done.
- Dashboard is static HTML, not a live server UI.
- Workflow execution only runs mapped safe local steps.
- Team/cloud sync exists as disabled local config only.

## Current Brand Commands

```sh
opai install
opai doctor
opai dashboard --html
opai hub discover tools
opai hub analytics status
opai hub sandbox check --command "git status"
```
