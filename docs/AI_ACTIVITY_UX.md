# AI Activity UX — the Calm Stream spec

How Vesta's surfaces (web GUI and CLI) show what the AI is doing, keep slow
models from looking frozen, and stay honest about what actually happened —
without flooding the user with rows.

This document is the single source of truth for the **Calm Stream** redesign
(epics #215/#216). It deliberately changes tested contracts; every contract
change is enumerated at the end.

## Why Calm Stream

The v1 feed rendered **one permanent row per raw event**. A single answer
produced 4–6 pipeline preamble rows, a "Connected to Claude" row per `system`
line, a "Streaming response" row **per assistant chunk**, one row per tool
call, and two rows per Codex item (started + completed). Root cause: events
were minted with random ids (`make_event` → `new_id()`), so the front-end's
id-keyed `upsert` could never coalesce repeated states.

Calm Stream keeps the promise of `docs/PRODUCT_IDENTITY.md` — *"Every step
visible. Every dollar accounted."* — but makes *visible* mean *legible*:
every raw event is still captured and reachable on demand; the default view
shows calm, truthful, logical rows.

## The event model (schema v2)

Events are created in **`vesta/activity.py`** (`make_event`). Schema v2 extends
v1 with four optional fields; absent fields keep v1 behavior, so v2 is fully
backward compatible:

```python
{
  "id":         str,          # v2: DERIVED on the stream path, not random
  "type":       str,          # unchanged vocabulary (see below)
  "status":     str,          # pending · running · success · warning · error · cancelled
  "title":      str,
  "detail":     str | None,
  "timestamp":  int,          # ms since epoch, always a real clock reading
  "durationMs": int | None,   # always measured, never synthesized
  "metadata":   dict,
  # ---- v2 additions (all optional) ----
  "requestId":  str,          # ties the event to its request
  "phase":      str,          # machine-readable step name
  "channel":    "feed" | "status",  # where it renders (default: "feed")
  "group":      str | None,   # consecutive feed events sharing a group render as one collapsible row
}
```

- **statuses**: `pending · running · success · warning · error · cancelled`
- **types**: `request_prepare · context_read · model_selected ·
  provider_checking · provider_authenticated · provider_auth_failed ·
  provider_request · request_sending · waiting_first_token · streaming ·
  tool_call · file_read · file_edit · command_run · command_complete ·
  ci_watch · validation · retry · retrying · completion · completed · failed ·
  cancelled · error`

### Channel routing

- `channel: "feed"` (default) → a timeline row.
- `channel: "status"` → the **status strip only**, never a timeline row.
  Connection, auth, and model announcements are ambient state, not steps.

### Derived stable ids

`derived_id(request_id, phase)` returns `"{request_id}:{phase}"`. The stream
path uses a fixed vocabulary so repeated states **update one row in place**
(the front-end `upsert` keys on `id`):

| id | What it is | Lifecycle |
| --- | --- | --- |
| `{rid}:phase` | The single pipeline preamble row | `running`, detail advances in place ("Preparing request…" → "Read project context" → "Model: <name>" → "Checking connection…" → "Sending request…"), then `success`; on failure `error` naming the failing step; on cancel `cancelled` |
| `{rid}:connect` | Connection/auth confirmation | `channel:"status"`; fires **once per request** regardless of how many `system` lines arrive |
| `{rid}:model` | Selected model announcement | `channel:"status"` |
| `{rid}:stream` | The single live streaming row | `running`, detail updates with real elapsed/size; flips to `success` with measured `durationMs` on the `result` line |
| `{rid}:tool:{seq}` | One tool call (`seq` = per-request counter) | `running` → `success`/`error`; carries a `group` key `{rid}:g{n}` where `n` increments when the tool *type* changes, so consecutive same-type calls group |
| `{rid}:codex:{item_id}` | One Codex item | `item.started` and `item.completed` **reuse the same id** (one row, running → done, measured duration) |
| `{rid}:done` | Terminal completed/failed/cancelled + receipt | unchanged semantics |

The per-request state (tool counter, group counter, Codex `item_id → event_id`
map, one-shot connect guard, start timestamps) lives in an **`ActivitySession`**
object in `vesta/activity.py`, constructed once per request by the runner.
`parse_claude_line` / `parse_codex_line` stay available as module-level
wrappers during migration.

### Grouping rules

Grouping is **presentation, storage is truth**:

- The store keeps every individual raw event.
- A pure function (`groupRows` in `vesta/assets/web/activity.js`) folds
  **consecutive** feed events sharing a `group` key into one logical row:
  `{kind: "group", title: "Read 4 files", status: worst(children), children}`.
- Group status is worst-of-children (`error` > `warning` > `running` >
  `cancelled` > `success`). A group containing an `error` child auto-expands.
- Expanding a group reveals the real children with their real timestamps.
- A single-member group renders as a plain row.
- Interleaved groups (A A B A) never merge across the interruption.

## Where it shows

- **Status strip** (chat view, above the timeline): provider connection dot +
  name, model chip, live cost/size counter, elapsed. Driven **only** by
  `channel:"status"` events plus the token/receipt signals. Dot states:
  idle · connecting · connected · error · cancelled. Resets per request.
  Cost appears only when a real number exists (Claude `total_cost_usd` →
  actual; otherwise nothing or an honest `estimated` badge — never a fake $0).
- **In-message status bar** (the pending assistant bubble): model · current
  stage · elapsed `mm:ss` · **Stop**. Built from `stage_message()`.
- **Activity timeline** (`Show activity` toggle): **logical** rows — the
  phase row, the stream row, tool singles and groups — with a status glyph
  (`◐` running, `✓` success, `!` warning, `✗` error, `⊘` cancelled), title,
  optional detail (`role="log"`, `aria-label="AI activity"`). Rendering is
  keyed and incremental (per-row DOM patch, rAF-batched) — never a full
  innerHTML rewrite.
- **Streaming body**: text deltas append live; final markdown renders on
  completion.
- **Message metadata footer**: model · duration · cost/savings receipt.
- **Global "AI working" pulse** on the brand dot while a request is active.
- **CLI mirror** (`vesta/cli_stream.py`): same session, same ids. On a TTY the
  phase row renders as one rewriting line (`\r`); non-TTY prints sequential
  lines. Groups print as `├ Read file ×4`.

## Transport

Events are delivered over the bridge's `activityBatch` signal as JSON arrays
(`vesta/activity_batch.py` `ActivityBatcher`): worker threads append to a
lock-guarded buffer, a GUI-thread `QTimer` drains it into one payload every
~33 ms, and a force-flush at request end (reply/error/cancel) delivers the
tail so no event is lost or arrives after the answer. A 200-event turn costs
one signal per frame instead of 200 crossings. The legacy per-event
`activity` signal stays defined for the classic GUI until it retires (#138);
the web front-end consumes `activityBatch` and ingests it in one store pass
(`store.ingestBatch`). Every payload carries the `requestId`; the
stale-request guard (`should_apply` / `canApply`) drops a stale batch whole.

## Slow-model UX rules (`stage_message`)

Elapsed-time thresholds keep a slow model (Opus) visibly alive. Reassurance
copy integrates into the stream row and status strip — never extra feed rows:

| Elapsed | Stage | Reassurance |
| --- | --- | --- |
| `< 15s` | "Waiting for {model}" | — |
| `15–45s` | "Waiting for {model}" | "{model} is taking longer than usual…" + **Switch to a faster model** |
| `> 45s` | "Still working — {model}" | "Still working. You can keep waiting, stop, or switch…" |
| streaming | "Streaming response" | — (tokens are arriving) |

Stop stays available the whole time; the screen never goes blank.

## Error UX rules (`error_card`)

Every failure maps to a calm, actionable card — never a raw stack trace up
front. Each carries `title · what · next · actions` where actions are drawn
from `retry · switch_model · connect · edit · details`. Covered statuses:
`cancelled` (neutral, not scary), `account_timeout`, `account_error`,
`account_not_connected`, `needs_model`, `needs_confirmation`, `blocked`,
`blocked_panic`, `rate_limit`, `network`, `unauthorized`, `context_too_large`,
`runner_error`, `empty`. Raw detail is redacted and hidden behind **Show
details**. `classify_error()` maps common raw strings (429, 401, network,
context) to those statuses. Pipeline phase failures render through the same
card, naming the failing step.

## Honesty invariants (non-negotiable)

1. **No fake progress.** Detail text only changes when something real
   happened; no synthesized token counts, no simulated streaming for runners
   that don't stream.
2. **Real time only.** `timestamp` is always a clock reading;
   `durationMs` is always measured start→finish, never estimated.
3. **Cancel is truthful.** Cancel flips running rows/groups to `cancelled`;
   partial text is kept; cancelled styling is neutral.
4. **Stale guard unchanged.** A signal applies only if its `requestId`
   matches the active request.
5. **Receipts unchanged.** Money labeling follows the ledger rules: *spent*
   = provider-reported; *estimated* = model math; a paid call is never a
   "saving"; missing cost renders as absent, never $0.
6. **Every event accounted for.** Grouping/coalescing is presentation only;
   the store (and the ledger, for its event kinds) keeps the full record. Any
   truncation is explicit ("N earlier events truncated"), never silent.
7. **"Completed" only on completion.** Emitted only when the pipeline
   actually returns an answered status; "Stopped by you" only when the user
   cancels; every terminal state replaces the pending bubble.

## Test contracts (deliberately rewritten by Calm Stream)

| Contract | v1 | v2 |
| --- | --- | --- |
| `__tests__/e2e/activity-truth.spec.js` | `.tl-row` count == events emitted (one row per event) | **Accounting contract**: singles + Σ(expanded group children) == feed events emitted; no event invisible; no row without a backing event; status-channel events excluded from row counts; truncation marker accounts for truncated events |
| `__tests__/e2e/activity.spec.js` | coalescing asserted with hand-picked ids | coalescing asserted with **derived ids** produced by mock-bridge v2 (schema v2 + batch emission) |
| `tests/test_agent_activity_events.py` | id-reuse asserted for aci/github paths | extended to the stream path: phase row keeps one id across statuses; one stream id per request; Codex started/completed share an id; tool `group` keys assigned per type-run |
| `tests/test_cli_stream.py` | asserts a "Preparing request" line | asserts the single rewriting phase line (TTY) / sequential phase lines (non-TTY) |

Everything stays hermetic: canned stream-json fixtures and the mock bridge
only — tests never invoke paid CLIs.
