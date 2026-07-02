# OPai Auth and Message Reliability Design

Date: 2026-07-01

Status: approved by the user's explicit instruction to continue autonomously

Scope: desktop web UI, account-backed CLI providers, local providers, message/activity state, connection settings, tests, and security documentation

## State of the Union

### Product flow

| Step | Current behaviour | File/code area | Risk |
| --- | --- | --- | --- |
| 1 | The browser creates a request ID, renders a pending assistant message, and calls the Qt bridge. | `opai/assets/web/app.js` | Request identity is sound, but UI status is split across ad-hoc variables. |
| 2 | The bridge composes the request and starts a worker with event, token, and cancel callbacks. | `opai/gui_web.py` | Exceptions are flattened to generic results and can lose typed failure information. |
| 3 | The pipeline reads preferences/context, chooses the selected route, and emits activity events. | `opaihub/gui_pipeline.py` | Raw model IDs and provider names are emitted into the normal timeline. |
| 4 | Account routes resolve a logged-in CLI from presence of an executable plus an auth file or environment signal. | `opaihub/accounts.py` | Presence is reported as connected even when credentials are expired or rejected. |
| 5 | The CLI process streams stdout; Claude JSONL is parsed, Codex uses an output file, and Copilot streams text. | `opaihub/accounts.py`, `opai/activity.py` | Streaming discards stderr and does not use the child exit code, so a provider failure can look successful. |
| 6 | `_ask_account` treats any non-empty returned text as `answered_by_account`. | `opai/app_state.py` | Authentication text can be promoted to an assistant answer. |
| 7 | The pipeline maps `answered_by_account` to `answered` and emits `Completed`. | `opaihub/gui_pipeline.py` | A failed provider call can end with a success event. |
| 8 | The browser finalizes the message from the backend status and renders raw result details. | `opai/assets/web/app.js` | No canonical deduplication or redaction boundary exists in the browser contract. |

### Authentication architecture

| Provider/tool | Auth method | Credential source | Current status | Risk |
| --- | --- | --- | --- | --- |
| Claude | Claude Code CLI account session; `claude auth status` is available locally. | Claude-managed files under the user's home; OPai checks presence only and never reads contents. | Executable and auth signals are present, but the observed request returned 401. | Invalid or expired credentials are labeled connected; CLI exit failures are not normalized. |
| OpenAI/Codex | Codex CLI login; `codex login status` is available locally. | Codex-managed auth file. | Executable and auth signal are present, but not health-checked by OPai. | Same presence-versus-validity gap. |
| GitHub Copilot | Copilot CLI login/keychain; no non-interactive status command is exposed by the installed CLI. | Copilot config/keychain or supported token environment variables. | Executable and auth signal are present but unverified. | OPai cannot claim verified connectivity without a real request. |
| Local providers | Local runner endpoint/process. | Local configuration; no cloud account credential. | Supported through `local_runner`. | Failures use a separate, coarser status vocabulary. |
| Mock provider | Browser/runner test doubles only. | Test fixture state. | Available in automated tests. | Contract drift between mocks and production can hide failures. |

There is no direct Anthropic/OpenAI HTTP API-key path in this UI flow. OPai launches the user's account CLI and that CLI owns request headers and tokens.

### Observed 401 diagnosis

- Auth artifacts are present, which is enough for current code to set `authenticated=true` and `connected=true`; validity is not checked.
- OPai does not pass an API key or bearer header to Claude. The selected `account:claude:haiku` route invokes the Claude CLI with `--model haiku`; the CLI supplies its own stored account credential.
- The provider's 401 therefore indicates that the CLI-managed credential was rejected (invalid, expired, revoked, or otherwise unusable), not that OPai formatted an HTTP header incorrectly.
- `AccountRunner.stream()` suppresses stderr and always returns `{text, cost}` after EOF, regardless of non-zero process exit. `_ask_account()` consequently promotes returned authentication text to `answered_by_account`, and `handle_gui_message()` emits `Completed`.
- There is no error-text deduplication at the provider or message boundary. Repeated provider output can become one repeated assistant answer, as seen in the screenshot.
- Exact live credential validation was intentionally not attempted because it could contact a cloud service; no secret file was opened and no credential value was printed.

### UX assessment

| Area | Score | Reason |
| --- | ---: | --- |
| Authentication clarity | 3/10 | Presence is presented as connected; invalid/expired/unverified are not distinguished. |
| Error message quality | 4/10 | Error-card scaffolding exists, but the observed 401 bypasses it and raw text can leak into chat. |
| Provider connection reliability | 3/10 | Child return code and stderr are ignored in the streaming path. |
| Message rendering correctness | 5/10 | Escaping, request IDs, cancellation, and empty-state handling exist; deduplication and canonical status do not. |
| Activity truthfulness | 3/10 | The pipeline can emit `Completed` after a provider process failed. |
| OPai branding consistency | 4/10 | Account model/provider labels lead in chat, composer, and timeline. |
| Recovery actions | 4/10 | Retry/switch exists, but Settings/reconnect/test/details are incomplete. |
| Fluidity vs ChatGPT/Claude/Cursor | 5/10 | Streaming, stop, and stale guards exist; failure/retry/scroll/connection workflows need one state model. |

## Approaches considered

1. UI-only cleanup: rename labels and special-case 401 in JavaScript. Low effort, but backend lies remain and other consumers receive incorrect results.
2. Backend-only normalization: classify CLI failures and correct terminal events. This fixes truthfulness but leaves fragmented recovery, settings, and branding.
3. End-to-end contract: normalize provider/auth/error results at the CLI boundary, carry them through the pipeline, and drive a single browser message/activity model. This is the selected approach because it fixes the root cause and gives tests a stable contract.

## Architecture

### Provider connection and error contract

Add a framework-free module for:

- connection states: `unknown`, `not_configured`, `connected`, `invalid`, `expired`, `disconnected`, `rate_limited`, `provider_unavailable`, `misconfigured`, and `checking`;
- application error codes such as `AUTH_MISSING`, `AUTH_INVALID`, `AUTH_EXPIRED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `NETWORK_ERROR`, `MODEL_UNAVAILABLE`, `CONTEXT_TOO_LARGE`, `STREAM_ABORTED`, `USER_CANCELLED`, and `UNKNOWN`;
- secret redaction, repeated-error collapse, safe technical details, OPai-first titles/messages, and recovery action IDs;
- user-facing and advanced display-name mapping.

The module returns plain dictionaries so existing Python/Qt/JSON boundaries remain simple.

### CLI boundary

Capture stdout and stderr concurrently, retain the child return code, and classify failures before returning from `AccountRunner`. Provider text may be diagnostic input but never becomes a successful assistant response after a non-zero exit or known auth error. Claude and Codex connection tests use their local status commands; Copilot is explicitly `unknown`/unverified when only presence can be established.

No connection test performs a model completion. Credentials stay owned by the provider CLI.

### Pipeline and activity

Use canonical terminal rules:

- only a non-empty valid assistant result emits `completed`;
- 401/auth failures emit `provider_auth_failed` with error status;
- network/provider failures emit `failed`;
- cancellation emits `cancelled` and ignores late signals;
- retries use a new request ID and emit `retrying`;
- empty output emits `failed`/`NO_RESPONSE` rather than a placeholder success.

Raw provider/model data is metadata for Inspector and technical details, not the primary title.

### Browser message model

Represent each request with one state from `queued`, `preparing`, `authenticating`, `sending`, `waiting`, `streaming`, `completed`, `failed`, `cancelled`, or `retrying`. The reducer rejects illegal terminal transitions, so `failed -> completed` cannot occur. Existing request-ID stale guards remain authoritative.

Chat always renders the assistant role as OPai. Model selectors use OPai mode labels; advanced provider/model information remains available in Inspector and Connections. Error cards consume the normalized backend object and provide Settings, reconnect, retry, and technical-details actions without repeating raw text.

### Connections UX

Extend Settings with connection cards containing provider, normalized status, credential source, last checked time, safe diagnostic, and context-sensitive actions. The bridge exposes read-only connection state plus explicit test/reconnect navigation actions. Unsupported health checks are labeled unverified, never connected by implication.

### Scroll and interaction

Maintain immediate pending feedback and Stop behavior. Auto-scroll only when the user is already near the bottom; preserve the reader's position after they scroll upward. Prevent duplicate active submits and keep retries isolated by request ID.

## Security

- Never read or return credential contents.
- Redact common key/token/bearer/cookie patterns in every technical message.
- Limit diagnostic size and omit command arguments that may contain prompts or secrets.
- Keep technical details collapsed and separate from normal chat text.
- Do not auto-run paid provider calls as health checks.
- Record dependency advisories and secret-scan results in `AUTH_SECURITY_REVIEW.md`; do not apply breaking dependency upgrades as part of this focused change.

## Testing

Follow red-green-refactor slices:

1. provider outcome normalization, redaction, deduplication, and display mapping;
2. child return-code/stderr handling and auth classification;
3. pipeline terminal-event truthfulness;
4. message reducer terminal transitions and stale response behavior;
5. invalid/missing/success/retry/branding/connection Playwright flows;
6. security scanners, full Python suite, Vitest, Playwright, and packaging/build checks.

Mocks model only provider boundaries; state and normalization logic are exercised as real code.

## Scope boundaries

- No direct API-key storage system will be invented because this product path delegates credentials to installed CLIs.
- No real paid/cloud completion will be made for testing.
- No changes will be made in the other agents' dirty worktree.
- The existing visual system will be extended, not redesigned wholesale.

## Self-review

The design has no placeholders. It distinguishes detected from verified connectivity, makes terminal truth a backend invariant, preserves provider detail for advanced surfaces, and limits this branch to the auth/message reliability path requested by the user.
