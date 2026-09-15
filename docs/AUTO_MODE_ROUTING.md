# Vesta Auto Mode — capability routing, fallback & cost policy

Auto mode is Vesta's "just pick the right model and make it work" path. A user
selects **Auto**, sends a message, and Vesta chooses — from every model the user
has actually configured — the cheapest one that can do the job, escalating only
when it must, and asking for confirmation only when a call is genuinely paid,
destructive, or irreversible.

This document records what was broken, the routing policy that replaced it, the
fallback behaviour, the cost/confirmation decisions, and the tests that prove
it.

## Root causes (what was actually broken)

Reading the real implementation (`vestahub/gui_pipeline.py`,
`vestahub/free_models.py`, `vesta/app_state.py`) surfaced five concrete defects,
not vague "unreliability":

1. **First-in-list selection, not capability selection.** Auto's fallback
   picked `free[0]` — the first available free model in declaration order
   (`_fallback_model()` returned `free[0]`). Because Kimi was declared first and
   Gemini second, whichever happened to have a key first was picked every time.
   There was no scoring by task, reliability, cost, or recency, so one provider
   dominated ("Gemini appears too often").

2. **Dead-end on the first failure.** When a free provider returned no answer
   (the reported *"The Kimi free-tier API returned no answer… set
   MOONSHOT_API_KEY"*), a runner error, an auth error, or a rate limit, the
   pipeline **rendered that error and stopped**. There was no automatic move to
   the next capable model — the user had to re-prompt or switch model by hand.

3. **Confirmation on routine work.** A plain Auto question went
   local → `no_local_model` → a `needs_auto_confirmation` card *before any model
   ran*, and an explicitly picked free model always returned
   `confirmation_required` unless `allow_cloud` was set. Normal, safe requests
   cost a round-trip and tokens for a confirmation the user did not need.

4. **`allow_cloud`-gated resolution.** Auto only resolved to a concrete model
   `if selected_model == "auto" and allow_cloud`. With `allow_cloud` false (the
   default first send) Auto never chose a model at all — it silently fell
   through to local-only, which is why messages sometimes "did not run".

5. **No reliability memory.** Nothing tracked which providers had just failed,
   so a broken provider was retried first on the very next turn.

## Routing policy

Auto now builds an **ordered fallback chain** from the live catalog
(`vestahub/auto_router.py :: resolve_auto_chain`). The order, in one testable
place:

1. **Local first** — a live-detection sentinel (`"auto"`) always leads. A local
   model is free and private, so Auto tries one before anything leaves the
   device.
2. **Configured free APIs** — the cheapest cloud tier. Ranked *within* the group
   by (a) not-in-cooldown, (b) lowest recent-failure penalty, (c)
   least-recently-used, (d) stable id. LRU is what stops Auto from hammering
   whichever provider is first — equally-healthy providers rotate.
3. **Connected paid accounts** — ranked the same way, at the tail because they
   cost real money.

Selection inputs the chain reflects, per the requirements: task type
(`classify_task`), provider availability + authentication (catalog `available`
flag and connection health), reliability and recent failures
(`provider_reliability`), cost bucket (local → free → paid), and — through the
local→free→paid ordering plus runtime fallback — the *cheapest capable* model
for the job. Nothing is hardcoded to a specific model; Kimi/Gemini/Groq/Claude
are just whatever the user configured.

## Fallback behaviour

The pipeline dispatch loop (`handle_gui_message`) walks the chain. A candidate's
result is classified as **retryable** or **terminal**:

| Situation | Classified | Auto does |
|---|---|---|
| Empty answer ("no answer"), runner error, model unavailable, auth error, rate limit, quota, timeout, provider unavailable, capability mismatch, no local model | **retryable** | records the failure, deprioritizes the provider, advances to the next candidate — **no re-prompt** |
| A genuine answer, a command/edit-approval request, a usage-limit prompt, a user cancel, a risky-command block | **terminal** | returns immediately |
| Next candidate is a **paid** account and paid isn't authorized | — | returns **one** `needs_auto_confirmation` card naming the exact paid model |
| Chain exhausted with nothing runnable | — | returns a single honest, actionable error |

Context is preserved across switches: the same `message`, task packet,
capability contract, mode, and repo context are reused for every candidate —
only the model id changes. Paid calls are **never** duplicated during fallback:
a paid account is offered behind a confirmation and only invoked once the user
confirms (which converts the turn into an explicit `account:` request), and the
no-answer detection runs **before** any route/cost is recorded, so a failed
attempt never bills or inflates the savings ledger.

## Cost & confirmation decisions

- **Choosing Auto is consent to run the cheapest capable model.** A free or
  local model that Auto picked for itself runs **without** a confirmation card.
- **Paid escalation still confirms.** The first paid call under Auto returns a
  card naming the exact model and its cost implication — real money is never
  spent silently. This is the *only* confirmation Auto adds for normal work.
- **Explicit free-model selection is unchanged.** If a user hand-picks a free
  model (not Auto), the existing one-time free-tier consent still applies.
- **Destructive / risky requests are unchanged.** Safe-Auto's risky-command
  block and the destructive-action gate still fire; Auto does not weaken them.
- **Reliability memory is local and secret-free.** `provider_reliability`
  stores only provider ids, small rolling outcome counters, timestamps, and a
  reason drawn from a closed vocabulary — never prompts, never raw error text.
  A failing provider is *deprioritized*, never refused: if it is the only option
  left, Auto still uses it.

## Conversational messages ("hi") are answered, not failed

A separate but closely-related defect made free models *look* broken on a
trivial message: with a **Build** focus (or Full Auto), a signal-less message
like `hi` was classified as an **implement** task. The model replied "Hello!",
edited nothing, and the honesty invariant ("an edit run that changed nothing is
not complete") correctly but unhelpfully marked the run **failed**
(`stuck_no_progress`).

Fix: `agent_policy.is_smalltalk_request()` detects a message that is *entirely* a
greeting/pleasantry and routes it to **Explain** (a direct chat answer),
overriding the focus hint. It full-matches the whole message, so
`hi, can you fix the login bug` stays an implement task — only a pure `hi` /
`thanks` / `hey there` becomes chat. This pairs with the fallback chain: the
same `hi` on a suspended/rate-limited provider (e.g. a Moonshot account out of
balance) still fails over to the next configured model instead of dead-ending.

## Credit balances: out-of-credit tools are excluded, not deprioritized

`vestahub.provider_balance` keeps a local, secret-free record of how much
credit each provider has left, from three honest sources (in trust order):

1. **provider** — a live balance API (Moonshot/Kimi exposes one; the registry
   makes adding more a one-entry change),
2. **observed** — a real call was refused for insufficient balance/quota
   (Moonshot's "suspended due to insufficient balance" HTTP 429 is now
   classified `PROVIDER_QUOTA_EXHAUSTED`, not a transient rate limit), or a
   call succeeded, which proves credit exists and clears the flag,
3. **manual** — the amount the user typed in Settings › Credits & Balance for
   subscription tools with no balance API (Claude/Codex/Copilot).

Unlike the reliability cooldown (a heuristic that only *deprioritizes*),
exhaustion is observed fact: an out-of-credit provider **cannot** answer, so
`resolve_auto_chain` excludes it outright, the model picker removes its models
(with a note explaining why and a fall-back of the selection to Auto), and the
Settings page shows the state with a recharge hint. The verdict expires after
`EXHAUSTED_TTL_SECONDS` (6 h) and is cleared by any successful call, a manual
balance above zero, or a live probe showing credit — so a recharge made
outside Vesta is rediscovered automatically.

## Diagnostics

`auto_router.routing_diagnostics()` returns a compact, secret-free view of how
Auto ordered its candidates (task type, chain, per-provider reliability, and
`skipped_out_of_credit` — the providers excluded for having no credit) for
internal troubleshooting — it is not surfaced as noise to normal users.

## Tests

Deterministic, provider-free (mocked bridge/catalog) coverage:

- `tests/test_auto_router.py` — chain is local→free→paid; unavailable free and
  failed-connection accounts excluded; recent failure deprioritizes within a
  bucket; LRU rotates equal providers; retryable/terminal partition;
  reliability penalty grows and decays; cooldown clears after a success; the
  sentinel/empty providers are never recorded; **no secret is ever persisted**.
- `tests/test_reliable_ai_controls.py::AutoFallbackTests` — Auto runs a
  configured free model **without confirmation**; falls back to the next free
  model on a no-answer (the Kimi symptom) without re-prompting; escalates to a
  paid account **with** confirmation once free is exhausted; returns one honest
  error under total unavailability; and (unchanged) skips accounts with a known
  failed connection and reaching-a-soft-limit still confirms.
- `tests/test_message_contract.py` — the local/Auto branch contract, including
  the "nothing configured → guide to account" path pinned to an empty catalog.

Run: `python -m pytest tests/test_auto_router.py tests/test_reliable_ai_controls.py tests/test_message_contract.py tests/test_pipeline_routing_and_safety.py -q`
