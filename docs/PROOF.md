# Vesta Before / After Proof

Vesta is the AI coding cost firewall. This page shows the *same work* with and
without Vesta, using numbers Vesta actually produces. Every figure here is
reproducible with the commands shown — nothing is hand-waved.

## The experiment

Five everyday coding tasks, routed through Vesta with `--record`:

| Task | Without Vesta | With Vesta (routed tier) |
| --- | --- | --- |
| Summarize git status/diff | strong cloud model | **L0** deterministic |
| Write a commit message | strong cloud model | **L0** deterministic |
| Fix a failing unit test | strong cloud model | **L0** local evidence first |
| Add a small helper + docstring | strong cloud model | **L1** local small model |
| Review a PR before opening | strong cloud model | **L1** local small model |

"Without Vesta" assumes the common default: send every task straight to a strong
frontier model (the `L3` baseline in
[`hub/model-intelligence/cost_model.yaml`](../hub/model-intelligence/cost_model.yaml)).

## The result (reproducible)

```text
# Vesta Savings Report
Vesta estimates $0.3600 saved across 5 routed task(s) (100.0% vs un-routed L3 baseline).

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

All five tasks stayed on free local/deterministic tiers, so Vesta avoided five
cloud calls and ~2,520 tokens of context bloat — at **$0 actual spend**. On a
real repo with hundreds of such tasks a week, the avoided spend compounds.

## Reproduce it yourself

```sh
vesta route "show git status and summarize the diff" --record
vesta route "write a commit message for the staged changes" --record
vesta route "fix the failing unit test in the auth module" --record
vesta route "add a small helper function with a docstring" --record
vesta route "review this pull request before I open it" --record
vesta savings --markdown
```

## How the estimate is built (honest math)

- Token estimate: ~4 characters per token.
- Baseline: every task would otherwise hit the `L3` strong-model tier.
- Per-tier USD rates and the baseline tier are **configurable** in
  `hub/model-intelligence/cost_model.yaml`; tune them to your providers.
- Savings = baseline tier cost − chosen route cost. Local tiers (`L0`/`L1`) cost
  `$0`, so every local route is pure savings versus the baseline.
- **Privacy:** the ledger stores one-way task hashes and counts only. Raw
  prompts and secrets are never written (`.vestahub/ledger/usage.jsonl`).

## Per-client before/after

Vesta activates the same firewall in front of every client. After
`vesta activate`, `vesta doctor` confirms each one:

| Client | Without Vesta | With Vesta |
| --- | --- | --- |
| Claude Code | unrouted prompts | `CLAUDE.md` policy + routing |
| Codex | unrouted prompts | `AGENTS.md` + skill discovery |
| GitHub Copilot | unrouted prompts | `.github/copilot-instructions.md` |
| Cursor | unrouted prompts | `.cursor/rules/vesta.mdc` |
| Cline | unrouted prompts | `.clinerules/vesta.md` |

```sh
vesta doctor   # readiness: active / broken / missing per client, with repair
```
