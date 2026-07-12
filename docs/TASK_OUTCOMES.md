# Task-outcome metrics (#288)

OPai's promise is *more useful work per token and per euro*. To prove it we need a
number that connects a user request to what it cost and whether it actually
finished — **cost per completed task** — plus honest evidence of duplicate model
calls avoided. Today spend, savings estimates, and execution state live in
separate records with no shared, queryable outcome key.

This note specifies the versioned **task-outcome record** and the reconciling
summary that turn those scattered facts into a single, privacy-preserving metric
surface reported identically by the GUI and CLI.

## Design principles

1. **Spend stays authoritative and single-sourced (#286).** The outcome record
   never *re-defines* spend. Cost per completed task uses the authoritative
   `model_call` spend sum as its numerator, so it reconciles exactly to the
   ledger and can never drift or double-count.
2. **Unknown is a first-class value.** Fields OPai has not yet measured (time to
   first useful result, selected-context size, cached tokens) are recorded as the
   literal string `"unknown"` — never synthesised, never a fake `0`. A true
   zero (no model call happened) is recorded as `0`, which is a fact, not a guess.
3. **At most one terminal outcome per task.** The record is keyed by the turn's
   request id and written idempotently: a second attempt for the same id returns
   the first record unchanged. A completed, failed, blocked, or cancelled task
   has exactly one terminal outcome.
4. **Privacy unchanged.** Records go through the same `record_event` redaction as
   every ledger entry: one-way `task_hash` only, no raw prompt, source, credential
   or personal data. Nothing leaves the machine.
5. **GUI and CLI report the same numbers.** Both surfaces call one function,
   `summarize_outcomes`, over the same append-only ledger.

## The record — `task_outcome` event (schema v1)

Written by `record_task_outcome` (in `opaihub/ledger.py`, alongside the other
recorders). On top of the standard `created_at` / `event_type` / `task_hash`:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | `1` — lets summaries evolve without breaking old records |
| `outcome_id` | str | Turn/request id; the idempotency key |
| `category` | str | Terminal class: `completed` \| `failed` \| `blocked` \| `cancelled` |
| `completion_state` | str | Finer pipeline state (`answered`, `read_only`, `cancelled_before_edit`, …) |
| `run_mode` | str | `ask` \| `plan` \| `safe-auto` \| `full-auto` \| `unknown` |
| `source` | str | Which surface produced the turn (all pipeline turns share the ledger) |
| `model_calls` | int | Provider calls this turn made (`0` when none) |
| `total_tokens` / `input_tokens` / `output_tokens` | int \| `"unknown"` | Tokens; `0` when no call, `"unknown"` when a call reported none |
| `tokens_measurement` | str | `provider` \| `estimated` \| `none` \| `unknown` |
| `attributed_cost_usd` | float \| `"unknown"` | Per-turn spend attributed from telemetry; `0.0` when no call, `"unknown"` when a paid call reported no dollar figure |
| `cost_measurement` | str | `actual` \| `derived` \| `estimated` \| `none` \| `unknown` |
| `cached_tokens` | int \| `"unknown"` | Tokens served from cache (`"unknown"` until measured) |
| `avoided_duplicate_calls` | int | Model calls this turn skipped via a cache hit |
| `selected_context_bytes` / `selected_context_tokens` | int \| `"unknown"` | Context OPai actually sent |
| `time_to_first_result_ms` | int \| `"unknown"` | Latency to first useful output |
| `recovered` | bool | Whether the turn recovered from a failure/retry |

The record captures what is *honestly known at the terminal boundary* of a turn.
Fields the pipeline does not yet thread through are `"unknown"` by design; the
schema is versioned so they can be filled in later without a migration.

## Emission — one per turn, at the pipeline chokepoint

Every surface (desktop GUI, `opai ask`/CLI stream, background runs, Build mode)
runs one turn through `handle_gui_message`, and every terminal path in that
function funnels through its inner `_decorate`. That is the single exactly-once
boundary. `_decorate` derives the terminal `category` from the turn's honest
`status`, reads the turn's `cost_telemetry` for tokens and dollars, and calls
`record_task_outcome` keyed by the turn id — wrapped so a telemetry hiccup can
never fail a user's turn.

**Only real terminal outcomes are recorded.** A genuine answer is `completed`; a
policy `blocked` turn is `blocked`; a `failed`/error turn is `failed`. Three cases
record **nothing**, preserving the ledger-honesty invariant that an untouched,
unspent turn leaves no trace:

- a **cancel before any work** happened (no model call, no changed files) — there
  is nothing to measure;
- a **`capability_mismatch`** (the chosen model can't do the job) — awaiting a
  different model, not a finished task; and
- an **awaiting-input** `needs_*` state — the task is not finished, so counting it
  as `completed` would inflate the denominator and understate cost per completed
  task.

A cancel that already spent or changed files *is* recorded as `cancelled`, because
it consumed real resources.

## The summary — `summarize_outcomes`

Reconciles outcomes against the authoritative ledger:

- **`by_category`** — counts of completed / failed / blocked / cancelled.
- **`spend.authoritative_estimated_usd`** — `sum(model_call.estimated_actual_usd)`,
  byte-identical to `summarize_ledger`'s `estimated_actual_spend_usd`.
- **`spend.cost_per_completed_task_usd`** — authoritative spend ÷ completed count,
  or `"unknown"` when nothing has completed yet (never a divide-by-zero, never a
  fabricated zero).
- **`duplicate_calls_avoided`** — proven from `cache_lookup` events whose
  `avoided_model_call` is true. Calls avoided are counted; token savings are
  **not** claimed unless measured.
- **distributions** — time-to-first-result and selected-context report `known`
  vs `unknown` counts with min/median/max over the known values only.
- **`reconciles_to_ledger`** — a self-check that the spend numerator equals the
  ledger's authoritative spend.

`opai outcomes [--json]` prints the summary; the GUI `taskOutcomes` bridge slot
returns the same dict, so the two surfaces are provably in parity.

## What this is (and isn't)

This is the **measurement foundation**: a truthful numerator and denominator for
the core differentiator, with unknowns labelled so baselines are honest before any
product target is set. It deliberately does *not* set targets, reward shallow
completion, or blend spend with guesses — task success stays user- and
test-grounded, and quality stays separate from cost.
