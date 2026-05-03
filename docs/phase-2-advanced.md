# Phase 2 Advanced Plan

## Add Model Adapters

- Local model adapter.
- Cheap cloud coding adapter.
- Strong cloud coding adapter.
- GPT-5.5 Max adapter behind explicit confirmation.

## Add MCP Runtime

- Filesystem MCP with roots.
- GitHub MCP read-only default profile.
- Local memory MCP.
- Testing MCP.
- Package lookup MCP with local cache.
- Browser MCP for localhost and docs research.

## Add Agent Runner

- Load `agents/catalog.yaml`.
- Resolve route from `opcoding.cost`.
- Batch agents that share context.
- Deduplicate prompts by task hash.
- Log estimated and actual cost.

## Add Automation

- Pre-commit secret and review checks.
- CI workflow to run `op review` and `op test --dry-run`.
- Session summarizer after long runs.
- Project health dashboard.
