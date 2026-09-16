# Vesta 0.1.1 Cost Reduction

Vesta 0.1.1 pre-alpha is tuned to make AI coding cheaper than using an assistant directly.

## Default Rules

- Start every workflow with local evidence: git status, diff stat, targeted tests, logs, registry metadata, and cached context.
- Do not call cloud models by default.
- Do not store full prompts by default.
- Do not load whole repositories by default.
- Route release, deploy, publish, and security work through local preflight before any model.
- Ask before L2+ cloud calls, paid tools, deploys, destructive commands, or contexts over 12,000 estimated tokens.

## New Budget Defaults

```text
Daily limit: $0.50
Monthly limit: $5.00
Per-task soft limit: $0.10
Per-task hard limit: $0.50
Default context cap: 6,000 chars
Hard context cap: 12,000 chars
```

## Commands

```sh
vesta route "fix failing tests"
vesta route "prepare release" --full-evidence
vesta models recommend "prepare release"
op cost . report
op ask "explain project" --project .
```

`op ask` returns a prompt for explicit use, but the cache stores only prompt hashes and metadata unless `VESTA_STORE_PROMPTS=1` is set.

## Escalation

```text
L0 local deterministic evidence
L1 cache or local lightweight model
L2 cheap cloud model only with confirmation
L3 strong model only with explicit evidence and confirmation
L4 GPT-5.5 Max only for failed lower tiers or high-risk incidents
```
