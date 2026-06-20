# OPai Effectiveness Benchmarks

OPai benchmarks prove the product promise with local evidence: OPai should be
cheaper, smaller, safer, and more efficient than sending every task straight to
a strong AI coding agent.

The default benchmark is local and cheap. It makes no cloud calls, stores no raw
prompts, and writes only hashes, counts, costs, and artifact hashes under
`.opaihub/benchmarks/`.

## Commands

```sh
opai benchmark list
opai benchmark run --suite local --mode both
opai benchmark run --suite max --mode both
opai benchmark gate --min-context-reduction 10
opai benchmark compare --format markdown
opai benchmark export --harness promptfoo
opai benchmark report --format markdown
opai benchmark report --format json
opai benchmark report --format html
```

Record a redacted benchmark event to the governance audit trail:

```sh
opai benchmark run --suite local --mode both --audit
```

## Local suite

The MVP suite compares normal AI use against OPai-routed use across:

- Planning
- Bug triage
- Test failure debugging
- Security review
- Release preflight
- Docs
- Dependency update
- Mobile readiness

The baseline assumes a task is sent directly to the configured frontier baseline
tier from the local cost model. The OPai run uses the existing local-first router
and measures the compact context payload OPai would give an agent.

## Max suite

`opai benchmark run --suite max --mode both` is OPai's highest local benchmark
mode. It is still offline and privacy-safe, but it is shaped around the outside
benchmarks that matter most for coding agents:

- SWE-bench Pro style long-horizon bug fixes, regressions, and refactors.
- Terminal-Bench style command-line build, CI, security, and release tasks.
- Aider Polyglot style multi-language edit-and-repair tasks.
- promptfoo style assertion, cost, latency, and red-team boundaries.
- OPai governance tasks for policy, audit, release, dependency, and mobile gates.

The max suite reports an `opai_effectiveness_index` from `0` to `100` and a
`leaderboard_grade`. A local `A+` means OPai is maxing its control-plane metrics
locally; it does **not** mean OPai has submitted to SWE-bench, Terminal-Bench, or
Aider.

## OPai Efficiency Score

Each run reports:

- `context_reduction_ratio`
- `paid_call_avoidance_ratio`
- `estimated_cost_reduction_ratio`
- `time_to_evidence_seconds`
- `success_rate`
- `risk_events_blocked`
- `human_interventions`
- `opai_effectiveness_index`
- `leaderboard_grade`

Ratios are capped at `50x` so zero-cloud-call fixture runs remain honest and do
not produce infinite multipliers.

## Benchmark gates

`opai benchmark gate` turns the latest benchmark run into a CI-ready proof gate.
It exits non-zero when OPai no longer meets the required efficiency bar:

```sh
opai benchmark gate \
  --min-context-reduction 10 \
  --min-paid-call-avoidance 1 \
  --min-cost-reduction 1 \
  --min-success-rate 1.0 \
  --min-effectiveness-index 95 \
  --require-risk-blocks
```

This is the core product wedge: OPai does not just claim cost control; it can
block regressions when local evidence stops proving the claim.

## Regression comparison

`opai benchmark compare` compares the latest two benchmark runs and flags
regressions in metrics where bigger is better (context/cost/paid-call reduction,
success, risk blocks) or smaller is better (time to evidence, human
interventions).

```sh
opai benchmark compare --format markdown
```

## Promptfoo export

`opai benchmark export --harness promptfoo` writes a privacy-safe starter config
under `.opaihub/benchmarks/promptfooconfig.yaml`. The export uses task ids and
one-way task hashes rather than raw prompts, so provider-backed evals remain
explicit and opt-in.

## Privacy

Benchmark history stores:

- Task id and category
- One-way task hash
- Context bytes and estimated tokens
- Estimated cost
- Local commands used
- Cloud calls avoided
- Policy events and risk blocks
- Assertion results
- Artifact hashes

Benchmark history does not store raw prompts by default.

## External validation

`promptfoo` is the first optional external harness because it supports coding
agent evaluations, assertions, scoring, and cost checks. OPai does not invoke it
by default; provider-backed comparisons should be opt-in.

Later validation layers can include SWE-bench Verified Mini, SWE-bench Pro, and
Terminal-Bench.

## Public claims

Do not claim a universal "50x" result from one run. Use benchmark evidence:

```text
OPai reduced context by 12x and avoided 90% of paid calls on the local benchmark suite.
```

The intended 10x-50x target applies to avoidable paid calls and context waste on
deterministic, evidence-first tasks, not every possible coding task.
