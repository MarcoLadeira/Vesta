# One runtime truth (issue #818)

> One request → one task identity → one ordered event history → one
> authoritative state → one evidence-backed terminal result.

`#613` built the kernel: nine `opaihub/journal_*.py` modules, a SQLite store,
lifecycle adapter, comparator, canonical reader, operation idempotency, a
retirement gate and a CLI. What it did **not** do is make anything depend on
it. The journal is a faithful mirror running beside the authorities it was
meant to replace, which means every fact OPai reports still has at least two
sources that can disagree.

This document is the working record for closing that gap. It is written as
work lands, not in advance: each section states what was measured, not what
is intended.

## Method

Every claim here is either a file/line reference or a reproduction. A section
with neither is a plan, and is labelled as one.


## Inventory: where the authority actually sits

Measured on `main` at `287dbfb`, by reading the code rather than the design.

### 1. The canonical read path is imported by nothing

`opaihub/journal_reader.py` is Stage 5 -- "GUI, CLI and receipts stop reading
files and start reading journal projections". Across `opaihub/` and `opai/`,
its only importer is `opaihub/journal_retirement.py`, which is itself reached
only by `opai/cli.py`'s doctor command. No GUI read path, no CLI read path and
no receipt reaches it.

So the epic's *"GUI, CLI and background projections are generated from the same
canonical state"* is not partly true. It is not true at all: the machinery to
make it true exists and is wired to nothing.

### 2. The canonical lease cannot name its owner

`opaihub/journal_runtime.py:260`:

```python
fence = acquire_lease(store, run_id=run_id, owner=surface, now=now)
```

`surface` is a category -- `"gui"`, `"cli"` -- not an identity. Every run
admitted by every OPai process on the machine records the same owner.

`heartbeat_at` is worse than coarse; it is inert. Its only writers are
`acquire_lease` (`journal_store.py:798`) and `release_lease`
(`journal_store.py:821`). Nothing restamps it while a run is in progress, so
its value is always the moment the run started.

`unterminated_runs` (`journal_runtime.py:657`) is explicit about needing what
neither field provides:

> each row carries the owner and the heartbeat and lets the caller decide,
> because the caller can look at whether that process still exists and this
> module cannot.

The caller cannot. Reproduced, admitting two runs as two different processes
would:

```
run_id     lease_owner  lease_held  lease_heartbeat_at
r-alive    gui          True        2026-09-07T10:00:00+00:00
r-dead     gui          True        2026-09-07T10:00:01+00:00

distinct owners recorded: {'gui'}
can a caller name the process that holds either lease? False
```

A run being tended right now and a run whose process was killed are the same
row. This is the epic's *"`cancelled` is impossible while owned controllable
work is still alive"* and *"restart rehydrates one honest actionable state"*,
and both currently rest on fields that cannot carry the answer.

### 3. Two lease authorities, and the legacy one is the capable one

| | `opaihub/owner_lease.py` | `journal_store.leases` |
| --- | --- | --- |
| substrate | JSON file per resource | the canonical journal |
| owner identity | pid + per-process boot id | a surface string |
| heartbeat | restamped by `renew()` | stamped once, at acquisition |
| liveness verdict | `describe()`, closed vocabulary | none available |
| fencing token | yes | yes |

The kernel #818 wants to be authoritative is the one that cannot answer "is
this still running?". The file it is meant to replace can.

`owner_lease.py` also states the rule the journal has to inherit: liveness is
decided by heartbeat, never by pid, because operating systems reuse pids. The
pid and boot id are still worth recording, for the narrower job they do
honestly -- recognising this process's own work.

### What this PR takes

Item 2, and only item 2. It is the smallest change that turns an acceptance
criterion from unprovable into provable, and items 1 and 3 both depend on it:
a canonical read path is not worth switching to while the canonical record
cannot say whether the work it describes is still alive.


## Shipped: a lease that names its owner

Schema v2 adds `owner_pid` and `owner_boot` to `leases`. `journal_liveness`
turns them into a verdict from a closed vocabulary:

| verdict | means | evidence |
| --- | --- | --- |
| `owned_here` | this OPai is working on it | pid **and** boot id are ours |
| `owner_gone` | the owner is not running | the pid is not in the process table |
| `owner_unverified` | something with that pid exists | a pid, and pids get reused |
| `unknown` | nobody can say | no pid recorded, or the platform declined |

The asymmetry is the design. Pid reuse can make a dead process look alive; it
cannot make a live one look dead. So `owner_gone` is safe to state and
"running" is not, which is why there is no `owner_alive`.

`owner_unverified` is deliberately excluded from `ACTIONABLE`. Acting on it
would mean cancelling or reclaiming work another OPai is doing.

### Evidence

Against real processes, not mocks -- a child admits a run and is killed with
`os._exit(9)`:

```
r-alive    owner=gui   pid=24516    -> owned_here
r-dead     owner=gui   pid=22652    -> owner_gone
```

Against this checkout's own live journal, migrated on a copy:

```
live journal, before: {tasks: 27, runs: 27, events: 76, operations: 46,
                       cost_events: 21, leases: 27}
after migration:      {tasks: 27, runs: 27, events: 76, operations: 46,
                       cost_events: 21, leases: 27}
schema_version: 2      integrity: complete      rows lost: none
```

Every pre-existing lease reads `unknown`, never `abandoned`. A migration that
made historical runs look recoverable would greet a user with a pile of
imaginary work, and there is a test pinning that it does not.

### What is deliberately still missing

`owner_unverified` can be collapsed to a definite answer by recording the
owning process's **creation time** alongside its pid: a pid whose start time
differs from the recorded one is conclusively a reused pid. That needs a
per-platform probe (`GetProcessTimes`, `/proc/<pid>/stat`, `sysctl`) and is
worth doing separately rather than smuggling into this change.

~~There is still no heartbeat.~~ Since fixed, and the correction is left
visible because the reasoning changed with it. `heartbeat_at` had two writers,
acquire and release, so it recorded when a run *started* and nothing else. A
reader could tell a lease was held and never whether anyone was still holding
it.

A running turn now restamps it, from the activity stream rather than from a
timer. That distinction is the point: a timer would keep a wedged run's lease
warm and report it healthy forever, hiding the exact thing a heartbeat exists
to expose. If events are flowing, work is happening.

That adds a fifth verdict, `owner_stale` -- the owner is still there and has
stopped tending this. Two rules keep it honest:

* **Only a heartbeat that stopped counts.** It must have moved since
  acquisition. A heartbeat that never moved proves nothing, because background
  and CLI runs do not beat at all, and judging them by a clock they never wound
  would report every one of them as dead.
* **Stale is reported, never acted on.** A quiet process may be wedged,
  suspended, or in a long provider call that emits nothing. `owner_stale` is
  outside `ACTIONABLE` and counts as possibly-alive, so recovery still refuses
  to write a terminal verdict over it.

The staleness window and the beat interval are `owner_lease`'s own constants
rather than new ones. Two definitions of "stale" in one codebase is the second
opinion this epic exists to remove.

## Found while measuring, not fixed here

This checkout's journal holds **four unreconciled `model_call_paid` operations**
from 2026-09-01. Paid calls that were begun and never reconciled, which is the
territory of the epic's *"unknown cost is never represented as zero"*. Recorded
here rather than acted on: it is a different acceptance criterion and deserves
its own reproduction.

`opaihub/attachments.py` had been writing durably since 2026-08-30 without a
migration owner, so Stage 1's ratchet was red on `main`. Triaged as
`NOT_RUNTIME_STATE` in this branch.


## The pattern, stated once

Three separate criteria in #818 turned out to be the same defect wearing
different clothes. In each, a surface answered a question it had no evidence
for, in the confident direction:

| criterion | the question | the answer it gave with no evidence |
| --- | --- | --- |
| owned work is still alive | who holds this run? | `"gui"` -- a category, for every process |
| terminal states are earned | did this run die? | "the owning session ended" (it had not) |
| unknown cost is not zero | what did today cost? | `$0.00` |
| approvals are consumed once | may I run this? | yes, to all eight racers |

None of these were reported as unknowns. Each was a plausible answer with
nothing behind it, which is the failure the epic names in its own words.

## Also measured, not fixed here

### The `approvals` table has no writer

`journal_store` defines it well -- fingerprint, `run_id`, `operation_key`,
nonce, `expires_at`, `consumed_at`, `revoked_at`. Exactly the shape #818 asks
for: "operation-bound, run-bound, expiring and atomically consumed".

Nothing writes to it. Across the whole repository the only code that touches
`approvals` is `journal_backup` (which backs it up and restores it) and its
tests. The real approval authority is `opaihub/command_consent.py`, a
file-based handshake in a shared temp directory.

That makes three modules now where the kernel has the machinery and nothing
uses it -- `journal_reader`, the lease identity columns before this PR, and
this. The pattern is worth naming: #613 built a kernel and then did not make
anything depend on it, so its correctness has never been load-bearing.

### An approval is not bound to the work it was given for

`consent_dir()` is a fixed per-user temp location shared by every OPai process
on the machine, and the grant record is `{"command": ...}`. No run, no
operation, no workspace. Reproduced:

```
window A: user approved 'git push' for their private repo
window B: consume_grant('git push') -> True
*** window B pushed on an approval the user gave to window A ***
```

The atomic-consumption half is fixed in this PR. This half is not. The fix is
to record the workspace on the grant and require the consumer to match it,
which means the pipeline and a provider CLI's hook subprocess agreeing on a
normalised root across a process boundary. If they disagree, the approval card
silently stops working -- so this needs the real hook path exercised
end-to-end, which is its own change.

### Cost has two operation identities, and the priced one is not the paid one

In this checkout's journal:

```
model.call         observed     21     <- carries every cost event
model_call_paid    executing     4     <- carries none
model_call_paid    reconciled   21     <- carries none
```

All 25 `model_call_paid` operations have no cost event. The 21 cost rows are
keyed `<digest>:account` against the `model.call` operations. Two operation
identities for the same underlying call, and the money is attached to only one
of them -- so `cost_events` joined to the operations that represent *paid work*
yields nothing.

Every one of the 21 is `measurement_kind = "estimated"`. There is not a single
`actual`. The schema supports `actual`, `derived` and `unavailable`; nothing
writes them.

Four `model_call_paid` operations have been sitting in `executing` since
2026-09-01 -- claimed, never reconciled.


## The migration cannot progress, and the reason is an identity mismatch

The most important thing measured in this work, and the one that changes what
#818 should do next.

Stage 7's gate reports `blocked` on `nothing_compared`, explained as "too few
runs exist in both records". Against this checkout's real journal:

```
journal runs by origin surface:   gui  27
legacy corpus (background runs):  0     (the directory does not exist)
runs in both:                     0
```

`journal_background.legacy_runs` is the only legacy corpus OPai assembles, and
it reads `.opaihub/agent/background/runs/`. This installation has never run
`opai automation`, so that directory has never existed. Meanwhile every run in
the journal came from the GUI.

**The comparator is comparing an empty set against 27 runs it can never
validate.** Not a shortage of data: a population mismatch. On any installation
that does not use background automation -- the normal desktop case -- Stages 4,
5 and 7 are unreachable by construction, and therefore so is AC9 ("legacy
runtime files are migration inputs/projections only").

#613's own closing note recorded the same shape of problem, and pointed at
`background_runs` as the fix because it is "durable, enumerable, and keyed by
run id". It is. It is also empty.

### Why the GUI population has no counterpart

`gui_pipeline` admits each turn with `task_id=runtime.task_id`,
`run_id=turn_id`. `gui_recents` archives conversations under a separate
`conversation_id`, and says why it has to:

> Identifies the *conversation* this thread is, stable across its turns and
> replaced when a new chat starts. `task_id` cannot do this job: it falls back
> to the per-turn request id.

Measured: **13 conversations, 27 journal tasks, zero shared identifiers.**
`tasks.origin_session` -- the column that could hold the cross-reference -- is
`''` for all 27 rows, because `record_admission` is never passed a session.

### What that does to the canonical record

```
runs per task:   1 run(s): 27 task(s)
attempt numbers: [1]
```

Twenty-seven tasks, twenty-seven runs, every attempt 1. The epic's required
architecture asks a canonical record to reconstruct "run/attempt lineage";
there is none, because a fresh task identity is minted per turn. `_admit_on`
carefully derives `attempt` as `MAX(attempt) + 1` per task -- machinery that
cannot fire, because no two runs ever share a task.

And the product principle is inverted. "One request → one task identity" is
satisfied trivially; what actually happens is one *conversation* producing N
task identities.

### The decision this leaves

Making the journal's task identity the conversation would give a multi-turn
chat one task and N runs, make `attempt` meaningful, and give the GUI
population a legacy counterpart -- unblocking the whole migration. It also
redefines what `attempt` means: turns in a conversation are not retries of one
objective, and calling them attempts is a semantic choice, not a refactor.

That is a maintainer's decision rather than an agent's, so this branch does not
take it. What it does instead is stop the gate from misdescribing the
situation: `RetirementReport` now carries `populations`, and an empty corpus
beside a populated journal is reported as "different populations ... cannot be
satisfied by waiting" rather than as a shortage of runs.

## A defect this PR introduced, and how it was caught

The spend-completeness change first shipped using `spend_completeness.complete`
to qualify the header's "today" figure. Opening the running app showed:

```
Codex · Auto · at least $0.00 today · $0.00 saved
```

on a day with no model calls at all -- because `complete` is all-time and four
operations from 2026-09-01 were still open. A hedge that can never clear is one
nobody reads, which is the same conclusion `budget.py` had already reached for
the budget *gate*: "a permanent prompt is not a safety feature -- it trains
people to click through."

`budget_status` now publishes `complete_today` and the surfaces use it. Worth
recording because no test would have found it: every test of that surface was
correct, and the payload it was given was correct. Only the running application
had the combination of facts that made it wrong.

## Status

| Migration step (per #818) | State |
| --- | --- |
| 1. Inventory every authoritative writer/reader | measured, above |
| 2. Parity assertions, legacy vs canonical | not started |
| 3. Cut over one local-provider path | not started |
| 4. Cut over one account-provider path | not started |
| 5. Cut over cancellation, verification, cost, delivery | not started |
| 6. Switch GUI/CLI/background readers to canonical projections | not started |
| 7. Migrate persisted state | not started |
| 8. Delete legacy authoritative writes | not started (user's call) |
