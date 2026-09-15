# Provider Connection Doctor and Guided Sign-In Design

## Scope

Close #166 and #167 as one provider-auth vertical slice. Vesta will aggregate existing secret-safe account/API diagnostics into a compact Settings surface and offer an explicit guided sign-in action from both Settings and auth error cards.

## Diagnostics service

`vestahub.accounts.provider_connection_doctor` returns normalized entries for Claude, Codex, Copilot, Gemini, Groq, and Mistral. Account entries combine detected CLI/sign-in state, the latest safe connection result, ignored environment-variable names, CLI version/install state, and recovery actions. API entries use `credential_statuses()` and expose only source labels and environment-variable names. No credential value, credential-file content, or raw provider output enters the payload.

Connection checks retain a bounded in-memory safe summary so Settings can show the last check/error without launching new auth probes. CLI versions use a short, cached local `--version` command on a worker thread; this is installation metadata, not an auth or model request and never blocks the GUI thread.

## Guided sign-in

`interactive_provider_login` accepts only the registered account-provider IDs and fixed login argv: `claude auth login`, `codex login`, or `copilot login`. It resolves the installed CLI, applies `provider_child_env`, and launches a visible terminal without a shell. Windows uses `CREATE_NEW_CONSOLE`; Linux uses an allowlisted terminal emulator. Unsupported platforms or missing CLIs return structured failures.

The web bridge runs login on a cancellable worker thread and emits a request-correlated completion signal. Closing Vesta terminates the login child cleanly. After the child exits, Vesta performs the existing forced connection check. A verified/detected successful result updates Settings; when initiated from an auth error card, it retries exactly the stored failed request once unless a newer conversation request has superseded it. Failed, cancelled, stale, or unverified login never retries.

## UI

Settings replaces the shallow account rows with a compact Connection Doctor section. Each card shows health, credential source, CLI version/install state, last check, ignored override names, safe diagnostic/error text, and relevant Test/Sign in/Disconnect/Repair actions. Existing free-provider key controls remain available.

Auth error cards add a provider-labelled Sign in button for `AUTH_MISSING`, `AUTH_INVALID`, and `AUTH_EXPIRED`. The visible terminal is the deliberate exception to Vesta's normal hidden-process behavior, and the button clearly communicates that it opens an external sign-in flow.

## Safety and tests

- Provider IDs and login commands are allowlisted; no user-built command or shell is used.
- Child environments are sanitized and only removed variable names are returned.
- Credential files and values are never read by the new flow.
- Login is initiated only by an explicit button click.
- Unit tests cover aggregation, redaction, command mapping, environment sanitation, completion probing, and failure states.
- Playwright covers doctor rendering, Settings sign-in, error-card sign-in, correlation, and one-time retry behavior.
