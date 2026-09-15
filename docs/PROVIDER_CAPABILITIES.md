# Provider capabilities + health-state machine (#168)

Provider knowledge used to be scattered — `ACCOUNT_SPECS` (accounts.py),
`FREE_MODEL_SPECS` (free_models.py), adapter kinds (provider_adapters.py), and
ad-hoc `authStatus` strings — so the picker, settings, Connection Doctor, and
router each reasoned about providers differently. Adding a provider meant editing
many files, and the UI could not uniformly answer *what can this provider do* or
*is it healthy right now*.

`opaihub/provider_capabilities.py` is the single source of truth those surfaces
read. It is the foundation of the provider-lifecycle epic (#295) and the natural
companion to the model registry (#170).

## One capability record — `ProviderProfile`

One record per provider, describing the **honest current** reality (not
aspirations):

| Capability | Meaning |
|---|---|
| `chat` | Can answer/converse |
| `code_execution` | Runs as a coding agent with its own tools |
| `repo_editing` | Can edit the repository through Vesta |
| `streaming` | Emits incremental output |
| `tool_calling` | Has native tool/function calling |

| Requirement | Meaning |
|---|---|
| `requires_api_key` / `requires_oauth` / `requires_cli` / `requires_git_repo` | What the provider needs before it can run |

Plus `supports_cancellation`. Two truths the picker must not hide:

- **Copilot cannot edit** through Vesta (its repo edits fail closed), so
  `repo_editing = False` for Copilot while `True` for Claude/Codex.
- **Local runtimes do not stream yet (#154)**, so `streaming = False` for
  ollama / openai-compatible rather than an aspirational `True`.

Profiles are derived from provider kind (account / free / local) and the existing
specs — **not** a second registry. `provider_profile(id)` returns one (raising on
unknown ids so a typo can't masquerade as capable); `all_provider_profiles()`
returns the serializable table.

## One health enum — `ProviderHealth`

```
not_installed → not_configured → configured → authenticated
                                     ↘ degraded / rate_limited / failed ↗
unknown  (nothing learned yet — never a synthesised "healthy")
```

`HEALTH_TRANSITIONS` defines the allowed moves: the ladder is not strictly linear
— a working provider can degrade, hit a rate limit, fail, or sign out, and any of
those can recover; `unknown` may resolve to anything; any state may re-observe
itself. `can_transition(a, b)` enforces it.

`canonical_health(...)` folds Vesta's existing signals into the enum: a missing
account CLI is `not_installed` (outranks a stale auth string), an explicit
rate-limit error code wins (authenticated-but-throttled), and everything else maps
from `authStatus`. Anything unrecognised is honestly `unknown`.

## One truth, every surface

`ProviderAdapter` grows `.profile` (the record) and `.health(status)` (pure over a
status dict — no hidden probes; `unknown` without one). The Connection Doctor
annotates every entry with `healthState` (canonical enum) and `capabilities`
(the profile), and `settings_payload` exposes the whole `providerProfiles` table.
The existing per-surface `health`/`authStatus` strings are left intact, so nothing
regresses while every surface gains one lifecycle truth to read.

## Not in scope

A single-flight active-session registry and orphan sweep (#169), async heavy GUI
bridge work (#146), and real local streaming (#154) build **on** this contract and
are tracked separately under epic #295.
