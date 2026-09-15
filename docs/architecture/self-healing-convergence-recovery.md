# Vesta Self-Healing Convergence & Recovery Intelligence

## Status

Canonical GitHub epic: [#569](https://github.com/MarcoLadeira/Vesta/issues/569)  
Parent programme: [#561](https://github.com/MarcoLadeira/Vesta/issues/561)

This document is the repository-level architecture and delivery index for Vesta's self-healing convergence programme. GitHub issues remain the authoritative work backlog; this file records stable ownership, invariants, sequence and evidence expectations for maintainers and coding agents.

## Product outcome

Vesta should notice that an agent is getting stuck before the user does, preserve everything useful, diagnose the earliest evidence-backed divergence, try a bounded low-cost recovery and only then stop with an honest explanation.

The programme exists because the current no-progress behavior can:

- wait until a hard 60-step/elapsed-time guard;
- treat useful investigation as no progress and irrelevant mutation as progress;
- stop without attempting structured recovery;
- overwrite a no-progress cause with generic provider failure;
- offer an ambiguous Retry action;
- lose the economic value of already completed discovery.

## Canonical ownership

This programme does not create a second runtime, event bus, router, ledger, verifier or UI state machine.

| Concern | Canonical owner |
|---|---|
| Task/run lifecycle and deterministic runtime truth | #295 |
| Durable history, crash recovery and state integrity | #517 / #613 |
| External-operation identity and reconciliation | #616 |
| Terminal result | #618 |
| Trajectory projection | #564 |
| Trajectory economics | #565 |
| Routing and Cost Firewall policy | #350 |
| Provider/tool health | #520 |
| Repository/worktree evidence | #521 / #620 |
| Verification truth | #522 |
| Privacy, authority and redaction | #516 / #526 / #527 / #622 |
| Product presentation | #234 / #377 / #577 |
| Evaluation | #515 / #574 / #657 |

## Architecture

```text
Canonical task, run, operation and evidence history
    -> ProgressEvidence projection
    -> FailureEnvelope and divergence diagnosis
    -> Unified convergence controller
    -> Deterministic recovery candidate selection
    -> Cost, policy, authority and capability filter
    -> Durable recovery checkpoint
    -> Re-plan / tool fallback / provider continuation / user question / stop
    -> Canonical RunResult, receipt and trajectory economics
    -> Offline replay, skill admission and shadow policy evaluation
```

### ProgressEvidence

Progress is objective-linked evidence, not a tool-name counter. Supported phases:

1. objective understanding;
2. localisation;
3. reproduction;
4. hypothesis evaluation;
5. implementation;
6. verification;
7. delivery;
8. recovery.

A progress score may trigger intervention. It cannot authorise side effects or declare completion.

### FailureEnvelope

Every stopped, failed or blocked run must preserve:

- trigger;
- earliest supported divergence or explicit unknown;
- primary failure family and contributors;
- provider, tool, repository, policy and runtime conditions separately;
- recoverability and considered actions;
- recovery attempts;
- final verdict and residual uncertainty.

Unknown must never default to provider failure.

### Convergence stages

```text
observe -> nudge -> compact -> re-plan -> critic -> recover -> ask -> stop
```

Hard attempt, elapsed-time, monetary and side-effect limits remain mandatory. They are the final circuit breaker, not the first recovery strategy.

### Checkpoint continuation

A recovery checkpoint preserves objective requirements, repository identity, verified facts, relevant files/symbols/tests, hypotheses, current changes, verification state, operations, costs, remaining caps, authority and recovery history.

Continuation must reconcile uncertain external effects and must not repeat completed or paid work.

### Deterministic recovery before learned recovery

Production recovery begins with reviewed, versioned recipes. Learned skills and cost-aware ranking remain local/offline/shadow until independently verified, held-out tested, privacy reviewed, canaried and rollbackable.

## Work breakdown

| Issue | Deliverable |
|---|---|
| #645 | Objective-linked ProgressEvidence |
| #646 | FailureEnvelope and root-cause diagnosis |
| #648 | Unified convergence controller |
| #649 | Semantic repetition detection |
| #650 | Compaction, re-plan and critic intervention |
| #651 | Durable provider-neutral recovery checkpoint |
| #652 | ToolCapabilityRegistry and fallback |
| #653 | Deterministic recovery recipe library |
| #654 | Verified recovery-skill admission |
| #655 | Cost-aware shadow recovery policy |
| #656 | Recovery UX and CLI parity |
| #657 | 240-scenario Self-Healing VestaBench |

## Implementation order

1. Build #645 and #646 in observe-only mode.
2. Add #649 and shadow #648 against current behavior.
3. Enable low-risk nudge/compact/re-plan through #650.
4. Complete #651, #652 and #653 only after durable operation reconciliation prerequisites are available.
5. Integrate #656 and qualify through #657.
6. Introduce #654 and #655 only after deterministic recovery is proven.

## Non-negotiable invariants

- No unsupported provider blame.
- No terminal result contradicts the trigger or diagnosis.
- No progress score authorises work or completion.
- No recovery bypasses authority, privacy, repository, provider, verification or budget controls.
- No non-idempotent operation is repeated without reconciliation.
- No paid attempt disappears from economics.
- No failed, blocked, partial or cancelled run is removed from evaluation.
- No unverified skill controls production.
- No raw private source, prompt, secret or chain of thought is stored in recovery memory.
- GUI, CLI, history, receipt and benchmark use one canonical causal result.

## Required qualification

The programme requires at least 240 uniquely named executable scenarios. Required release gates include:

- at least 95% root-cause-family accuracy on deterministic fixtures;
- at least 85% diagnosis/divergence accuracy on a held-out labelled corpus;
- zero false provider blame in the curated non-provider corpus;
- at least 60% fewer wasted actions before intervention;
- at least 80% fewer unrecovered no-edit hard stops;
- zero duplicate paid or destructive effects under fault injection;
- zero authority, privacy, policy or budget bypasses;
- 100% eligible checkpoint preservation;
- no more than 5% median cost regression where verified completion does not improve;
- defensible improvement in verified outcomes per euro before a production savings claim.

## Pull request requirements

A PR under #569 must state:

- child issue and canonical owner;
- current source-proven defect;
- affected execution paths;
- schemas and compatibility impact;
- safety, privacy, cost and authority boundaries;
- migration/shadow/rollback behavior;
- named tests and benchmark scenarios added;
- exact evidence proving acceptance criteria;
- remaining limitations.

A merged PR is implemented, not automatically qualified or released.
