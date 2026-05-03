# Cost Control

The hub uses tiered routing:

- `L0`: local deterministic tools.
- `L1`: cache or local lightweight model.
- `L2`: cheap coding model.
- `L3`: stronger reasoning model.
- `L4`: GPT-5.5 Max or equivalent, always confirmation-gated.

Rules:

- Run local scans, tests, linters, formatters, and registry lookup first.
- Load diffs and logs before whole files.
- Cache repeated prompt bundles and tool results.
- Batch agents with shared context.
- Ask before cloud tools.
- Ask before paid models.
- Ask before destructive commands.
- Do not send secrets to cloud models.
- `opai install` does not install optional tools unless `--with-tools` is passed.
- `opai hub analytics status` is local-only and reports `estimated_spend_usd` without telemetry.

Budgets live in `hub/cost/budget.yaml`; model routing lives in `hub/models/routing.yaml`.
