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

## Anti-Waste Rules

- Do not ask an agent to run `git status`; run git locally.
- Do not send the whole repo when a diff and two files are enough.
- Do not run full test suites before targeted tests unless the change is shared or release-bound.
- Do not call multiple agents with identical context; batch shared context once.
- Summarize long sessions into project memory before continuing.
