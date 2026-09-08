# Epic #821 implementation assessment

Scope: source review and local qualification against the complete issue on 2026-09-08. Testing resumed at the user's request. No provider calls were made.

Overall judgment: approximately 80% toward epic closure after local qualification, with roughly 75% of the intended product implementation present. These are engineering estimates, not measured completion scores or release approval. The PR remains draft.

## Local qualification

The following runs overlap; their counts must not be added as unique tests.

| Run | Result |
| --- | --- |
| Initial focused objective/store/execution/CLI/bootstrap suites | 97 passed; one outdated fixture lacked the required ownership fence. Fixture corrected and its integration suite rerun successfully. |
| Broader receipt, budget, journal, GUI-mode, ownership and integration regressions | 193 passed, 1 skipped. |
| Desktop/CLI/bootstrap and additional qualification | 105 passed; the real-worker case exposed the shutdown defect described below. It passed in the final runtime rerun after the fix. |
| Final objective execution, integration, qualification, capacity and orphan-process suites | 36 passed, 1 POSIX-only test skipped on Windows, 5 subtests passed. |
| Packaging, asset integrity, update packaging and release identity surfaces | 30 passed. These are contract tests, not a native executable rehearsal. |
| Agents/composer Chromium suite | 27 passed. |
| Expanded Agents Chromium suite | 8 passed, including two new live-revision/artifact-target regressions; 29 distinct browser tests across both runs. |
| Full JavaScript unit suite | 122 passed, 3 existing chat-components failures. The failing test and implementation files are unchanged from PR base `3d5c90a`. |
| Scoped Ruff lint/format, Bandit, design tokens and lifecycle generation | Passed; two design-token tests passed. |
| Six synthetic workloads, sequential and concurrent | All 12 integrated successfully; zero conflicts and no intervention, with complete synthetic zero-cost evidence. |

The real subprocess test found that the parent watcher blocked on Python's buffered stdin, causing an interpreter-shutdown abort after a successful response. The watcher now uses the pipe's raw file descriptor. The regression exercises canonical launch authorization, a real leased Git worktree, the worker entry point, response evidence and worktree lookup with an explicitly stubbed provider.

Additional regressions cover exact receipt reconciliation and tamper detection, interrupted-owner acknowledgement, sequential eligibility, invalid launch authority, bounded dependency reports, incremental ledger records and cross-objective scheduling.

Synthetic benchmark results are in `.opaihub/qualification/agents-benchmark-current.json` locally. These runs used deterministic workers and overlapped other local checks; timing is diagnostic, not a controlled provider-performance comparison. Hosted PR checks were not reported when inspected. The full Python repository suite and native packaged executable have not been qualified in this pass.

## Acceptance coverage

| Epic criterion | Implementation assessment |
| --- | --- |
| High-level objective creates bounded assignments | Implemented: deterministic plan validation and canonical objective/assignment records. Invalid planner contracts now finalize as needing attention. |
| Concurrent isolation; overlapping/unknown scopes serialize | Implemented: worktree leases, transactional admission, project/host caps and explicit sequential eligibility. Cross-objective capacity waits now keep scheduling. |
| Canonical owner, route, model, budget, activity, changes, verification | Partial: fields and observed routes exist; capabilities, resource/plan quotas and Verified Outcome Router policy need end-to-end qualification and completion. |
| Separate bounded parent/child context | Implemented: isolated context plus bounded dependency findings, treated as untrusted reports. |
| Independent failures preserve sibling work | Implemented for ordinary worker failures; supervisor, storage and termination failures still need qualification. |
| Exact per-agent and objective costs | Implemented: canonical event accounting, unknown-cost coverage, receipts and incremental ledger reads. |
| Stop one/all without orphan processes | Partial: live process-tree cancellation exists; complete supervisor loss is still an unresolved recovery boundary. |
| File/content conflicts detected before integration | Implemented: observed Git evidence, sibling/user-change checks and isolated integration. Semantic conflicts rely on integrated verification/review. |
| Completion requires integrated verification | Implemented: policy from original repository, persisted manifest and matching integration commit. |
| GUI restart reconstructs canonical state | Partial: reconstruction and queued-work controls exist; lost-owner capacity recovery remains incomplete. |
| Later CLI inspection/control uses same runtime | Implemented, including objective creation, execution, controls and receipt export. |

“Implemented” records code presence and design coverage, not acceptance-test success.

## Remaining product work

1. Establish process-tree termination after complete supervisor loss before releasing ownership/capacity. Never infer termination from a missing parent alone.
2. Finish the canonical blocked-operation approval flow, request-another-review flow and direct diff/PR inspection from the Agents workspace. Worktree opening is available.
3. Complete and qualify capability-aware routing and resource/provider-plan quota behavior under parallel load. An explicitly selected model must retain its authority boundary.
4. Qualify packaging, restart, cancellation, concurrency, accounting and failure paths; then run provider-backed sequential/concurrent comparisons for all six required workload classes. The synthetic harness does not establish model quality or competitive speed.

## Improvements in this pass

- Validate planner contracts while planning errors can still release ownership safely.
- Continue scheduling objectives waiting behind another objective.
- Preserve rationale, cost estimates and explicit sequential eligibility in the planning contract.
- Carry bounded predecessor findings into dependent assignments without granting them authority.
- Project objective and per-agent receipts from journal evidence; support UI copying and CLI export with optional local signing.
- Show queue reasons, an enabled-state indicator, queued-work execution and canonical worktree-opening controls.
- Surface executor failures after the initial objective acknowledgement and bound international-text worker packets by encoded size.

## Activation

Use a build containing PR #842. Open the composer's **Mode** picker (the button showing Auto, Manual or the current permission mode), enable **Allow multiple agents mode**, then send an objective. The mode button includes **Agents** while enabled. The toggle persists; permission mode and model selection remain separate. Existing objectives are managed from the Agents workspace.
