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

There is still no heartbeat. `heartbeat_at` is stamped at acquisition and at
release and by nothing in between, so it cannot yet distinguish a process that
is alive but wedged from one that is alive and working.

## Found while measuring, not fixed here

This checkout's journal holds **four unreconciled `model_call_paid` operations**
from 2026-09-01. Paid calls that were begun and never reconciled, which is the
territory of the epic's *"unknown cost is never represented as zero"*. Recorded
here rather than acted on: it is a different acceptance criterion and deserves
its own reproduction.

`opaihub/attachments.py` had been writing durably since 2026-08-30 without a
migration owner, so Stage 1's ratchet was red on `main`. Triaged as
`NOT_RUNTIME_STATE` in this branch.

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
