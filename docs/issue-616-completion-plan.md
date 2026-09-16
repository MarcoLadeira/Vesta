# #616 Completion Plan — Canonical Exact-Once External Operation Protocol

Branch: `kimi/issue-616-exact-once-completion`
Base: `main` @ 792970d (includes PRs #665, #693, #715, #719, #721)

## Approach

1. **Measure before changing.** Inventory every external side-effect path
   (provider, tool/command, filesystem, Git, GitHub, cost) against the #616
   operation-protocol checklist. Extend the existing canonical primitives
   (`vestahub/operation_class.py`, `vestahub/idempotency.py`,
   `vestahub/provider_invocation.py`, `vestahub/call_reconciliation.py`,
   `vestahub/run_journal.py`) — do not build a parallel framework.
2. **Close the real gaps in small green slices**, committing and pushing each
   slice to the PR.
3. **Verify** with contract, concurrency, boundary fault-injection, and
   restart tests; run required merge gates before flipping the PR to
   `Closes #616`.

## Final adapter inventory (verified by call-graph walk + static test)

| Path | Operation key | Reconciler | Fault behaviour |
|---|---|---|---|
| Paid provider dispatch | native/idempotency (main, #693/#719) | usage/cost reconciliation (main) | single dispatch, fail-closed classes (#665) |
| Cost recording | one event per operation (main, #381) | late-usage reconcile (main) | no double charge |
| `git_commit` | repo, paths, message, tree | — (fail closed) | `COMMIT_STATE_UNCERTAIN` |
| `git_push` | repo, branch, resolved head | `ls-remote` ref observation | `PUSH_STATE_UNCERTAIN` |
| `git_create_branch` | repo, name | — (fail closed) | `BRANCH_STATE_UNCERTAIN` |
| `open_pr` | repo, head, base, title | `find_pull_request` head/base | `PR_STATE_UNCERTAIN` |
| `github_comment` | repo, number, body | `find_comment` exact body | `COMMENT_STATE_UNCERTAIN` |
| `github_request_review` | repo, number, sorted reviewers | `find_requested_reviewers` | `REVIEW_REQUEST_UNCERTAIN` |
| `merge_pr` | repo, PR, method | `pr view --json state` | uncertain RuntimeError |
| `comment_pr` | repo, PR, body | `pr view --json comments` | uncertain RuntimeError |
| `update_pr` | repo, PR, title, body | `pr view --json title,body` | uncertain RuntimeError |
| `run_command` granted | repo, exact argv | — (effects not observable) | `COMMAND_STATE_UNCERTAIN` |
| `write_file` | repo, path, content sha256 | on-disk content hash | `WRITE_STATE_UNCERTAIN` |
| `apply_patch` | repo, patch sha256 | forward/reverse `git apply --check` | `PATCH_STATE_UNCERTAIN` |

Read-only tools (`run_command` allowlisted git reads, GitHub reads) are
class 1 — safely repeatable, no claim required. `pr edit` is guarded as
set-state. Worktree leases and parallel-agent worktrees carry their own
durable lease protocol (#613 shadow-journal stages).

## Evidence

- `tests/test_side_effect_boundary.py` — AC #10 static enforcement: every
  outward-mutation sink in the dispatch layer is inside a claimed operation.
- `tests/test_operation_fault_matrix.py` — crash-after-success at every
  external boundary with fresh-executor restarts; external-effect counters
  prove zero duplicates. 10,000 raced claim sequences; 16-thread adapter
  race dispatches once.
- `tests/test_idempotency.py` — per-adapter lifecycle, reconciliation and
  fail-closed contracts.

## Scope guardrails

- Did not duplicate #613 (journal), #619 (cost outbox), #620 (change
  attribution), or PR #715's ChangeSet architecture.
- No GUI redesign, no provider-path rewrite, no unrelated refactors.
