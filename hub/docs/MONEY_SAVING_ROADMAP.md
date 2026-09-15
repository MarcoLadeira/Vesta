# Vesta 10x Money-Saving Roadmap

This is the working surface for the **Vesta 10x Money-Saving Roadmap** milestone
(#49-#55). Everything here is local, privacy-safe (one-way hashes, no prompts,
no telemetry), and tested.

## #49 Real Savings Ledger

Every routed action records a savings event with one schema (a benchmark event
and a normal route share it). Events carry `agent`, `repo`, and `source`, and
roll up by day/week/month/agent/repo.

```sh
vesta route "<task>" --record      # record a savings event
vesta savings --rollups            # daily/weekly/monthly/agent/repo totals
vesta savings --markdown --export report.md   # private export (no prompt text)
```

## #50 Hard Cost Firewall

A firewall that *prevents* avoidable paid/cloud calls, not just a dashboard.

```sh
vesta budget set --daily 2 --monthly 30   # per-project ceilings (atop the policy profile)
vesta budget status                       # spend today/month vs ceilings, remaining
vesta budget gate "<task>"                # exits non-zero if the escalation target exceeds policy/budget
vesta budget panic                        # deterministic/local-only until disabled
vesta budget panic --off
```

The gate checks the *escalation target* (`recommend_model`) because Vesta's local
router never self-escalates. Panic mode blocks every paid/cloud route.

## #51 10x Context Engine

```sh
vesta context profile             # ranked waste sources + before/after bytes/tokens/cost
vesta context profile --markdown
vesta context ignores --clients cursor,claude,copilot,cline   # never clobbers user rules
vesta context pack --changed      # tiny redacted pack of changed files + adjacent tests
```

## #52 Unified Agent Routing

`vesta doctor` reports active/broken/missing for Claude, Codex, Cursor, Cline,
and Copilot; `vesta activate --repair` fixes stale paths. Reproducible
baseline-vs-Vesta demos: [CLIENT_DEMOS.md](CLIENT_DEMOS.md).

## #53 Free Public Alpha Distribution

The public alpha has no checkout, licence, invitation, or private-access gate.
Publish only verified release paths and keep source/development installation
guidance separate from end-user artifact guidance. See the
[Free Public Alpha policy](PRICING_AND_EDITIONS.md).

## #54 Proof Bundles for Alpha Users and Teams

```sh
vesta proof bundle --out proof.json   # benchmark + savings + policy + audit + signed evidence
vesta proof bundle --markdown
vesta proof verify proof.json         # re-checks signature + artifacts; fails closed on tamper
```

No raw prompts; the bundle reports governed agents, risk blocks, and policy
exceptions for a user or team.

## #55 External Benchmark Validation

```sh
vesta benchmark list                          # local + max suites, external harnesses
vesta benchmark run --suite max --mode both   # leaderboard-aligned local stress suite
vesta benchmark export --suite local          # opt-in promptfoo handoff (provider-backed)
vesta benchmark gate --min-effectiveness-index 95   # CI proof gate
```

Provider-backed harnesses (promptfoo, SWE-bench, Terminal-Bench, Aider Polyglot)
are opt-in and clearly separated from local Vesta proof. Run them under a budget
(`vesta budget`) and panic mode to keep provider spend gated.
