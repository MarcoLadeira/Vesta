# Agents command center implementation plan

Issue: https://github.com/MarcoLadeira/OPai/issues/821

Goal: supervise one engineering objective as safely isolated, independently routed assignments and produce one verified, cost-accounted outcome.

Architecture: extend the existing SQLite runtime journal with objective and assignment records. A Python service owns planning validation, admission, execution, controls and reconciliation. Desktop and CLI consume this service; JavaScript only renders its snapshots. Reuse worktree leases, provider routing, command cancellation and verification primitives.

## Product contract

- Add an independent **Allow multiple agents mode** switch to the mode picker. Preserve permission mode and selected model. Enabling concurrency grants no additional command, cloud or paid authority.
- Open a dedicated Agents workspace for enabled requests: objective, budget, assignment rows, selected-worker details, changed files, verification, integration queue and result.
- Plans contain bounded objectives, roles, paths, dependencies, capabilities, verification targets, route/model intent, risk and budgets. Validate before dispatch; unknown/overlapping scopes serialize.
- Admission and ownership are transactional. Workers receive isolated leased worktrees, bounded context, distinct task/run/operation identities and no parent transcript.
- Costs include unsuccessful work and expose unknown values explicitly. Objective totals derive from attributable cost evidence.
- Stop one or all through canonical IDs, retain evidence and worktrees, and never report stopped while owned work is still running.
- Reconcile actual changes in an integration worktree, detect conflicts and require integrated verification before completion. Do not mutate the user's checkout during reconciliation.
- Restart reads the journal and reconciles interrupted ownership without replaying provider operations.

## Implementation delivered

- Transactional journal ownership, deterministic plans, dependencies, conservative scope admission, reservations and exact cost evidence.
- Isolated process workers, independent routing, host capacity, cancellation, retained worktrees and integrated verification against the original repository policy.
- Mode-picker toggle, persisted preference, desktop bridge and objective workspace with live revisions, controls, observed routes and integration evidence.
- CLI creation, execution, inspection and controls using the same service, plus the packaged internal worker entry point.
- Deterministic benchmark harness for all six issue workload classes. It measures orchestration only; it does not establish provider quality or competitive speed.

## Remaining qualification and recovery boundary

Current issue-by-issue coverage and remaining product work: [2026-09-08 assessment](2026-09-08-agents-epic-readiness.md).

Testing resumed at the user's subsequent request on 2026-09-08. Local runtime, desktop, packaging-contract and synthetic benchmark evidence is recorded in the assessment above. Provider-backed benchmarks, native packaged execution, remaining product controls and supervisor-loss recovery still block closure. Keep PR #842 draft and #821 open.

Expired owners are fenced and surfaced as needing attention. A returning original owner can acknowledge termination against the interrupted fence, retain its evidence and release capacity without claiming success. If the owner never returns, capacity remains reserved; automatic orphan release and provider-operation replay are unavailable. Recovery after complete supervisor loss still needs a proven-termination path before that part of the epic is complete.

## Validation and delivery constraints

Python >=3.10; current Qt/web stack; no new cloud dependency. Use deterministic tests and no paid/provider calls without explicit authority. Preserve existing unrelated edits in the original checkout. Commit and push coherent increments to the same draft PR. Do not close the epic or claim provider-benchmark qualification without evidence.
