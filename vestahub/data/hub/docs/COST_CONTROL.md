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
- `vesta install` does not install optional tools unless `--with-tools` is passed.
- Optional local tools install into an external Vesta tool cache instead of a
  per-project `.opcoding-tools` directory.
- `vesta activate` writes AI-client ignore files such as `.claudeignore` and
  `.vestaignore` so generated caches stay out of model context.
- `vesta slim` reports generated cache weight; `vesta slim --clean` removes
  generated project bloat while preserving Vesta project state.
- `vesta route` is compact by default and omits full evidence output; use
  `vesta route --verbose "<task>"` only when a human or agent needs raw evidence.
- AI CLI launch wrappers print only the `Using Vesta` status line by default.
  Set `VESTA_WELCOME=1` or pass `vesta launch <tool> --welcome` for graphics.
- `vesta hub analytics status` is local-only and reports `estimated_spend_usd` without telemetry.

Budgets live in `hub/cost/budget.yaml`; model routing lives in `hub/models/routing.yaml`.
