# Real Agent Operation Activity Design

## Scope

Close #109 by emitting timeline events at the real repository/GitHub operation boundary. The UI already accepts arbitrary `AiActivityEvent` values, so this slice changes backend production and adds end-to-end proof without adding presentation-only states.

## Event lifecycle

Each operation emits a running event and updates that same event ID when it reaches a terminal state. Test execution uses `validation`; PR creation uses `tool_call`; CI polling uses `ci_watch`; merging uses `command_complete`. Titles describe the concrete action and never claim success before the subprocess or CI snapshot proves it.

`vesta.activity.emit_event` remains a thin wrapper over `make_event`. Delivery is best-effort: a closed or broken display callback cannot change the outcome of the underlying operation.

## Operation sources

- `AgentComputerInterface.run_tests` emits running/passed/failed validation evidence and only safe scope/return-code metadata.
- `GitHubAdapter.create_pr` emits PR-opening lifecycle events.
- `GitHubAdapter.pr_checks` emits a truthful CI snapshot.
- `GitHubAdapter.watch_pr_checks` polls to pass/fail/cancel/timeout with injectable clock/sleep controls and one stable timeline row.
- `GitHubAdapter.merge_pr` emits merge lifecycle events only when a merge is actually attempted.

GitHub CLI return codes 1 (failed checks) and 8 (pending checks) are accepted only for `pr checks` when JSON is present. Empty/error output remains a transport failure. Raw test output, PR bodies, credentials, and CLI error text never enter activity metadata.

## Verification

Unit tests cover success, failure, pending-to-pass, timeout, cancellation, callback failure, nonzero GitHub check states, and secret exclusion. Playwright proves running/completed events upsert into four distinct agent-console rows with no duplicate lifecycle entries.
