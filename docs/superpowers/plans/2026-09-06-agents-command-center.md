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

## Delivery sequence

- [ ] Journal contracts and deterministic plan validation, with dependency, scope, budget and transactional-admission tests.
- [ ] Isolated execution, independent routing, bounded context, cancellation, crash recovery, costs and integration verification, tested with deterministic local workers and real temporary Git repositories.
- [ ] Picker switch, desktop bridge and Agents workspace, with state rehydration and controls tests.
- [ ] CLI inspection/control of the same canonical service, without a second state machine.
- [ ] Sequential versus concurrent benchmark for six issue-required workload classes; record time, cost, conflicts, verification and intervention. Clearly distinguish deterministic qualification from provider-backed outcome evidence.
- [ ] Independent review, targeted regressions, Python/web required checks and updated PR evidence.

## Validation and delivery constraints

Python >=3.10; current Qt/web stack; no new cloud dependency. Use deterministic tests and no paid/provider calls without explicit authority. Preserve existing unrelated edits in the original checkout. Commit and push coherent increments to the same draft PR. Do not close the epic or claim provider-benchmark qualification without evidence.
