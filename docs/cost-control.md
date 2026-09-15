# Cost Control

OPcoding treats expensive reasoning as a scarce resource.

## Routing Levels

- `L0`: deterministic local tool or script.
- `L1`: cache or local lightweight model.
- `L2`: cheap coding model after local evidence collection.
- `L3`: stronger model for broad, risky, or ambiguous work.
- `L4`: GPT-5.5 Max. Requires explicit confirmation.

## Required Before AI

1. Load `.opcoding/context.md`.
2. Collect only relevant diff, logs, and files.
3. Redact secrets.
4. Check prompt cache and route logs.
5. Estimate tokens.
6. Check budget.

## Unaccounted Spend

A provider call is recorded twice: once when it is dispatched, once when its
result lands. If the second record never arrives -- the process died, the
machine slept, the write failed -- the request still left Vesta and may still
have been billed. The work happened; only its cost is unknown.

Such a call is **outstanding** while it could still be running, and
**abandoned** once it could not: either the process that dispatched it is gone,
or six hours have passed (`ABANDON_AFTER_SECONDS` in
`opaihub/call_reconciliation.py`). A call Vesta's own process started is never
abandoned while that process lives, so a long turn is never retired underneath
itself.

Abandoning records `cost_unknown`. It never invents a number, and a late result
that arrives afterwards still supersedes it.

What this changes:

- **Reports** — `vesta savings`, `vesta budget status`, and Settings → Model
  Usage count abandoned calls as unaccounted spend permanently. Totals stay
  labelled a lower bound, never a verified figure.
- **Gating** — `vesta budget gate` asks for confirmation when a call was
  abandoned *today* and a budget ceiling is set, because a ceiling cannot be
  enforced against an incomplete total. That window clears on its own by the
  next day. A call merely in flight never prompts.

## Anti-Waste Rules

- Do not ask an agent to run `git status`; run git locally.
- Do not send the whole repo when a diff and two files are enough.
- Do not run full test suites before targeted tests unless the change is shared or release-bound.
- Do not call multiple agents with identical context; batch shared context once.
- Summarize long sessions into project memory before continuing.
