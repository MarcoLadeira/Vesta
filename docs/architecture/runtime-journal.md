# One transactional runtime journal (SQLite WAL)

- **Status:** proposed — the store is written from the live run path; nothing reads from it as authority yet
- **Issue:** #613 (child of #611; canonical parent #517)
- **Date:** 2026-08-23

## Context

Vesta has good persistence *primitives* — atomic replacement, interprocess locks,
versioned state — and no single ordered history. Trust-critical state lives in
per-subsystem JSON and JSONL files, each individually plausible.

A crash between provider execution, repository mutation, cost recording and
history persistence therefore leaves several truths that are each defensible
and mutually contradictory. Nothing in the system can say which one happened.

Stage 1 inventoried the durable writers and forced each into a classification.
Stage 2 gave every `JOURNAL_OWNED` record a shadow journal *and* a runtime
comparator, which proved each record can be rebuilt independently. But those
shadows are per-record JSONL files. They cannot provide:

- a sequence that orders events *across* records;
- operation identity that survives a retry;
- fencing that stops a stale supervisor writing after takeover;
- an integrity verdict for the history as a whole.

## Decision

Use one SQLite database in WAL mode as the transactional runtime journal, with
the schema #613 specifies, reached only through a typed API in
`opaihub/journal_store.py`.

### Why SQLite WAL

- **Concurrent reads do not block the writer.** A GUI and a CLI rehydrate while
  a worker commits critical state — that is the actual shape of the problem.
  Under WAL this is a property of the engine, not something layered on top.
- **Real transactions.** Appending an event and advancing an operation must be
  one atomic act; no arrangement of separate files gives that.
- **Available wherever Python is.** No new runtime dependency, which matters
  because #613's non-goals rule out taking on a hosted database.
- **Failure tooling exists.** `PRAGMA quick_check`, `foreign_key_check`, and a
  documented corruption model.

### Why not more JSONL

Stage 2's JSONL shadows stay — they are how each legacy record is compared
against the journal during Stage 4 qualification. But they cannot be the
destination: ordering across files requires a shared sequence, and giving a
pile of append-only files one is reinventing a database badly.

## Design choices, and why the obvious alternative is worse

Each of these is enforced by a test that fails if the choice is reverted.

| Choice | Why not the obvious thing |
|---|---|
| `AUTOINCREMENT` sequences | `MAX(seq)+1` **reuses** a sequence after a delete, making two different events indistinguishable in replay |
| `BEGIN IMMEDIATE` | `DEFERRED` lets two writers both begin, then fails one at its first write *after* it has done work |
| newer schema ⇒ `incompatible` | Collapsing it into `corrupt` sends the user to recovery when they need an upgrade |
| unreadable payload ⇒ `degraded` + first bad sequence | A silently short read looks like a complete short history |
| `read_events` returns `readable=False` rows | Omitting them turns a gap into a plausible-looking shorter history |
| projection rebuild **stops** at corruption | Folding past it yields a state that looks complete and never happened |
| one cost row per operation (`UNIQUE`) | Double attribution becomes an insert error, not an audit finding months later |
| `str(exc)` wrapped in `redact` | Integrity details reach doctor output and support bundles (#622 AC9) |

## Consequences

**Accepted:**

- One more file format in the state directory during migration. Mitigated by
  Stage 7 retiring legacy authority only after telemetry shows zero use.
- `chmod 0o600` is a no-op on Windows. Said at the call site rather than
  implied — least-privilege there needs ACLs and is **not** done.
- `redact` scrubs secrets, not filesystem paths, so a corrupt-database detail
  can still name the file it failed to open. That is the boundary the codebase
  has agreed on, not a claim the string is safe anywhere.

## Status

| Stage | State |
|---|---|
| 1 — inventory + enforced classification | done |
| 2 — shadow write + dual read, every entry | done (`2b0379d`) |
| store: schema, typed API, fencing, integrity | done |
| projections: deterministic rebuild + property tests | done |
| 3 — vertical slice | admission, terminal, cost and verification written from the live path |
| 4 — dual-read qualification | comparator built; corpus supplied by `journal_background` |
| 5 — canonical reads | `JournalReader` serves migrated runs, falls back otherwise |
| 6 — operation migration | mirrored at the `idempotency` choke point |
| 7 — legacy retirement | gate implemented and reachable; opens on real evidence |

Stages 4-7 were correct and unreachable for a while, which is worth recording
because it is the failure mode this kind of migration invites. The reader and
the retirement gate were fully built and fully tested, and nothing in the
application called either: `journal_retirement` had no importers at all. Worse,
the population being journalled had no legacy counterpart -- `gui_pipeline`
journals a GUI turn keyed by `turn_id`, which is minted per call and persisted
nowhere -- so no amount of correctness in the comparator could have produced a
comparison.

`background_runs` is the record that closes it: durable, enumerable, keyed by
run id. `journal_background.legacy_runs` assembles it, `_save_run` mirrors it,
and doctor reports the resulting verdict. A migration is only observable once
both halves describe the same population.

Required tests:

| Category | State |
|---|---|
| unit | done |
| crash matrix | done |
| property-based | done |
| fault injection | done, including the backup case |
| multiprocess integration | done |
| performance | done |

Measured on a developer machine, recorded here because #613 asks for the
numbers as evidence rather than as thresholds:

```
append    p50 1.02ms   p95 1.19ms   (n=300)
rebuild   2.06ms @500  4.86ms @1000  ratio 2.36 (linear)
startup   2.41ms @50   2.05ms @1000  (flat in history)
storage   451 B/event  (checkpointed)
```

The budgets in the test are far above these on purpose. They catch a regression
*in kind* — an accidental O(n²) rebuild, a per-append fsync storm, a dropped
index — not a slow afternoon on a shared runner. They are not a claim that the
store is fast.

## Backup and recovery (requirement 12)

`journal_backup` uses SQLite's online backup API rather than copying files. In
WAL mode the recent commits live in the `-wal`, so copying `journal.sqlite3`
alone produces a backup missing exactly the work most worth keeping — and one
that looks entirely valid. Every backup is reopened, integrity-checked and
hashed before it is declared; one that fails verification is deleted rather
than left to be found and trusted when there is nothing else left.

Restoring a backup taken against a different project drops its approvals. The
issue requires that a database copied from another user or device not
automatically grant authority, and an approval is exactly granted authority: a
person said yes, once, to a specific thing, here. Runs and costs survive the
restore — only authority is refused. Restore also backs up what it replaces,
so recovery is never the step that destroys the last copy.

Reachable as `vesta journal status | backup | backups | restore`.

## What did not finish

The issue opens by describing a run that "may appear active with no worker",
and says recovery "cannot know whether to resume, reconcile, block or request
attention". `unreconciled_operations` answered that for external effects from
Stage 6; `unterminated_runs` answers it for runs.

Both are deliberately *reports*. An unterminated run holding a lease is either
running now or was abandoned by a process that died, and this database cannot
tell those apart — a lease is released by `record_terminal`, not by a process
exiting. The row carries the owner and the heartbeat and stops; the caller can
check whether that process exists, and this store cannot. Two tests pin the
refusal: a live run and one killed with `os._exit` must look identical, and no
field may be named "orphaned", "dead" or "crashed".

Reachable as `vesta journal pending`, counted in `vesta journal status` and
doctor.

## Minimisation (requirement 9)

`privacy_class` existed on events from Stage 1 and meant nothing: no caller set
it, and every event was stored as `internal` whatever it held. Worse, nothing
redacted what went in. A task reading `fix my auth, the key is sk-ant-...` put
that key verbatim into `journal.sqlite3` — found by reading the raw file back
and searching its bytes, not by reading the code.

Minimisation now happens at the store boundary, where every event passes
through, rather than at the call sites that build payloads: one site that
forgot would write a secret to disk and nothing would ever say so. Strings are
redacted and bounded however deeply nested, keys included, and before the
payload is hashed — so `payload_hash` describes what is actually stored.

Two paths bypassed that and were found by tests reading the file rather than
the API: `runs.terminal_reason`, written by a direct UPDATE, and
`operations.external_ref`, which is usually a plain PR URL and occasionally a
signed one where the signature *is* the credential.

`privacy_class` is now declared per event type, defaulting to `sensitive`
rather than `internal`, so forgetting the table over-protects. The honest
limit: redaction catches shapes it recognises, and a long private paste
matching none of them survives it — truncation bounds that without pretending
to solve it.

## Open questions

1. **Stage 4 wants captured production traffic.** `journal_background` supplies
   a real corpus and a fully migrated project now qualifies against it with no
   differences, which is what made Stages 5-7 reachable. Stage 4 asks for
   *captured* traffic as well, and that still has not happened — synthetic
   agreement is evidence that the comparison works, not that the migration
   survives real histories.
2. **Restore cannot replace a journal another process holds open.** On Windows
   an open handle blocks the replace. The refusal is the safe outcome and it
   names the real cause (`journal_in_use`) rather than blaming the backup, but
   a restore still means closing Vesta first.
