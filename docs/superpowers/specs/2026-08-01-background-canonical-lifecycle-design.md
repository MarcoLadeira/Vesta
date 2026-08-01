# Background runs: canonical lifecycle adoption

Issue #379 requires every public execution path to expose one canonical run
lifecycle.  Background automations were an exception: their durable records
used a small, independent `status` vocabulary, collapsed `partial` and
`timeout` into `failed`, and recorded no terminal reason or transition history.

## Scope

This slice makes `AutomationRun.run_state` the canonical lifecycle field and
keeps `status` as a compatibility projection for existing automation callers.
Each run persists a bounded canonical `state_history`, a typed `reason_code`,
and timestamps already supplied by the record.  Notifications and CLI list/run
output contain those same canonical facts because they serialize the durable
record rather than derive a local terminal meaning.

## State mapping

| Canonical state | Compatibility status | Reason example |
| --- | --- | --- |
| `queued` | `queued` | `queued` |
| `preparing` | `queued` | `preparing_execution` |
| `running` | `running` | `execution_started` |
| `cancel_requested` | `running` | `cancellation_requested` |
| `completed` | `completed` | `background_completed` |
| `partial` | `partial` | `background_partial` |
| `blocked` | `blocked` | `approval_required` |
| `failed` | `failed` | `executor_error` |
| `cancelled` | `cancelled` | `cancelled_by_user` |
| `timeout` | `timeout` | `background_timeout` |

`interrupted` remains readable as a legacy compatibility status, but its
canonical terminal state is `failed` with `reason_code=interrupted`.  Old JSON
records without `run_state` are derived from their stored legacy status once;
new records always write the canonical fields.

## Transition and stale-worker rules

The execution path emits `queued -> preparing -> running -> terminal` through
the shared `RunState` transition table.  A cancellation request moves an active
run to `cancel_requested`, and only a terminal state may follow.  Before a
worker starts, it reloads its durable run: if cancellation already made it
terminal, the stale queued object is ignored and the executor is never called.
Finalization also reloads and leaves a terminal record unchanged.  This is the
semantic guard for in-process stale events; #439 owns the later interprocess
locking and atomic-persistence hardening.

## Verification

Focused tests cover canonical lifecycle history, preservation of `partial` and
`timeout`, cancellation before a stale worker begins, recovery of an interrupted
run, legacy-record migration, and notification/CLI projections.  Existing
background automation coverage remains the compatibility regression suite.
