# Vesta Local Execution: `vesta ask` (real savings, not estimates)

For a long time Vesta *planned* the cheap route but never ran it - the savings
were estimates. `vesta ask` closes that gap (open issue #13): it actually answers
cheap tasks with a **local model** and caches the result, so a real cloud call is
avoided every time.

```sh
vesta ask "summarize my uncommitted changes"
vesta ask "what tests cover the auth module?"
vesta ask "draft a commit message for these changes"
```

## How it works

1. **Classify** the task with Vesta's model-intelligence taxonomy.
2. **Result cache** - a *near-duplicate* task in the same repo state returns
   instantly for $0 (the model runs zero times). Matching is normalized
   (case/whitespace/punctuation), so "Summarize the diff" and "summarize  the
   DIFF!" share one cached answer.
3. **Run locally** on a compact, already-redacted evidence prompt (Vesta's
   context-slimming applies, so the local model sees a tiny prompt).
4. **Record** the avoided cloud call to the savings ledger (`source: ask`).

## Safety and privacy

- **Loopback/private only by default.** Vesta only talks to a local model on a
  loopback/private endpoint (reuses the #19 endpoint classifier). A public
  `LOCAL_MODEL_URL` is refused - Vesta never silently ships your prompt to a host.
- **Cloud is never auto-called.** If the recommended tier is cloud/paid, `vesta
  ask` returns `confirmation_required` instead of spending.
- **No dependencies, no telemetry.** Pure stdlib HTTP to your local server; the
  raw task is never stored (one-way cache key), answers stay on disk.

## Connect a local model

`vesta ask` finds a local model automatically from, in order:

1. `LOCAL_MODEL_URL` - any OpenAI-compatible local server (LM Studio, vLLM,
   llama.cpp server). Example: `http://127.0.0.1:1234/v1`.
2. `OLLAMA_HOST` or the default Ollama endpoint `http://127.0.0.1:11434`
   (`ollama serve`, then `ollama pull llama3.2`).

Check what Vesta sees:

```sh
vesta hub models discover-local
```

If no local model is running, `vesta ask` degrades gracefully with a setup hint -
it never falls back to a paid call on its own.
