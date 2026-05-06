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
- Optional local tools install into an external OPai tool cache instead of a
  per-project `.opcoding-tools` directory.
- `opai activate` writes AI-client ignore files such as `.claudeignore` and
  `.opaiignore` so generated caches stay out of model context.
- `opai slim` reports generated cache weight; `opai slim --clean` removes
  generated project bloat while preserving OPai project state.
- `opai route` is compact by default and omits full evidence output; use
  `opai route --verbose "<task>"` only when a human or agent needs raw evidence.
- AI CLI launch wrappers print only the `Using OPai` status line by default.
  Set `OPAI_WELCOME=1` or pass `opai launch <tool> --welcome` for graphics.
- `opai hub analytics status` is local-only and reports `estimated_spend_usd` without telemetry.

Budgets live in `hub/cost/budget.yaml`; model routing lives in `hub/models/routing.yaml`.
