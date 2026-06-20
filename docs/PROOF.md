# OPai Before / After Proof

OPai is the AI coding cost firewall. This page shows the *same work* with and
without OPai, using numbers OPai actually produces. Every figure here is
reproducible with the commands shown — nothing is hand-waved.

## The experiment

Five everyday coding tasks, routed through OPai with `--record`:

| Task | Without OPai | With OPai (routed tier) |
| --- | --- | --- |
| Summarize git status/diff | strong cloud model | **L0** deterministic |
| Write a commit message | strong cloud model | **L0** deterministic |
| Fix a failing unit test | strong cloud model | **L0** local evidence first |
| Add a small helper + docstring | strong cloud model | **L1** local small model |
| Review a PR before opening | strong cloud model | **L1** local small model |

"Without OPai" assumes the common default: send every task straight to a strong
frontier model (the `L3` baseline in
[`hub/model-intelligence/cost_model.yaml`](../hub/model-intelligence/cost_model.yaml)).

## The result (reproducible)

```text
# OPai Savings Report
OPai estimates $0.3600 saved across 5 routed task(s) (100.0% vs un-routed L3 baseline).

| Signal                       | Value           |
| Routed tasks                 | 5               |
| Local routes                 | 5               |
| Cloud calls avoided          | 5               |
| Estimated baseline spend     | $0.3600         |
| Estimated actual spend       | $0.0000         |
| Estimated savings            | $0.3600 (100%)  |
| Context characters saved     | 10090           |
| Context tokens saved (est.)  | 2520            |
```

All five tasks stayed on free local/deterministic tiers, so OPai avoided five
cloud calls and ~2,520 tokens of context bloat — at **$0 actual spend**. On a
real repo with hundreds of such tasks a week, the avoided spend compounds.

## Reproduce it yourself

```sh
opai route "show git status and summarize the diff" --record
opai route "write a commit message for the staged changes" --record
opai route "fix the failing unit test in the auth module" --record
opai route "add a small helper function with a docstring" --record
opai route "review this pull request before I open it" --record
opai savings --markdown
```

## How the estimate is built (honest math)

- Token estimate: ~4 characters per token.
- Baseline: every task would otherwise hit the `L3` strong-model tier.
- Per-tier USD rates and the baseline tier are **configurable** in
  `hub/model-intelligence/cost_model.yaml`; tune them to your providers.
- Savings = baseline tier cost − chosen route cost. Local tiers (`L0`/`L1`) cost
  `$0`, so every local route is pure savings versus the baseline.
- **Privacy:** the ledger stores one-way task hashes and counts only. Raw
  prompts and secrets are never written (`.opaihub/ledger/usage.jsonl`).

## Per-client before/after

OPai activates the same firewall in front of every client. After
`opai activate`, `opai doctor` confirms each one:

| Client | Without OPai | With OPai |
| --- | --- | --- |
| Claude Code | unrouted prompts | `CLAUDE.md` policy + routing |
| Codex | unrouted prompts | `AGENTS.md` + skill discovery |
| GitHub Copilot | unrouted prompts | `.github/copilot-instructions.md` |
| Cursor | unrouted prompts | `.cursor/rules/opai.mdc` |
| Cline | unrouted prompts | `.clinerules/opai.md` |

```sh
opai doctor   # readiness: active / broken / missing per client, with repair
```
