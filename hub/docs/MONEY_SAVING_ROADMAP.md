# OPai 10x Money-Saving Roadmap

This is the working surface for the **OPai 10x Money-Saving Roadmap** milestone
(#49-#55). Everything here is local, privacy-safe (one-way hashes, no prompts,
no telemetry), and tested.

## #49 Real Savings Ledger

Every routed action records a savings event with one schema (a benchmark event
and a normal route share it). Events carry `agent`, `repo`, and `source`, and
roll up by day/week/month/agent/repo.

```sh
opai route "<task>" --record      # record a savings event
opai savings --rollups            # daily/weekly/monthly/agent/repo totals
opai savings --markdown --export report.md   # private export (no prompt text)
```

## #50 Hard Cost Firewall

A firewall that *prevents* avoidable paid/cloud calls, not just a dashboard.

```sh
opai budget set --daily 2 --monthly 30   # per-project ceilings (atop the policy profile)
opai budget status                       # spend today/month vs ceilings, remaining
opai budget gate "<task>"                # exits non-zero if the escalation target exceeds policy/budget
opai budget panic                        # deterministic/local-only until disabled
opai budget panic --off
```

The gate checks the *escalation target* (`recommend_model`) because OPai's local
router never self-escalates. Panic mode blocks every paid/cloud route.

## #51 10x Context Engine

```sh
opai context profile             # ranked waste sources + before/after bytes/tokens/cost
opai context profile --markdown
opai context ignores --clients cursor,claude,copilot,cline   # never clobbers user rules
opai context pack --changed      # tiny redacted pack of changed files + adjacent tests
```

## #52 Unified Agent Routing

`opai doctor` reports active/broken/missing for Claude, Codex, Cursor, Cline,
and Copilot; `opai activate --repair` fixes stale paths. Reproducible
baseline-vs-OPai demos: [CLIENT_DEMOS.md](CLIENT_DEMOS.md).

## #53 Private Paid Distribution

Controlled alpha: the public site never leaks raw install URLs; paid users get a
private install command/package after checkout. See
[COMMERCIAL_ACCESS_AND_IP_PROTECTION.md](../../docs/COMMERCIAL_ACCESS_AND_IP_PROTECTION.md).

## #54 Proof Bundles and Team Pilot Reports

```sh
opai proof bundle --out proof.json   # benchmark + savings + policy + audit + signed evidence
opai proof bundle --markdown
opai proof verify proof.json         # re-checks signature + artifacts; fails closed on tamper
```

No raw prompts; the bundle reports governed agents, risk blocks, and policy
exceptions for a budget owner.

## #55 External Benchmark Validation

```sh
opai benchmark list                          # local + max suites, external harnesses
opai benchmark run --suite max --mode both   # leaderboard-aligned local stress suite
opai benchmark export --suite local          # opt-in promptfoo handoff (provider-backed)
opai benchmark gate --min-effectiveness-index 95   # CI proof gate
```

Provider-backed harnesses (promptfoo, SWE-bench, Terminal-Bench, Aider Polyglot)
are opt-in and clearly separated from local OPai proof. Run them under a budget
(`opai budget`) and panic mode to keep provider spend gated.
