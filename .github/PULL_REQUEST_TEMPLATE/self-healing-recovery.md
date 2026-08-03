# Self-Healing Convergence & Recovery PR

## Canonical ownership

- Parent epic: #569
- Child issue:
- Canonical runtime/data owner:
- Presentation consumer, if any:

Confirm this PR does not create a duplicate runtime, router, event vocabulary, ledger, verifier or UI-side source of truth.

## Source-proven defect

Describe the exact current behavior, affected execution paths and evidence. Separate:

- guard/trigger;
- root cause or explicit unknown;
- provider/tool/repository/runtime condition;
- terminal outcome.

## Change summary

Explain the smallest cohesive implementation and why it belongs in this owner.

## Canonical contracts

- Inputs and IDs:
- Outputs/events/projections:
- Schema/version changes:
- Persistence/replay impact:
- RunResult/receipt impact:
- GUI/CLI impact:

## Recovery and side-effect safety

- Checkpoint behavior:
- Operation reconciliation:
- Retry/continue/re-plan semantics:
- Cancellation/restart behavior:
- Duplicate-effect prevention:

## Authority, privacy and cost

- Approval/policy changes:
- Redaction/retention changes:
- Repository/worktree impact:
- Provider/tool capability impact:
- Cost measurement and hard caps:

## Migration and rollout

- Legacy path inventory:
- Shadow comparison:
- Canary boundary:
- Rollback:
- Legacy deletion criteria:

## Tests

List uniquely named executable scenarios, not only assertions.

- Unit/schema/property:
- Integration:
- Crash/fault/concurrency:
- Provider/tool compatibility:
- GUI/CLI/accessibility:
- Security/privacy/poisoning:
- Benchmark fixtures added to #657:

## Evidence

Attach exact commands, fixture IDs, reports, screenshots, performance numbers and artifact/commit versions.

## Acceptance checklist

- [ ] Unknown failure does not default to provider blame.
- [ ] Trigger, causal diagnosis and terminal verdict remain distinct.
- [ ] Progress evidence cannot authorise side effects or completion.
- [ ] Recovery cannot bypass authority, privacy, repository, provider, verification or budget controls.
- [ ] Uncertain non-idempotent effects are reconciled before retry.
- [ ] Failed, blocked, partial and cancelled attempts remain costed and evaluated.
- [ ] GUI, CLI, history, receipt and benchmark consume the same canonical truth.
- [ ] Rollback to the previous deterministic behavior is proven.
- [ ] Remaining limitations and non-goals are explicit.

## Definition of Done

State the evidence that proves this child issue is implemented and qualified. A merged PR alone is not release qualification.
