# Epic #821 implementation assessment

Scope: source review against the complete issue on 2026-09-08. No tests, lint, benchmarks or provider calls were run during this pass. Current-head behavior remains unverified.

Overall judgment: roughly 75% of the intended implementation. This is an engineering estimate, not a measured completion score. The PR remains draft.

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
