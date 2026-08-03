# Epic #295 — alpha gate audit

**Status: the epic is NOT closeable. 5 of 14 alpha gates are fully evidenced; the rest
are blocked on other P0 epics or on work not yet done.**

#295 states plainly: *"This epic remains open and alpha remains blocked until
all of the following are evidenced."* This document records, gate by gate, what
is actually evidenced today and what each remaining gate needs — so the decision
to ship or wait is made on facts rather than on a feeling that a lot of work has
landed.

It is written to be re-run: every "evidenced" claim names the test that proves
it, and every gap names the epic that owns it.

---

## Summary

| # | Gate | Status | Owner of the gap |
| --- | --- | --- | --- |
| 1 | False completion: 0 | **Partial** | require a passed policy |
| 2 | Silent message loss: 0 | **Evidenced** | — |
| 3 | Duplicate active run: 0 | **Partial** | cross-process durability |
| 4 | Duplicate side effect: 0 | **Partial** | provider/tool calls |
| 5 | Orphan processes: 0 | **Evidenced (Windows)** | POSIX CI run |
| 6 | Cross-surface terminal agreement | **Partial** | #525 shared control |
| 7 | Illegal transitions: 0 unhandled, observable | **Evidenced** | — |
| 8 | Restart recovery: 100% | **Partial** | #517 resume decision + reconciliation |
| 9 | Cancellation truth | **Partial** | #380 teardown proof |
| 10 | Provider conformance: 100% | **Evidenced** | — |
| 11 | Verification truth | **Partial** | #522 |
| 12 | Budget/policy fail-closed | **Evidenced** | — |
| 13 | No hidden active work | **Partial** | #524 desktop lifecycle |
| 14 | Receipt completeness | **Partial** | residual risk |

Five gates are genuinely closed (2, 5, 7, 10, 12) — gate 5 on this Windows host,
pending a POSIX CI run of the same fixtures. No gate is at "not started" any
more, but "Partial" is doing real work in this table: gate 1 closed
provider-manufactured completion and now derives acceptance from the resolved
policy, yet a repository with no required test checks still completes on diff
evidence alone; gate 3 covers in-process duplicates but not cross-process ones;
gate 4 keyed the operations that write history but not provider or tool calls.
Each of those is a genuine remaining gap, not a rounding error.

**Two entries in this table were wrong in the first draft** — gate 5 was recorded
as "not started" when the mechanism was already wired into the provider path,
and gate 12 as "unaudited" when the negative was already proven by name. Both
were corrected by checking rather than trusting the note. An audit that is not
itself verified is just a second opinion.

---

## Gate-by-gate

### 1. False completion: 0 — Partial (provider-manufactured completion closed)

`completed` is computed from a verdict, never from model prose
(`opaihub/completion.py`), and the pipeline emits the canonical run state
alongside it. `test_completion_contract.py` and `test_run_state.py` hold the
one-for-one mapping between terminal states and verdicts.

**A provider can no longer manufacture one.** `evaluate_completion` promised in
its own docstring that only evidence from OPai's execution path could return
`COMPLETED`, and two things contradicted that:

- `_has_successful_test` accepted a bare `{"tests": {"status": "passed"}}` —
  a status string with no provenance was enough to satisfy `tests_pass` and
  stamp the run completed. Measured against the old code, both that and the
  legacy `test_results` spelling returned `COMPLETED` on nothing but a claim.
- The call site built the verdict's input as `{**payload, ...}`, spreading the
  whole provider-influenced result, so whether that key was reachable depended
  on which fields happened to exist rather than on the design. The old spread
  carried `tests` *and* a provider-supplied `completion_verdict` straight in.

Nothing populated those keys on the live path, so it was not exploitable —
which is exactly the distinction removed. Unreachable-today is one refactor from
reachable, and the gate asks for *impossible*. Test evidence now requires a
record of something OPai ran (its own tool trace, or a check carrying the exit
status it observed), and `evidence_payload()` allowlists what the verdict may
read, so a new field is invisible to it until deliberately added to
`MEASURED_EVIDENCE_KEYS`. `test_false_completion_guards.py` (14 tests + 6
subtests) covers both directions — including that honest runs still complete,
since a guard that fails real work gets deleted.

**Missing:** the verdict is still only as strong as the policy behind it. #590
now *resolves* a versioned verification policy, but nothing yet **requires** an
applicable passed policy: an implement-mode run whose text does not mention
tests still completes on diff evidence alone. Wiring the resolved policy into
the acceptance requirements is the remaining step, with the check runner in the
rest of #539.

### 2. Silent message loss: 0 — Evidenced

Every submission is accepted, queued, rejected or visibly blocked.

- A message typed during an active run is held and sent, never discarded —
  `queued-message.spec.js` (7 tests), including that it is not flushed into an
  awaiting-input turn.
- A turn that stops to ask is `awaiting_input`, non-terminal, and resumable —
  `test_awaiting_input_state.py` (24 tests + 47 subtests).
- Dead-end failures name a model that can still run —
  `test_pipeline_consistency.py`.

### 3. Duplicate active run: 0 — Partial (in-process closed)

`opaihub/admission.py` supplies the admission key the gate is worded against,
and `SessionRegistry.claim()` enforces it.

**The defect.** `handle_gui_message` minted a fresh random `turn_id` per call.
The single-flight registry keys on that id, so it could only deduplicate callers
that *already knew* two submissions were the same — two submissions of one task
got two ids and were, by construction, two different requests. A double-click, a
renderer replaying a pending send after reconnecting, or Retry pressed mid-run
(`send(retryOf)` bypasses the busy guard) each started a **second full run**: two
provider calls, two charges, two sets of edits racing over the same files.

**The key** is derived from what makes two submissions the same intent —
repository, task text, model, mode. The path is resolved so two spellings of one
checkout do not become two runs, and the key is a digest so no prompt text rides
into snapshots or logs.

**Scoped to active runs**, which is the design point. #295 requires consistency
*"while not limiting user messages and interactions"*, and asking the same thing
twice on purpose is a real second request. A submission is a duplicate only while
an equivalent run is still live — which catches every mechanical duplicate (they
arrive *because* the first is still running) and blocks no deliberate one.

**Atomic**, because a double-click is a race. `claim()` test-and-sets inside one
lock acquisition. A check-then-act version was written first and measured: with a
5ms window, 4 concurrent submissions started 4 runs. That also exposed a bad
test — a 12-thread race test passed against the broken implementation, because
the real window is sub-microsecond — so the test now widens the check
deterministically instead of hoping for an interleaving.

**Never a dead end:** a duplicate returns the live run's id. The GUI removes the
duplicate bubble rather than rendering an error card; the CLI exits `blocked`.
`test_admission.py` (22 tests + 4 subtests) covers both directions.

**Missing:** cross-process duplicates — GUI and CLI submitting the same task
simultaneously — are not covered, because the guarantee is in-memory. Closing
that needs the durable admission record the epic also asks for (*"persist the
admission event before displaying the task as active"*), which is the same
durability work gate 8 needs. #517.

### 4. Duplicate side effect: 0 — Partial

`opaihub/idempotency.py` supplies operation keys with a **three-state** model,
and the two outward operations that had no protection at all are wired to it:

- `open_pr` — `create_pull_request` POSTed straight to GitHub, so a retried,
  resumed or reconnected turn opened a second pull request.
- `comment_pr` — the same, posting a duplicate comment on the thread.

Both are visible to other people and not undoable by OPai.

The third state is the design point. A set of completed keys is wrong at exactly
the moment it matters: the process can die *between* performing the side effect
and recording it. With two states the store must guess, and both guesses are
real failures — redo duplicates, skip silently drops work. So an operation is
`fresh` (safe to perform), `in_flight` (**may already exist**; not repeated, not
claimed as success, reported as uncertain), or `done` (recorded result returned).

`test_idempotency.py` (22 tests + 4 subtests) covers key stability and
separation, the crash-between-effect-and-record case, provably-failed attempts
releasing the key, uncertainty expiring rather than blocking forever, results
holding no bodies or secrets, and an unwritable store never blocking the user.
Red-checked: neutering the guard fails 6 tests including both duplicate cases.

`git_commit` is now keyed on staged **content** (`git write-tree`), not on the
request — keying on paths and message would refuse a user who legitimately
commits "wip" twice with real work in between.

Measuring an unkeyed build established what that actually buys, and it is
narrower than it first appears:

    WITHOUT the key   replay.ok=False   GIT_COMMIT_FAILED
    WITH the key      replay.ok=True    Already committed as e46fc0f
    (commits in history: 2 either way)

Git already refuses the duplicate. What the key adds is honesty — a replay used
to be told the commit *failed* when it had landed, and the caller is usually the
model, so a spurious failure invites it to amend or re-commit differently to fix
a problem that does not exist — plus the crash-between-commit-and-record case,
which git has no opinion about and which now reports uncertainty.

**`git_push` is deliberately left unkeyed**: re-pushing the same ref at the same
sha is a no-op, so a key would add bookkeeping and no safety, and ceremony that
implies a guarantee it does not provide is worse than none.

**Missing:** provider calls and tool invocations are unkeyed. Durable replay
across processes is #517.

### 5. Orphan processes: 0 — Evidenced on Windows, logic-only on POSIX

*This entry was corrected twice. The first draft said "not started", which was
wrong — the mechanism was already wired in. The second said the mechanism only
lacked real-process proof. Writing that proof found a defect the unit tests had
encoded as intended behaviour.*

**The defect.** `terminate_tree` returned immediately when the direct child had
already exited. That reads as reasonable and is wrong in the one case that
matters: a **crashed provider CLI** is precisely when its grandchildren
(language servers, git, sub-agents) are left running against the user's
repository. A dead root is not evidence of a clean tree. A red-check against
the old implementation confirms the orphan survived; against the new one it
does not.

`test_process_tree.py` had asserted the buggy behaviour by name
(`test_already_exited_process_is_not_signalled`). It is replaced by two tests
that separate the cases it had conflated: an exited-but-unreaped child still
gets its tree reaped, and a **reaped** child is never signalled again — because
once a pid is reaped the OS may reissue it, and signalling a recycled pid hits
a stranger.

**The fix.** `adopt()` records, at spawn time, how to reach the tree *after the
root dies*:

| | mechanism | why the old path could not do it |
| --- | --- | --- |
| Windows | job object | `taskkill /T` walks parent→child; once the root is gone the link is gone. Job membership outlives the root. |
| POSIX | process group id recorded at spawn | `getpgid` fails on a dead leader — exactly when it is needed. |

The Windows job also carries `KILL_ON_JOB_CLOSE`, so the tree dies even when
OPai is force-killed and none of its cleanup code ever runs. `_killpg` refuses
to signal OPai's own group: cheap to check, unrecoverable to miss.

**Evidence.** `test_orphan_processes.py` spawns real child *and grandchild*
processes and proves each scenario the gate names — cancel, timeout, app exit,
provider crash, retry. Two deliberate choices make the proof mean something:

- Liveness is a **heartbeat file**, not a pid probe. A pid can be recycled, and
  on Windows `os.kill(pid, 0)` does not probe — it terminates.
- Every scenario **first proves the orphan is real**, asserting the grandchild
  is still beating before anything claims to have reaped it.

Plus `test_process_tree.py` (26 tests) for the logic, adoption failure modes,
single-release of the handle, and the self-group guard.

**Missing:** the real-process fixtures run on this Windows host; the POSIX
paths are covered by logic tests and by the same fixtures when run on POSIX
CI, which has not yet happened. Browser workers and temporary servers are not
separately fixtured — the tests use generic child processes, which exercise the
same mechanism but do not name those two cases from the gate.

### 6. Cross-surface terminal disagreement: 0 — Partial

GUI and engine agree: `test_run_state_parity.py` asserts the JS store's state
vocabulary maps onto the canonical machine and that the awaiting-status lists are
identical; `test_runtime_phase_parity.py` binds the engine's `RuntimePhase` to
the same lifecycle and proves no legal phase move implies an illegal canonical
transition.

The CLI now maps terminal states to **distinct** exit codes from the same
canonical source (`run_state.exit_code_for`), so `opai ask` and the streaming
path cannot drift from each other or from the engine:

| ending | code | why |
| --- | --- | --- |
| completed | 0 | success; keeps `if ! opai ask …` working unchanged |
| failed | 2 | the code it already meant |
| partial | 3 | work landed but is unverified — not the same as failure |
| blocked | 4 | a refusal; retrying hits it again |
| timeout | 5 | worth retrying |
| cancelled | 130 | the shell's SIGINT convention, already in use |

Every non-completed ending previously collapsed to `2`, so automation could
learn only "it failed" — it could not retry a timeout while leaving a refusal
alone. `1` is deliberately unused: argparse and most shells spend it on usage
errors, and a lifecycle outcome must not be confused with a mistyped command.
`test_cli_exit_codes.py` (16 tests + 29 subtests) pins the values as the public
contract they are and drives each ending through the real CLI.

**Missing:** the rest of Workstream H — starting a task on one surface and
controlling it from the other, and rehydrating shared state on reconnect. #525.

### 7. Illegal transitions: 0 unhandled, every violation observable — Evidenced

Both halves now hold.

- *Rejected:* `run_state.transition()` refuses the edge and preserves the prior
  state; `AgentRuntime._move` raises outright.
- *Observable:* refusals are recorded with a bounded tail and an unbounded
  count, on both the engine (`illegal_transitions()`) and the GUI store
  (`illegalTransitions()`). `test_illegal_transitions.py` (13 tests + 6
  subtests) covers recording, terminal-escape attempts, bounding, thread
  safety, source sanitisation, and that a caller cannot mutate the record.
- *Zero in real use:* the same suite drives the real pipeline through answered,
  failed and awaiting turns and asserts the count stays 0.

### 8. Restart recovery: 100% — Partial

Session resume exists and is covered by `session-resume.spec.js`.
`owner_lease.py` now supplies the evidence needed to tell an abandoned run from
one a sibling window owns, and now also issues durable, fenced leases
(`owner_lease.acquire/renew/is_current`) so a superseded supervisor can be
detected rather than trusted on its own heartbeat — `test_owner_lease.py`'s
`TwoProcessLeaseRaceTests` and `ClockSkewTests` cover the split-brain and
clock-jitter cases #517 calls out explicitly.

Workflow runs (#379's canonical run/step machine) are now backed by an
append-only journal (`opaihub/run_journal.py`) with monotonic sequence
numbers, atomic fsync'd appends, and a fenced supervisor lease per run
(`workflow_runner.workflow_journal_path` / `workflow_lease_path`), additive to
the existing snapshot file so no prior consumer changed. `replay_workflow_run`
reconstructs a run's canonical state purely from the journal and is proven —
not assumed — to agree with the persisted snapshot, including on a failed run,
by `RunJournalReplayTests` in `test_workflow_log_persistence.py`. A corrupt or
unrecognized record is quarantined (moved aside with a manifest, never
silently dropped or guessed past) rather than accepted, and the pipeline
recovers on the very next transition — `test_run_journal.py` (26 tests) covers
crash recovery at every durable write boundary, a truncated tail, a corrupt
interior record, and repeated recovery converging identically.

**Missing:** nothing yet *acts* on a stale lease or a quarantined journal —
both are observable (`read_workflow_log(...)["lease"]`,
`Recovery.quarantined`) but no resume flow decides what to do with them yet.
There are still no kill-and-resume fixtures at each lifecycle phase, and
reconciliation after restart against live provider subprocesses and GitHub
(worktrees already reconcile via `worktree_leases.py`, #537) remains open.

### 9. Cancellation truth — Partial

Immediate acknowledgement is evidenced: `stop()` moves to `cancel_requested`
("Stopping…"), control returns to the user at once, and `cancelled` is claimed
only when the backend confirms the worker returned — `cancel-teardown.spec.js`
(7 tests), plus twelve updated regressions.

**#380, first slice landed:** the gap the epic actually names — *"'Cancel
requested' is not cancellation if provider calls, child processes... continue"*
— is closed for the highest-risk case. `AgentComputerInterface.run_command`
(`opaihub/aci.py`) now polls a real, isolated process tree and calls
`terminate_tree` (#108) the moment a cancellation or timeout fires, instead of
blocking inside one uninterruptible `subprocess.run` call; `test_aci_cancellation.py`
proves this against a real spawned grandchild, not just injected logic — the
actual "runner-wedge" evidence #264 asks for. `RepositoryToolExecutor.invoke()`
used to check the token exactly once before dispatch, so nothing downstream
ever looked again once a git or test command had started; `cancel` is now
threaded through every tool method that can act on it (`_run_command`,
`_git_commit`, `_git_push`, `_git_create_branch`, `_open_pr`), closing "between
push and PR creation" and the same shape of gap one level down, between two
git calls inside one commit (`test_provider_tools_cancellation.py`). A
cancelled attempt is reported as `CANCELLED`, distinct from an ordinary
failure — required so a retry policy can never retry a cancellation — and
leaves no idempotency residue (a cancelled commit's key is abandoned, not left
`in_flight`).

`opaihub/cancellation_lifecycle.py` adds the durable, ordered phase model the
epic asks for — `requested -> acknowledged -> draining -> force_terminating ->
terminated`, refining `CANCEL_REQUESTED` the way `RuntimePhase` refines the
rest of the lifecycle — backed by the #517 journal, so "cancellation
acknowledgement latency" and "hard-stop latency" (the epic's own named
metrics) are computed from durable timestamps. `test_cancellation_lifecycle.py`
covers phase legality, metrics, and — after `run_journal.append_if` was added
to fix a real race the test caught — two callers racing to cancel the same
scope converging on one consistent history.

**Missing:** the phase tracker is not yet wired into the live GUI/CLI turn
lifecycle (`local_runner.py`'s existing `LocalRunCancelled`-based streaming
cancellation and `background_runs.py`'s `cancel_event` continue to work
exactly as before, untouched, rather than risked in the same change) — that
integration, plus mid-flight cancellation of `git push` specifically (a
network call bounded by a 120s timeout today, not yet pollable the way
`run_command` now is) and provider-adapter-level token propagation (#533),
remain open. Bounded teardown is a 10s client-side timer, not a platform SLO, and
an unconfirmed teardown reports honestly but does not raise `needs_attention`
with evidence. `needs_attention` is not a canonical state yet because nothing
produces it.

### 10. Provider conformance: 100% — Partial (matrix exists and is green)

`opaihub/provider_conformance.py` states the contract as **14 named clauses**,
each recording the user-visible symptom of its breach, and
`test_provider_conformance.py` runs every clause against every adapter with no
network call and no spend.

The gap it was written to close: the two families had silently diverged.

| family | entry point | on provider failure | `available()` |
| --- | --- | --- | --- |
| `AccountRunner` (CLIs) | `stream()` | returns a result dict | checks a path |
| `LocalRunner` (HTTP) | `complete()` | **raises** | **makes a network call** |

Everything above them is written against *one* set of promises, so each
divergence surfaced to the user as "sometimes it works and sometimes it
doesn't".

**The first run failed 9 of 56 cells, and four were real defects:**

1. **Partial output was discarded on failure.** Three error paths returned
   `text: ""` while the accumulated stream held content the user had already
   watched appear. The retry then regenerated — and re-paid for — the same
   tokens.
2. **An empty reply was a silent success.** No text, exit 0, no error: the run
   rendered as answered when nothing came back. `NO_RESPONSE` already existed in
   the error vocabulary; nothing emitted it. Tool steps are now the
   discriminator, so an edit-mode run that changes files and says nothing is
   still not called a failure.
3. **A throwing activity listener killed the run.** `on_text`/`on_event` were
   called unguarded, so a rendering bug in one line destroyed an otherwise
   healthy run. Listeners are observers and must not be able to kill what they
   observe.
4. **Ollama could not stream at all**, purely because it speaks NDJSON rather
   than SSE — so a mid-run failure lost everything every other provider kept.
   The stream reader is now protocol-agnostic and Ollama streams like the rest.

The remaining two failures were faults in the harness, not the product, and are
recorded as such: `cost_is_real_or_unknown` rejected `None`, which the clause
itself calls conforming; and the local probe called `runner.complete` directly
rather than through the product's own `_complete_streaming`, measuring a call
OPai never makes.

**Coverage is now complete for the advertised adapters**: claude, **codex** and
copilot on the CLI side, Ollama, **free-tier API** and OpenAI-compatible on the
HTTP side — 6 adapters x 14 clauses, all passing.

Codex was the shape most likely to differ: text arrives only on
`item.completed` rather than as deltas, the turn ends with an explicit
`turn.completed`/`turn.failed`, and there is an out-file fallback. It passed on
first run, and the probe was checked for vacuity rather than trusted — a healthy
run parses `Hello world.` through the real parser, a failure keeps its partial
text alongside a classified error, and an empty turn is not a success. It passes
because the four defects found via claude and copilot were fixed in the shared
`AccountRunner.stream()`, which all three CLIs use.

**Missing:** the clause list still does not cover tool-call or heartbeat
behaviour from Workstream C, so the *contract* is narrower than that workstream
describes even though every adapter now satisfies it. #520.

### 11. Verification truth — Partial

See gate 1. The mechanism exists; the *requirement* does not. #522.

### 12. Budget and policy fail-closed — Evidenced

*Also corrected during the audit: the first draft said "unaudited", which meant
"I did not look". Looking found the negative already proven.*

The gate's exact requirement — unknown or corrupt state cannot become zero cost
or broad authority — is covered by name:

- `test_corrupt_primary_and_backup_fails_closed_on_paid_routes`
- `test_corrupt_nan_cap_on_disk_fails_closed_not_open`
- `test_corrupt_primary_recovers_the_configured_cap_from_backup` (the user's cap
  survives corruption rather than vanishing to zero)

The authority half is covered in the same shape elsewhere:
`test_a_corrupt_grant_file_is_treated_as_no_grant` (command consent),
`test_unparseable_payload_fails_closed` and `test_empty_payload_fails_closed`
(agent spawn guard), `test_invalid_transition_fails_closed` (agent runtime), and
`test_unknown_legacy_status_fails_closed` (completion contract).

### 13. No hidden active work — Partial

The supervisor lease means a run always records an owner, and the queued-message
work means input is never silently dropped.

**Missing:** the gate is about renderer/window failure specifically — that a
desktop crash cannot leave paid work undiscoverable. Proving it needs #524.

### 14. Receipt completeness — Partial (evidence and authority landed)

Receipts carry route, cost, model and the verdict, and `message_contract`
records the lane and its reason. Probing a real run showed `evidence`,
`changed_files` and `approvals` were all absent — a receipt stating an outcome
without the evidence behind it, or what the run was allowed to do, asks the user
to take OPai's word for it, which is what a receipt exists to avoid.

Receipts now carry:

- **`evidence`** — the same `EvidenceRef` list the verdict was computed from, so
  the reasoning is checkable rather than asserted. This is the visible half of
  gate 1's work: the verdict trusts only observed evidence, and now the user can
  see it.
- **`changed_files`** — measured from the repository, not claimed.
- **`authority`** — every one-shot grant the turn used, with its scope. The
  approved command is redacted and bounded first: it can carry a token in a
  flag, and a receipt is durable, so it must not become where a secret rests.
  `approvals_recorded` is stated explicitly, because an empty list otherwise
  cannot distinguish "nothing was approved" from "we did not record approvals".

`test_receipt_evidence.py` (11 tests) covers those, plus the existing promise
that no raw prompt reaches the receipt.

**Missing:** residual risk is still not represented — it needs a risk model that
does not exist yet — and full verification-run evidence arrives with the check
runner in the rest of #539. #516 / #528.

---

## What was delivered against this epic

| Slice | Gate(s) | Evidence |
| --- | --- | --- |
| `awaiting_input` / `cancel_requested` states | 2, 6 | `test_awaiting_input_state.py` |
| Queued message during a run | 2 | `queued-message.spec.js` |
| Two-phase cancellation | 9 | `cancel-teardown.spec.js` |
| `RuntimePhase` bound to the canonical lifecycle | 6, 7 | `test_runtime_phase_parity.py` |
| Supervisor leases | 3, 8, 13 | `test_owner_lease.py` |
| Observable illegal transitions | 7 | `test_illegal_transitions.py` |

## Honest reading

The consistency work landed so far removes several classes of *lie* — a run that
was waiting reported as failed, a cancel that claimed teardown it had not
proven, a message silently discarded, an engine running a second undeclared
lifecycle, a crashed run indistinguishable from a live one, and a refused
transition nobody could see.

It does not make the runtime deterministic in the sense the epic requires. The
three gates with no implementation at all — duplicate side effects, orphan
processes, provider conformance — are exactly the ones that protect money, the
user's machine and the user's repository. They should not be waived quietly.

Per the epic: *"Any waiver must be documented in #518 with owner, evidence,
bounded impact, mitigation, expiry and rollback plan."*
