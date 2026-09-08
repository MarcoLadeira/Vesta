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
| evidence-backed delivery | can I push? | "Ready to push & open PRs", from `bool(token)` |
| approvals bind to a run | is this approval mine? | yes, to a different window's run |
| the install works | is OPai healthy? | `ready`, with a dead desktop icon |
| one canonical origin | which surface asked? | `"gui"`, for CLI and background too |
| a claim is exact-once | did I just create this? | yes, on every retry |

None of these were reported as unknowns. Each was a plausible answer with
nothing behind it, which is the failure the epic names in its own words.

The last four were found after the first four, by looking for the same shape
somewhere else. That turned out to be a reliable way to find real defects:
every place OPai returns a value that *could* be "I do not know" is a place
worth checking, because the confident answer is usually still there.

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

~~Every one of the 21 is `measurement_kind = "estimated"`.~~ Since fixed, and
the cause was one line. `CostTelemetry` already reports how a number was
arrived at -- `normalize_account_result` returns `actual` when a provider gave
a real dollar figure (Claude does) and `estimated` when it did not (Codex) --
and the mirror hard-coded `estimated` over the top.

The zero was the worse half. `cost_usd` is deliberately `None` for a call
whose price nobody measured, and `float(... or 0.0)` turned that into a
recorded **$0.00**. "Unknown cost is never represented as zero", inside the
store the epic wants to make authoritative. The `unavailable` kind existed
from v1 and had no writer. Now:

```
operation         amount  measurement_kind
r1:account        0.0421  actual         <- Claude reported dollars
r1:codex             0.0  unavailable    <- Codex reported none
```

A provider that reports exactly $0.00 still records `actual`. Measuring zero
is a measurement; only absence is unknown.

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


## An operation could go backwards, and that is why the two identities stay

The two operation identities above are the same call recorded twice: the
idempotency layer claims `model_call_paid:<digest>`, and the cost mirror claims
`<turn>:account`. #818 asks for one operation identity per external effect, so
unifying them is the obvious next step.

Trying it surfaced why it is not a one-line change. `record_run_cost` claims
its operation as `observed`, and `record_operation` set `state`
**unconditionally** -- so pointing the cost at the idempotency key would have
set an already-`reconciled` push back to in-flight. For a table whose entire
job is idempotency for pushes, PRs, commits and paid calls, that is the wrong
direction to be silent in.

The `runs` table has forbidden terminal regression since #613. `operations` had
no equivalent, and nothing had hit it only because the two writers use disjoint
key schemes -- luck, not design, and exactly the luck that runs out when the
keys are unified.

That guard now exists, so the unification has somewhere safe to land. It is
still not done here: it means changing which key the mirror uses across two
layers, and the guard is the prerequisite rather than the change itself.

`uncertain` is deliberately off the ladder -- an outcome that becomes
unknowable after it was reconciled is a real thing to record -- and so is any
state a newer OPai wrote that this build cannot rank, because refusing that
would turn a forwards-compatibility problem into a hard failure.


## The install itself was the loudest instance

Reported as "the app is giving me some error message". It was worse than an
error message: the desktop icon exited 1 with no window, no dialog, no log
line and nothing on stderr.

OPai's updater reinstalls OPai with `sys.executable -m pip install -e .`.
Inside the desktop app that interpreter is `pythonw.exe`, and pip's vendored
distlib builds a `gui_scripts` launcher by substring substitution --
`fn.replace("python", "pythonw")` -- so `pythonw.exe` became `pythonww.exe`,
which is not a file. **Updating OPai from inside OPai killed the way the user
opens OPai.** The console scripts were damaged more quietly by the same
install: they inherited `pythonw.exe`, where `sys.stdout` is `None`, so
`opai --version` in a terminal printed nothing.

Measured on this machine, before any change:

```
OPai-Desktop.exe  MISSING  C:\Python313\pythonww.exe
opai-gui.exe      MISSING  C:\Python313\pythonww.exe
opai.exe          OK       C:\Python313\pythonw.exe
```

And `opai doctor` said `readiness: ready`, because every other component was
genuinely clean and nothing had ever read the launchers. `opaihub.proc.console_interpreter`
fixes the cause; `opaihub.launcher_health` makes the question answerable, and
an unreadable launcher reports `unreadable` rather than healthy.

## Two identities that were never recorded

**Which surface asked.** `handle_gui_message` is the one turn pipeline, and
five things call it: the QtWebEngine desktop, the classic desktop host,
`opai ask`/`opai route`, background automations and `opai build`. Admission
recorded `surface="gui"` as a literal, so all five were filed as desktop runs.
`journal_retirement` already groups runs by `origin_surface` to report
populations -- that report could only ever have had one row. AC2 asks for GUI,
CLI and background projections built from one canonical state, and the state
could not tell them apart.

The default now refuses to guess: a caller that does not say is recorded as
`unknown`, because a sixth surface silently inheriting the desktop's name is
the same bug in a fresh disguise. The wiring is checked by an AST walk rather
than a string search, so a *new* call site is caught too -- which immediately
found one, hidden inside a `**kwargs` dict.

**Which run an approval belongs to.** `consent_dir()` is a fixed per-user path
so a provider CLI's hook subprocess can find it with no argument plumbing. The
cost is that every OPai window shares one handshake directory, and the grant
recorded only *which command* had been approved. Measured with two real
processes: window A's user approved a push in one repository, and window B --
another repository, another run, a question its user was never asked --
consumed it and was told yes.

## AC6, measured on this repo's own journal

> `completed` is impossible without the required objective/verification/delivery evidence.

It is not. The store accepts whatever verdict a caller hands it:

```
record_terminal accepted 'completed' -> True
runs.terminal_verdict               -> completed
events recorded                     -> ['run.admitted', 'run.completed']
verification artifacts              -> 0
```

`unevidenced_completions` counts the gap rather than closing it, and the
restraint is deliberate. Every mirror in `journal_runtime` records rather than
re-decides -- the layer that *can* judge a completion is `opaihub.completion`,
which has the answer, the diff and the policy in front of it. And refusing to
record a terminal state would leave the run reading as unfinished, which is a
worse lie than an unevidenced completion. Enforcement is Stage 5's; it needs
this number to be zero first, and nothing could see it before.

**Two numbers, because the first one flatters.** On the real journal in
`.opai/source`, 27 runs recorded:

| | |
| --- | --- |
| completed | 21 |
| with no evidence of any kind | **0** |
| with no verification | **20** |

Every real turn records a cost, so counting cost as evidence reads as a clean
bill of health for a criterion that is plainly unmet. AC6 names objective,
verification and delivery evidence and does not mention cost at all. Shipping
only the zero would have been this epic's own failure in miniature: a
confident answer with the inconvenient half left out.

`opai journal status` now says so:

```
unverified:     20 of 21 completed runs have no verification (AC6 asks for this one)
```


## Migration step 2 was not blocked. It was pointed at the wrong pair.

Steps 2 and 4 have both sat at "not started" for the same reason: the legacy
corpus and the journal's runs come from different subsystems, so their
populations can never overlap and no amount of waiting produces a comparison.

That is true of the *legacy* comparison. It is not true of parity in general.

`journal_store.rebuild_projection` is a deterministic fold over the event log
-- and nothing in OPai has ever handed it a reducer. It was exercised only by
its own tests: the fifth piece of #613 machinery found on this branch with no
importer, after `journal_reader`, the lease identity columns, the `approvals`
table and `mirror_from_status`. A fold with no reducer answers nothing, which
is why "collapse projections into deterministic reducers over canonical
events" had nothing to collapse into.

`opaihub/journal_projections.py` is that reducer, and the parity check it
makes possible compares the journal **against itself**. The `runs` table and
the `events` table are written by the same lifecycle calls, in the same
transactions -- two recordings of one history. If they disagree, the canonical
store is contradicting itself, which is the failure #613 opens by describing,
inside the thing meant to settle it.

It needs no legacy corpus, so unlike the qualification comparison it runs
today. On this repo's real journal:

```
comparable        : True
runs in table     : 27
runs in projection: 27
disagreements     : 0
```

`opai journal status` stays silent when they agree, counts them when they do
not, and says "unknown" out loud when the projection had to stop at an
unreadable event -- because a *short* history compared against a complete
table would report disagreements that are only the part it never read.


## A promise the app made and nothing had checked

The session inspector said **"Ready to push & open PRs"**. It got that from

```python
connected = bool(token)
ready = connected and allow
```

the *presence of a string*. An expired, revoked, wrong-scope or mistyped token
produced the identical line, and the user finds out at the worst possible
moment -- after a run has done all the work and tries to push.

OPai already knew how to check. `verify_github_connection` calls `/user` and
returns a real verdict. It was wired to one button in the Connection Doctor,
its result was never persisted, and the readiness row never consulted it. The
check answered a dialog and was forgotten.

The verdict is now remembered, and the row cites it:

| what a check found | what the row says |
| --- | --- |
| valid | Ready to push & open PRs |
| never checked | Token connected · not verified yet |
| rejected | GitHub rejected this token — reconnect in Settings |
| unreachable | Token connected · last check couldn't reach GitHub |

`ready` itself is unchanged and still means "a token is stored and pushes are
allowed" -- refusing to run because nobody has verified a token that works
would break the flow the gate exists to enable. What changed is that no caller
can render "ready" as "verified" without saying which it means.

On this machine the row now reads *not verified yet*, which is true, and sits
directly under **Tests: Not Run** -- the same honesty the app already had in
one place and not the other.

Recording is a wrapper rather than a call at each return. The implementation
has five exits and hooking them one by one means a later sixth is silently not
recorded; one exit by construction is the same reasoning
`gui_pipeline.handle_gui_message` uses for its terminal event.


## Status

| Migration step (per #818) | State |
| --- | --- |
| 1. Inventory every authoritative writer/reader | measured, above; the last unclassified writer (`cancellation_lifecycle`) now has an owner |
| 2. Parity assertions, legacy vs canonical | partial -- `cancellation_lifecycle` gained its dual read, and the events/`runs` parity check runs today with no legacy corpus |
| 3. Cut over one local-provider path | not started |
| 4. Cut over one account-provider path | not started |
| 5. Cut over cancellation, verification, cost, delivery | not started |
| 6. Switch GUI/CLI/background readers to canonical projections | not started |
| 7. Migrate persisted state | not started |
| 8. Delete legacy authoritative writes | not started (user's call) |
