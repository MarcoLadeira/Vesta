# #616 Completion Plan — Canonical Exact-Once External Operation Protocol

Branch: `kimi/issue-616-exact-once-completion`
Base: `main` @ 792970d (includes PRs #665, #693, #715, #719, #721)

## Approach

1. **Measure before changing.** Inventory every external side-effect path
   (provider, tool/command, filesystem, Git, GitHub, cost) against the #616
   operation-protocol checklist. Extend the existing canonical primitives
   (`opaihub/operation_class.py`, `opaihub/idempotency.py`,
   `opaihub/provider_invocation.py`, `opaihub/call_reconciliation.py`,
   `opaihub/run_journal.py`) — do not build a parallel framework.
2. **Close the real gaps in small green slices**, committing and pushing each
   slice to the PR.
3. **Verify** with contract, concurrency, boundary fault-injection, and
   restart tests; run required merge gates before flipping the PR to
   `Closes #616`.

## Already present on main (verified during inventory; to be documented in PR)

- Paid-provider exact-once: single-dispatch, durable operation claims,
  paid-call start recording, usage observation, cost reconciliation
  (PRs #693, #719).
- Fail-closed operation classes and retry classification (PR #665,
  `opaihub/operation_class.py`).
- Idempotency machinery for Git/GitHub operations
  (`opaihub/idempotency.py`, `tests/test_git_commit_idempotency.py`).
- Run journal / shadow-journal durable state (#613 stages, PR #721).

## Suspected remaining gaps (to confirm via runtime call-graph inventory)

- Tool/command, filesystem, Git and GitHub mutation paths may not be fully
  wired into the durable operation lifecycle (intent-before-dispatch, stable
  identity, restart reconciliation).
- Static/architecture tests preventing direct protected side effects outside
  registered adapters (AC: "direct side-effect calls ... blocked").
- Bounded restart reconciliation across all protected adapters.

## Scope guardrails

- Do not duplicate #613 (journal), #619 (cost outbox), #620 (change
  attribution), or PR #715's ChangeSet architecture.
- No GUI redesign, no provider-path rewrite, no unrelated refactors.
