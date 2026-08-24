# One transactional runtime journal (SQLite WAL)

- **Status:** proposed — the store exists, nothing reads from it as authority yet
- **Issue:** #613 (child of #611; canonical parent #517)
- **Date:** 2026-08-23

## Context

OPai has good persistence *primitives* — atomic replacement, interprocess locks,
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
| store: schema, typed API, fencing, integrity | in review |
| projections: deterministic rebuild + property tests | in review |
| 3 — vertical slice | not started |
| 4 — dual-read qualification | not started |
| 5 — canonical reads | not started |
| 6 — operation migration | not started |
| 7 — legacy retirement | not started |

Also not started: the crash matrix, multiprocess integration tests, fault
injection, and performance numbers. #613 requires all four as evidence.

## Open questions

1. **Windows least-privilege.** The database file needs ACLs; `chmod` does
   nothing there.
2. **Retention split.** Functional requirement 5 asks for audit-critical vs
   high-volume presentation events to have separate retention. Specified, not
   implemented.
3. **Privacy enforcement.** The schema carries `privacy_class` on events and
   artifacts, but nothing yet enforces #527's minimisation rules against it.
4. **Backup and recovery.** Requirement 12 asks for these in doctor/preflight;
   `store_health` reports integrity but there is no backup path yet.
