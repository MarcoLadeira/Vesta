# Lifecycle and Result Truth Design (#612, #618)

## Goal

Make one versioned, provider-neutral lifecycle schema the only editable source
of transition, terminality, cancellation, exit-code, presentation, and legacy
compatibility semantics. Make every supported terminal payload expose one
validated `RunResult` projection derived from that lifecycle and its evidence.

## Scope and boundaries

`vestahub/lifecycle_schema.json` is the editable source. A deterministic,
standard-library generator reads it and produces four reviewed projections:

- `vestahub/generated_lifecycle.py` for Python runtime/state/CLI users;
- `vesta/assets/web/generated-lifecycle.js` for browser consumers;
- `vestahub/data/lifecycle-fixtures.json` for CLI, background, and
  cross-language golden vectors;
- `docs/lifecycle-schema.md` as the human-readable reference.

The generator has a `--check` mode. Generated-file headers identify the source
and generator, and CI invokes the check before formatting and tests. No runtime
loads or executes the generator.

`vestahub/run_state.py` remains the stable public API but becomes a thin adapter
over the generated Python projection. It records illegal edges with a durable
diagnostic sink supplied by callers; in-memory diagnostics remain developer
visibility only. The browser message reducer reads the generated browser graph
and may retain presentation aliases, but aliases can only project to canonical
states and may not define a canonical edge.

`vestahub/run_result.py` defines the versioned, immutable `RunResult` envelope.
It has typed lifecycle, provider, retry/recovery, verification, delivery,
economic, authority, diagnostics, and presentation fields. Construction rejects
impossible terminal/evidence combinations (for example, completed without
reconciliation, completion with failed verification for a mutating run, or a
retry-safe unknown reason). It serializes deterministically and accepts only the
current or immediately previous schema version; unknown versions become a
typed `incompatible` degradation rather than success or generic failure.

Existing `status`, `completion_state`, and background status strings are read
by explicit migration adapters. They are carried as output-only compatibility
fields and cannot decide transitions, retries, or completed truth once a
`RunResult` is present. Each adapter emits an in-process telemetry counter; a
single migration map documents source, target, and removal gate.

## State semantics

The canonical graph includes the existing active states plus `awaiting_input`,
`cancel_requested`, and terminal `needs_attention`. `cancel_requested` remains
non-terminal. `cancelled` is terminal only when reconciliation is reported
complete. Repair is explicitly the generated `verifying -> running` edge with
reason class `verification_repair`; it is legal in every generated projection.
Terminal states have no outgoing edges.

Every generated transition vector carries source, target, reason class,
attempt-creation flag, cancellation eligibility, verification and delivery
guards. The Python and browser reducers consume the same vectors. Duplicate,
stale, and terminal-mutating events preserve the prior state and create an
illegal-transition diagnostic event.

## Result adoption

The local ask, explicit model, background runner, GUI pipeline, CLI renderer,
receipt payload, and browser status mapper each consume or attach the same
serialized `RunResult`. Existing user-facing output remains compatible, but
the canonical envelope determines terminal state, reason, recovery action, and
presentation category. `RunResult` references evidence rather than copying a
mutable provider snapshot.

## Testing and validation

- Unit tests cover every schema edge, reason requirements, terminal
  immutability, cancellation, repair, and result invariants.
- Golden fixture tests compare the generated Python and browser transition
  reducers and the CLI/background/result projections byte-for-byte.
- A deterministic 10,000-sequence property-style test attacks duplication,
  reordering, stale revisions, and terminal mutations.
- Integration tests exercise verification repair and background result
  conversion, including cancel/complete conflict handling.
- Generator drift, architecture boundary, and compatibility tests enforce the
  source-of-truth and migration policy.

## Compatibility and removal plan

Schema version 1 is current; version 0 is accepted only through the explicit
legacy import adapter. Readers of unknown future versions get an incompatible
degraded `RunResult` and no automatic retry. Legacy status telemetry is exposed
by `legacy_status_usage()` and removal is permitted only after production
telemetry reports zero authoritative reads and writes for one supported release
cycle. Legacy strings stay at input/output boundaries until that gate is met.
