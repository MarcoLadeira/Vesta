# Epic #295 — alpha gate audit

**Status: the epic is NOT closeable. 3 of 14 alpha gates are fully evidenced; the rest
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
| 1 | False completion: 0 | **Partial** | #522 verification policy |
| 2 | Silent message loss: 0 | **Evidenced** | — |
| 3 | Duplicate active run: 0 | **Partial** | #517 admission keys |
| 4 | Duplicate side effect: 0 | **Partial** | git ops + #517 replay |
| 5 | Orphan processes: 0 | **Partial** | real-process fixtures |
| 6 | Cross-surface terminal agreement | **Partial** | #525 shared control |
| 7 | Illegal transitions: 0 unhandled, observable | **Evidenced** | — |
| 8 | Restart recovery: 100% | **Partial** | #517 replay |
| 9 | Cancellation truth | **Partial** | #380 teardown proof |
| 10 | Provider conformance: 100% | **Not started** | #520 adapter contract |
| 11 | Verification truth | **Partial** | #522 |
| 12 | Budget/policy fail-closed | **Evidenced** | — |
| 13 | No hidden active work | **Partial** | #524 desktop lifecycle |
| 14 | Receipt completeness | **Partial** | #516 / #528 |

Three gates are genuinely closed (2, 7, 12). Nothing here should be read as
"nearly done": gate 10 has no implementation at all, and gate 5's
mechanism is proven only against injected fakes. Those are the ones that protect
the user's repository, their money and their machine.

**Two entries in this table were wrong in the first draft** — gate 5 was recorded
as "not started" when the mechanism was already wired into the provider path,
and gate 12 as "unaudited" when the negative was already proven by name. Both
were corrected by checking rather than trusting the note. An audit that is not
itself verified is just a second opinion.

---

## Gate-by-gate

### 1. False completion: 0 — Partial

`completed` is computed from a verdict, never from model prose
(`opaihub/completion.py`), and the pipeline emits the canonical run state
alongside it. `test_completion_contract.py` and `test_run_state.py` hold the
one-for-one mapping between terminal states and verdicts.

**Missing:** the verdict is only as strong as the verification policy behind it,
and that policy lives in #522. Until an applicable passed policy is *required*,
"technically impossible to complete without proof" (the gate's own wording) is
not yet true.

### 2. Silent message loss: 0 — Evidenced

Every submission is accepted, queued, rejected or visibly blocked.

- A message typed during an active run is held and sent, never discarded —
  `queued-message.spec.js` (7 tests), including that it is not flushed into an
  awaiting-input turn.
- A turn that stops to ask is `awaiting_input`, non-terminal, and resumable —
  `test_awaiting_input_state.py` (24 tests + 47 subtests).
- Dead-end failures name a model that can still run —
  `test_pipeline_consistency.py`.

### 3. Duplicate active run: 0 — Partial

`opaihub/session_registry.py` enforces single-flight per request id, and the GUI
refuses a second concurrent submit (`activity.spec.js`: "Enter during generation
does not create a duplicate request"). `opaihub/owner_lease.py` now identifies
the owning process.

**Missing:** the gate is *per admission/idempotency key*, and there is no
admission key — a resubmit after a reconnect is a new request id, so nothing
deduplicates it. #517.

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

**Missing:** `git_commit` and `git_push` are not keyed. Both have partial natural
protection — a repeat commit of unchanged paths fails, and a repeat push is a
no-op — but "partial natural protection" is not the gate's zero. Provider calls
and tool invocations are unkeyed. Durable replay across processes is #517.

### 5. Orphan processes: 0 — Partial

*This entry was corrected during the audit. The first draft said "not started",
which was wrong — checking rather than trusting the note found the mechanism
already in place.*

`opaihub/process_tree.py` implements per-platform process-group isolation and
full-tree termination, and it is wired into the real provider path:
`accounts.py` launches CLIs with `isolated_group_kwargs()` and stops them with
`terminate_tree()`. `test_process_tree.py` (11 tests) covers the Windows and
POSIX strategies, escalation from terminate to hard kill, idempotency, an
already-exited child, a failing tree-killer, and a process with no pid.

**Missing:** those tests state plainly that *"no real processes are spawned: a
fake process object stands in for the tree, and the platform kill strategy is
injected"*. The gate asks for something stronger — child processes, browser
workers and temporary servers **reaped on Windows and POSIX fixtures**, with
zero orphans proven after cancel, timeout, app exit, provider crash and retry.
A unit test with an injected killer proves the logic; it cannot prove the
operating system actually reaped anything.

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
one a sibling window owns.

**Missing:** nothing yet *acts* on a stale lease, and there are no kill-and-resume
fixtures at each lifecycle phase. Deciding what to do with abandoned work needs
replay guarantees from #517 to be safe.

### 9. Cancellation truth — Partial

Immediate acknowledgement is evidenced: `stop()` moves to `cancel_requested`
("Stopping…"), control returns to the user at once, and `cancelled` is claimed
only when the backend confirms the worker returned — `cancel-teardown.spec.js`
(7 tests), plus twelve updated regressions.

**Missing:** bounded teardown is a 10s client-side timer, not a platform SLO, and
an unconfirmed teardown reports honestly but does not raise `needs_attention`
with evidence. `needs_attention` is not a canonical state yet because nothing
produces it.

### 10. Provider conformance: 100% — Not started

There is no adapter conformance matrix. #520.

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

### 14. Receipt completeness — Partial

Receipts carry route, cost, changed files and verdict, and `message_contract`
now records the lane and its reason. Approvals, residual risk and full
verification evidence are not consistently present. #516 / #528.

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
