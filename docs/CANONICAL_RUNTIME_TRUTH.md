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
| zero false completion | did this turn succeed? | `completed`, for partial, timeout and blocked alike |
| cancelled means stopped | did the work stop? | `cancelled`, with no phase reaching terminated |
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

### An approval was not bound to the work it was given for -- now it is

`consent_dir()` is a fixed per-user temp location shared by every OPai process
on the machine, and the grant record was `{"command": ...}`. No run, no
operation, no workspace. Reproduced with two real processes:

```
window A: user approved 'git push' for their private repo
window B: consume_grant('git push') -> True
*** window B pushed on an approval the user gave to window A ***
```

Both halves are fixed in this PR. Consumption is atomic (a rename claim), and
the grant now records the run it was issued for. The run id reaches the hook
subprocess through `OPAI_RUN_ID`, exported by `provider_child_env`, and
`consume_grant` refuses a grant belonging to another run.

The refusal needs evidence, so it fires only on a *positive* mismatch: "this
grant is run B's and I am run A". A hook that cannot say which run it is --
a provider CLI that sanitises the environment it hands its hooks -- is still
allowed, because refusing there would silently break every approved push,
which is a worse failure than the leak and is not something OPai should
inflict on a user who just clicked Approve. Where identity does not propagate
at all, the behaviour is exactly what it was before the check existed.

The `approvals` table is still not the authority (see above); the binding
lives in `command_consent`, which is what the hook can reach.

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


## AC5, measured the same way

> `cancelled` is impossible while owned controllable work is still alive.

It is not. Measured with two real processes: a process holding no fence for a
run can write `cancelled` for it while the owning process is demonstrably
still running, and the store accepts it.

```
owner pid 11308 alive: True
journal liveness verdict : 'owner_unverified'
a foreign process wrote 'cancelled' -> accepted=True
journal now says                    -> 'cancelled'
the owning process is still         -> alive
```

And on this repository's own journal, **all six** cancelled runs carry no
cancellation-phase evidence at all. Every one says the run stopped; not one
records that anything did.

`unconfirmed_cancellations` counts them. Evidence means a `run.cancel_phase`
event that reached `terminated` -- `cancellation_lifecycle` already models the
whole ladder, and `terminated` is the phase that means *confirmed stopped*
rather than *asked to stop*.

Counting rather than refusing, and here the reason is stronger than
consistency with AC6: **a Stop that OPai declined to record would be a Stop the
user pressed and did not get.** Refusing would trade a reporting fault for a
blocking one, which is never the right trade.

```
unconfirmed:    6 of 6 cancelled runs have no phase reaching 'terminated'
```

## The other direction: none of this may block anybody

Every check in this epic is a place where a bug becomes "OPai refuses to send
my message" or "the Approve button does nothing". That failure is worse than
any of the lies being fixed -- a tool that will not do what you asked is not
more trustworthy than one that occasionally reports it wrong.

An audit found one real risk, and it was introduced by this branch.
`grant_belongs_to` used strict equality, so a caller that could not name its
run was refused. The process that spends a grant is the PreToolUse hook, and
OPai does not launch it: OPai launches the *provider's* CLI, and that launches
the hook. Whether `OPAI_RUN_ID` survives that hop is a third party's decision,
so a provider that sanitises its hook environment would have silently refused
every approved push.

Refusal now needs evidence, like everything else here:

| grant | caller | allowed | |
| --- | --- | --- | --- |
| run-A | run-A | yes | the same run |
| — | — | yes | neither knows |
| run-A | — | yes | the caller cannot identify itself |
| — | run-A | yes | the grant predates run binding |
| run-A | run-B | **no** | two runs naming themselves differently |

The rest of the audit came back clean and is pinned in
`tests/test_journal_never_blocks_the_user.py`:

- the canonical journal is still **write-only** from the app's perspective --
  an AST walk over every live surface finds only `beat_lease` and `record_*`,
  so no journal state can gate a turn, a push, a PR or a merge;
- doctor's verdict is a report: a broken launcher still exits 0;
- the CLI's exit code comes from the run's own result, so correcting the
  terminal verdicts here cannot move what anyone's automation sees.


## Can a user feel any of this?

The question OPai has to be able to answer, because it is a coding tool and
none of this is worth one message somebody could not send. Measured rather
than argued.

### It costs 24 ms

A whole turn's bookkeeping -- admission, a heartbeat, the terminal record --
on the drive OPai actually lives on. A turn that calls a model takes seconds,
so this is under one percent of it.

That number nearly went the other way. The first measurement said **686 ms**
and I had already switched `synchronous=FULL` to `NORMAL` to fix it. Then the
drive turned out to be the variable:

| | sqlite `close` |
| --- | --- |
| D: (scratch drive the tests use) | 214.73 ms |
| C: (where the journal lives) | 2.82 ms |

On the real drive `FULL` costs 0.9 ms per commit, so the change bought about
5 ms per turn in exchange for power-loss durability. Reverted. Every cost test
is now a **ratio** against a plain durable commit on the same filesystem --
28 commits' worth on C:, 19 on D: -- so a slow disk moves both numbers and the
assertion still means the same thing.

### A totally broken journal does not stop a turn

Every entry point raising at once, and the turn still returns its answer byte
for byte. No journal failure reaches the result as an error, a status or a
message, and a turn that fails for its own reasons still reports *its* error.

That test passed at first while never calling the journal at all -- the
wrapper clears its ContextVar on entry, so a stubbed turn published no run
identity. Its own guard assertion caught it.

### Nothing reads the journal to decide anything

An AST walk over every live surface: the only journal calls are `beat_lease`
and the `record_*` family. No journal state can gate a turn, a push, a PR or a
merge. Stage 5 changes that deliberately, and the test says so, so a *read*
that can refuse work has to be somebody's decision rather than an accident.

The expensive reports -- parity, unevidenced completions, unconfirmed
cancellations, launcher health -- are `opai doctor`'s and doctor's only,
checked the same way.

### Exactly one visible string changed

The whole inspector payload, diffed against `main`:

```
- "github": "Ready to push & open PRs"
+ "github": "Token connected · not verified yet"
```

Every key and every row label identical, and **zero web assets changed** -- the
frontend is byte-for-byte `main`. The e2e suite drives a mock bridge with no
Python in it, so it cannot be affected by any of this.

The added work behind that row is 0.249 ms of a 41.8 ms render, and the render
already ran in a worker thread on request rather than on a poll or a
keystroke.

### And the one thing that would have blocked somebody

`grant_belongs_to` used strict equality, so a caller that could not name its
run was refused. The process that spends a grant is the PreToolUse hook, and
OPai does not launch it -- the *provider's* CLI does. A provider that
sanitises its hook environment would have silently refused every approved
push. Refusal now needs positive evidence: two runs naming themselves
differently. Verified against the worst case with real subprocesses.


## A second review, and what it found

An independent review of this branch ran the suites, reproduced defects with
real processes, and reported twenty-one findings. Five were reproduced against
running code, which is the standard the rest of this document holds itself to,
so they are recorded here with what each one cost and what closed it.

### 1. The lease named whoever first saved the run, not whoever ran it

`opai automation enqueue` writes the run file and exits. `opai automation run`
executes it in a **second process** that never took the lease over. So the
journal's owner was a pid that had been gone for milliseconds, and a
concurrent `opai automation recover` did exactly what the liveness check was
added to prevent:

```
failed: the owning session ended before it finished   <- on a run that was running
```

The reverse held too: a GUI that only queued a run kept it looking owned for
six hours after the process actually running it had died.

A process that saves an *executing* state (preparing / running / verifying)
now takes the lease over. A cancel request or a recovery sweep describes a run
without running it, and must not fence out the process that is -- so those do
not. Reproduced end to end with three real processes, and pinned by
`tests/test_journal_executor_owns_the_run.py`.

### 2. A turn that stopped to ask was filed as permanently blocked

`evaluate_completion` returns BLOCKED for every `needs_*` status, and the
recorder preferred the verdict over the status. So "shall I push?" was written
into the canonical record as `blocked` -- an immutable terminal -- for a turn
whose user's next click resumes the same work. Measured on a real turn:

```
status              needs_auto_confirmation
completion_verdict  blocked
run_state           awaiting_input
journal             blocked          <- before
journal             awaiting_input   <- after
```

The engine already computes the right answer once, in `run_state` (#379), with
the one exception only the status knows about. The journal records that field
now instead of re-deriving a worse version of it, and a result without one is
derived through `generated_lifecycle.LEGACY_STATUS_MAP` -- the map every
surface shares -- rather than a hand-written copy that had already drifted by
ten of the thirteen awaiting statuses.

### 3. The parity check reported disagreements that were not there

`turn_parity` matched a whole conversation against each run, because no saved
turn recorded which run produced it. An ordinary chat with one complete and
one partial turn read as "2 of 2 runs disagree".

Each saved assistant turn now carries the journal run id, reported by the
pipeline to its caller *out of band* -- the result a surface hands the user is
still byte for byte what the turn produced -- and the check joins turn by
turn. Turns saved before the key are counted as unjoinable, never as
disagreements. The two records also name endings differently (`awaiting_input`
against the saved verdict `blocked`), so what is compared is the question
#818 asks of both: did this turn finish the work, was it cancelled, or
neither.

### 4. `journal status` said "unfinished: 0" over a real unfinished run

Every report in the doctor shared one `suppress(Exception)`. On a journal
written by a newer OPai the first report raised, nothing after it was set, and
the CLI filled the gaps with its own reassuring defaults. Every fact now
starts as "not checked", each report stands alone and records why it could not
look, and a missing key reads as unknown rather than fine.

### 5. The heartbeat could freeze a streaming answer

`beat_lease` runs on the turn's own thread, between streamed chunks, and
opened the store with the ordinary ten-second busy timeout. A backup,
compaction or migration holding the write lock froze the answer for about
eleven seconds -- against a docstring promising "never block a turn".
Heartbeats now wait 50 ms and skip; every other write keeps the full timeout,
because a verdict must still land.

### The rest

Also fixed, each with a test: an approval could be revived after its turn
ended by the put-back in `consume_grant`; the armed run was a process-wide
global two concurrent turns overwrote; `uncertain` was a way back down the
operation ladder (`reconciled -> uncertain -> intended`); a GitHub verification
outlived the token it was about; the launcher doctor read `/usr/bin/env
python3` as `env` and called pip's `/bin/sh` trampoline healthy whatever it
ran; `automation recover` changed its stdout shape and listed chat turns;
`journal pending` explained a run with no recorded process as a reused pid;
parity compared a reason the row truncates against one the event does not, and
read the two tables in separate snapshots; the run reducer claimed constant
cost while copying every run per event; and the PR's own inventory test was
failing because two new modules were unclassified.

Three findings were about the same shape of mistake rather than a defect: one
validator for process ids instead of three that disagreed, one reading of the
clock per budget report instead of four, and one place that says "at least "
instead of three. Each disagreement was real: `pid_is_running(True)` probed
pid 1, which exists everywhere.

## Living alongside the other open branches

Two other pull requests were open while this one was finished: #842 (the
Agents command center) and #817 (the settings redesign). A change to the
journal's schema is the kind of thing that stays invisible until somebody else
merges, so both were trial-merged against this branch and their tests run on
the result, rather than assumed compatible because git said so.

### Two branches, one migration number

#842 also defines journal migration **2** -- agent-objective tables, where this
branch's migration 2 is the lease owner columns. Every build before this one
decided what to run from the recorded number alone, so whichever build touched
a journal first stamped it v2 and the other build skipped its own v2 for ever:
tables or columns that never existed, on a database every structural check
called perfect.

`migrate()` now checks each migration against the catalogue and applies what
is missing, deciding again under the write lock. A complete journal costs one
catalogue read and no write lock (`open_store` 1.28 ms before, 1.46 ms after).
Two ratchets keep it that way: every migration statement must be a shape that
can be checked (`CREATE TABLE/INDEX IF NOT EXISTS`, `ALTER TABLE ... ADD
COLUMN`), and versions must run 1..N with no repeats -- so a merge that resolves
the collision by keeping two `(2, ...)` entries fails instead of shipping. The
shape the old creation race left behind (v1 recorded, v2's columns present) no
longer bricks anything; it simply opens.

### The stamp locked older builds out

Measured on a `main` worktree: once this branch's build had opened a project's
journal, `main` -- and so #817, which is `main` plus settings -- called it
"written by a newer OPai". Admission returned no fence, nothing was journalled,
and doctor escalated the project. All for a migration that adds two nullable
columns an older build would never notice.

The recorded version now means *what an older build must know to use this
journal*, not *the newest migration applied*. Migrations an older build can
ignore are declared in `_OLDER_BUILDS_CAN_IGNORE` and do not raise it; the
ratchet refuses a unique index there, because an older build's writes could
violate one. Journals this branch had already stamped 2 are restored to 1 the
next time they are opened -- written once, never lowered past a stamp this
build cannot vouch for, and decided under the lock so a newer build stamping
meanwhile is left alone. The same `main` worktree then admitted a run into that
journal, and doctor called it healthy.

That also closes the other merge order. Had #842 merged first, its build would
have trusted a v2 stamp written by this one and skipped the Agents tables.

### What the trial merge found

Three files conflicted (`journal_store.py`, `gui_pipeline.py`,
`chat-components.test.js`); each resolves mechanically. The first trial merge
ran 1015 tests on the merged tree -- the web unit suite passed too (130) -- and
three failed:

- `test_journal_inventory` -- #842's own: it fails on #842 alone, because its
  new modules write the journal without being classified.
- `test_journal_run_origin` -- this branch's ratchet doing its job:
  `objective_worker.py` drives turns without naming its surface. Its failure
  message now says exactly what to add.
- a fixture in this branch that borrowed #842's real table name, and collided
  with the real table the moment both existed. It uses its own names now.

With the recipe below applied, the same run is 1056 passed and one failed:
#842's own inventory test. The recipe was scripted and run, not just written
down, and doing so found that this branch's new interop tests had hardcoded
version numbers "1" and "2" -- every one broke on the merge, where #842's
migration became 3. They now test the mechanism on a migration added on top of
whatever the build has.

One interaction is not a failure but will be visible: #842's `ObjectiveStore`
writes `runs` rows directly rather than through `journal_runtime`, so those
runs have no lifecycle events. `opai journal migration` will count them under
event parity, and `journal pending` will list their leases as `unknown`. Both
are reports, not gates -- doctor readiness does not read them, and nothing is
blocked -- and both are true: the store does disagree with itself about those
runs until they record their events.

#817 merges without conflict. The only file both branches change is `app.js`,
in unrelated places.

### Doctor still migrated

Review finding 16 said running doctor silently migrated the journal. The fix
made `store_health` ask without migrating, and pinned that one function. It
did not make the finding false: the migration report doctor prints next --
event parity, unfinished runs, turn parity -- opened the store the ordinary
way, and a journal one migration behind came out of `opai doctor` upgraded.
Found by running doctor on such a journal rather than by reading the fix.

Doctor now runs inside `journal_store.reading_only()`. There `open_store`
creates nothing and migrates nothing: a journal that needs a migration is
refused, each report says "migration pending" instead of a guessed number,
nothing is escalated, and the next ordinary use applies it as before. The
flag is a context variable, so a turn running on another thread while doctor
looks is not made read-only.

### Merge recipe for whichever lands second

1. `journal_store.py`: keep both migrations, numbered 2 and 3 in merge order,
   and set `SCHEMA_VERSION` to 3. If the Agents tables are judged ignorable by
   older builds (new tables and a non-unique index are), add 3 to
   `_OLDER_BUILDS_CAN_IGNORE`; left out, the stamp becomes 3, which is the safe
   default.
2. `gui_pipeline.py`: keep both import lines, union the keyword arguments
   (`surface`, `conversation_id` and #842's `task_id`, `run_id`,
   `authority_root`, `local_model_endpoint`, `objective_bypass_permissions`),
   and keep #842's skip of admission when `authority_root` is set around this
   branch's `record_admission(... surface=..., session=...)` call.
3. `chat-components.test.js`: this branch's assertions are a superset; keep
   them.
4. Add `"opaihub/objective_worker.py": "agent"` to `CALLERS` in
   `tests/test_journal_run_origin.py` and pass `surface="agent"` where it calls
   `handle_gui_message`.

## Status

| Migration step (per #818) | State |
| --- | --- |
| 1. Inventory every authoritative writer/reader | measured, above; the last unclassified writer (`cancellation_lifecycle`) now has an owner |
| 2. Parity assertions, legacy vs canonical | partial -- `cancellation_lifecycle` gained its dual read; the events/`runs` parity check runs today with no legacy corpus; and the cross-surface check joins turn by turn, on the run id each saved turn now carries |
| 3. Cut over one local-provider path | not started |
| 4. Cut over one account-provider path | not started |
| 5. Cut over cancellation, verification, cost, delivery | not started |
| 6. Switch GUI/CLI/background readers to canonical projections | not started |
| 7. Migrate persisted state | not started |
| 8. Delete legacy authoritative writes | not started (user's call) |
