# Architecture

Folder layout:

- `vesta/`: public Vesta CLI and install bootstrap.
- `vestahub/`: registry, health, MCP, workflow, dashboard, discovery, analytics, sandbox, and team modules.
- `opcoding/`: legacy-compatible coding workflow engine used by Vesta for project scans, tests, GitOps, local tools, and routing.
- `hub/registry/`: central metadata for tools, agents, workflows, MCP servers, and models.
- `hub/install/`: install manifest and setup notes.
- `hub/tools/`: future tool adapters and install recipes.
- `hub/agents/`: future agent implementations and overlays.
- `hub/workflows/`: workflow docs and future executable workflow definitions.
- `hub/mcp/`: MCP example configs and future generated profiles.
- `hub/models/`: model routing policy.
- `hub/prompts/`: low-token reusable prompts.
- `hub/context/`: context loading and compression policy.
- `hub/cache/`: generated cache artifacts, ignored by default.
- `hub/security/`: permissions, risky commands, and secrets policy.
- `hub/cost/`: budgets and spend controls.
- `hub/scripts/`: helper scripts for validation and migration.
- `hub/templates/`: reusable templates.
- `hub/project-adapters/`: future stack-specific project overlays.
- `hub/docs/`: documentation.
- `hub/logs/`: generated logs, ignored by default.
- `hub/sandbox/`: future isolated execution area.

Design pattern:

```text
task -> op-hub registry lookup -> cost/security gates -> OPcoding/local tools -> optional model escalation
```

Registries are data-first so adding tools does not require code changes.

Public users should start from `vesta`. Advanced users can still call `op-hub` and `op` directly.
