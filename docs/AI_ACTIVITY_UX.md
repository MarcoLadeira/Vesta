# AI Activity UX

How OPai's web UI shows what the AI is doing, keeps slow models from looking
frozen, and stays honest about what actually happened.

## The event model

Activity events are created in **`opai/activity.py`** (`make_event`) and mirror
the requested shape:

```python
{ "id", "type", "status", "title", "detail", "timestamp", "durationMs", "metadata" }
```

- **statuses**: `pending · running · success · warning · error · cancelled`
- **types**: `request_prepare · context_read · model_selected · provider_request
  · waiting_first_token · streaming · tool_call · file_read · file_edit ·
  command_run · command_complete · ci_watch · validation · retry · completion ·
  cancelled · error`

The pipeline (`opaihub/gui_pipeline.py`) emits the **real** pre-call stages
(`request_prepare` → `context_read` with the routing-step count →
`model_selected` → `provider_request`). For **claude** streaming, the runner
parses `--output-format stream-json` and emits real `file_read` / `file_edit` /
`command_run` events from `tool_use` blocks plus `streaming` text deltas. On the
wire, each event is delivered to the front-end over the bridge's `activity`
signal, tagged with the request id.

## Where it shows

- **In-message status bar** (the pending assistant bubble): model · current
  stage · elapsed `mm:ss` · **Stop**. Built from `stage_message()`.
- **Activity timeline** (`Show activity` toggle): one row per event with a
  status glyph (`◐` running, `✓` success, `!` warning, `✗` error, `⊘`
  cancelled), title, and optional detail (`role="log"`, `aria-label="AI
  activity"`).
- **Streaming body**: claude text deltas append live; final markdown is rendered
  on completion.
- **Message metadata footer**: model · duration · cost/savings.
- **Global "AI working" pulse** on the brand dot while a request is active.

## Slow-model UX rules (`stage_message`)

Elapsed-time thresholds keep a slow model (Opus) visibly alive:

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
context) to those statuses.

## No fake completion

- "Completed" is emitted only when the pipeline actually returns an answered
  status; "Stopped by you" only when the user cancels.
- A cancelled request keeps any partial text and is styled calmly, not as an
  error.
- The UI can never get stuck loading: the elapsed timer and stage copy always
  advance, Stop is always live, and every terminal state (answered / stopped /
  error) replaces the pending bubble.

## Future agent-action events (scaffolded)

The type set already includes `ci_watch`, `validation`, `command_complete`, and
generic `tool_call`. The front-end renders any event type generically, so when
the backend gains richer agent execution (open/merge PR, watch CI, run tests as
discrete steps) it only needs to emit those events — no UI change. Streaming for
**codex/copilot** and true local-model streaming are tracked as backlog issues
(see the PR description); today claude streams richly, codex/copilot stream text
and are fully cancellable.
