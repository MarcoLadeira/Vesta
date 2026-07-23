# Changelog

## 0.2.1 Alpha.1

### Desktop app

- Redesigned the **Settings** surface: a new Overview landing page (status
  stat-tiles, an honest needs-attention list, and quick controls), a grouped
  navigation rail with icons, and per-page hero copy with factual scope chips
  (App-wide / This project / Local only). Every number shown is bound to the
  real settings payload — no invented data.

## 0.2.0 Alpha.2

A security, reliability, and desktop-polish patch. Every change is local-first
and privacy-preserving by default; nothing new phones home.

### Release engineering

- Added a reproducible **release-candidate preflight** (`opai release preflight`):
  one deterministic readiness verdict over a clean tree, version/tag/changelog/
  license consistency, the test gate, required docs, and artifact checksums +
  signing — exit 0 only when the release is ready. A **dry-run** path proves
  publishing is disabled and the network is blocked (`opai release
  dry-run-proof`), and **rollback** restores the previous tested release without
  touching user state (`opai release rollback`). Sanitized preflight/rollback
  evidence is archived by a dedicated CI workflow. See `docs/RELEASE.md`.

### Desktop app

- Added a windowed `opai-gui` launcher (`[project.gui_scripts]`): on Windows a
  Start-menu/taskbar shortcut now opens the app with **no attached console
  window**. `opai gui` from a terminal is unchanged.
- The window, taskbar, and Alt-Tab now show the packaged OPai icon (with a
  Windows AppUserModelID so the app groups under its own icon, not `pythonw`).
- Replaced the last native `window.confirm` dialogs with styled, keyboard-
  operable in-app approval cards.

### Local-first onboarding

- Added `opai models onboard [--smoke]`: a guided readiness state machine
  (not installed → stopped → unreachable → incompatible → no model → ready) for
  Ollama and OpenAI-compatible runtimes. Every non-ready state returns a
  concrete, consent-gated next command as text — OPai never downloads a model,
  starts a service, or reaches a public host on its own.

### Security & privacy

- Raw prompts are no longer stored globally: chat history is redacted, hashed
  per workspace, and clearable from the sidebar.
- Locked down the web GUI surface: no remote-URL or clipboard access and a
  deny-by-default content-security policy; copy goes through a write-only bridge.
- MCP configurations now enforce path/write policies — filesystem access is
  read-only by default and `.git`, `.env`, secrets, and caches are blocked, with
  `validate_mcp_config` to check a config before use.
- Hardened git operations with reference validation and fail-closed secret
  scanning (a scan error blocks rather than silently passes).
- Added signed, verifiable savings receipts: `opai receipt verify <file>` reports
  `VERIFIED` / `CONTENT_VERIFIED` / `TAMPERED` from a portable content hash plus
  an HMAC signature.
- Safe Auto now fails closed when a native provider cannot enforce granular
  edit permissions; OPai never enables Copilot's unbounded all-tools bypass.
- Push and pull-request authority now requires an explicit current request and
  is no longer implied by an ordinary implementation task.

### Reliability

- **Auto mode is now capability-, cost-, and reliability-aware.** OPai Auto
  builds an ordered fallback chain across every configured model — local first,
  then the cheapest configured free APIs, then (with confirmation) connected
  paid accounts — ranked by recent reliability and least-recently-used so it no
  longer just picks whichever provider is first. If a provider returns no answer
  (the "Kimi free-tier API returned no answer" case), errors, is rate-limited,
  unauthenticated, or times out, Auto silently advances to the next capable
  model instead of dead-ending or making the user re-prompt. Free/local models
  Auto chose itself run without a confirmation card; only the first *paid* call
  still asks. A local `provider_reliability` memory (no prompts, no secrets)
  deprioritizes recently-failing providers. See `docs/AUTO_MODE_ROUTING.md`.
- **A bare greeting is answered, not failed.** A conversational message ("hi",
  "hello", "thanks") is now always treated as a direct chat answer, even under
  a Build focus or Full Auto. Previously the focus hint forced "hi" into an
  *implement* run, the model replied "Hello!", changed nothing, and the run was
  marked failed — so free models looked broken on a simple message. Greetings
  now route read-only and complete cleanly.
- **Stop** now genuinely cancels across account CLIs, free-tier API requests, and
  local generation, terminating the whole process tree (no orphaned children
  that keep spending after you quit or close the window).
- Every edit-capable run creates a recoverable checkpoint before it may touch
  files and finalizes with the resulting changes, so no edit route is unrecorded.
- Replaced the generic tool doctor with real per-tool health checks
  (installed / runnable / passed-on-this-project) so a stale or broken local tool
  is no longer reported as healthy.

### Cost & honesty

- Paid and cloud calls are never counted as savings; receipts carry explicit
  confidence labels.
- Capture claims are now honest — sessions are reported as measurable,
  pass-through, or unmeasured rather than implying every task is routed.
- Cut per-message context and ledger overhead on the send path.
- Route comparisons and model spend are now separate ledger concepts: one
  provider call creates one spend event, and budgets never add route estimates
  to actual spend.
- Local runners without bounded repository tools return an actionable
  capability mismatch for edit requests instead of a false success.

### Autonomy

- Full Auto must be explicitly pinned and acknowledged before it can run;
  persisted full-auto is reset until re-pinned. Manage it with
  `opai autonomy status | pin | unpin`.

### Developer workflow

- Leaner CI: pull requests run on Linux for fast, low-cost feedback while the
  full Windows and cross-OS wheel matrix runs on `main` and on manual dispatch.
- Added clean-install verification and a hermetic test suite that runs without
  network access or provider credentials.

### QA E2E fix bundle (issue #219)

A full end-to-end QA pass — "Solve GitHub issue #219" driven through the real
desktop GUI — surfaced 23 findings (F2–F24), including a fabricated completion
that closed the real issue. Fixes in this bundle
(`docs/QA_E2E_ISSUE219_2026-07-17.md`, resolution map in
`docs/QA_E2E_ISSUE219_RESOLUTION.md`):

- **Destructive-action gating:** `gh`/`git` mutations (`gh issue close|comment`,
  `gh pr`, `git push`, …) are classified destructive and require confirmation
  even in Full Auto — the pin dialog's promise is now kept (F22, F23).
- **Composer mode sync:** the composer run-mode reflects the real engine mode
  after a workspace switch, and the Pin-Full-Auto acknowledgement runs whenever
  the effective mode would change (F16, F4).
- **Intent mapping:** "solve/fix/implement" maps to an edit-capable task focus
  instead of read-only Explain (F18, F5).
- **Free-model agentic execution:** the free API path (Gemini/Groq/Mistral)
  executes tools instead of narrating them; free providers are also registered
  in `opai.model_registry` with honest capability metadata, pinned by a
  consistency test (F6, F2).
- **Honesty invariants:** no green "Completed/✓ Ran/successfully solved"
  without real output, a diff, or a test artifact; empty command output is
  surfaced as an error, not a success (F8, F11, F14, F19, F24).
- **Loop & spend guards:** recursive self-invocation (`opai route` from inside
  a task) is blocked, repeated identical failures stop the run, and paid spend
  cannot burn with zero progress (F12, F13, F15).
- **Branded executable (F3):** documented in `docs/BRANDED_EXECUTABLE.md` —
  why pip-installed launches show `pythonw.exe`, the existing native-artifact
  path, and the recommended signing track; packaging follow-up.

## 0.2.0 Alpha.1

- Added the public static launch funnel under `site/`, ready for Cloudflare
  Pages deployment.
- Added launch CTAs for Controlled Alpha, Founding Pro, Team Pilot, and opt-in
  benchmark proof reports.
- Switched the launch funnel to controlled alpha access: no public GitHub issue
  intake for paid users, team pilots, or benchmark proof reports.
- Added commercial access and IP protection guidance for private distribution.
- Promoted OPai to public alpha metadata (`0.2.0a1` package version,
  `v0.2.0-alpha.1` release tag).
- Documented the 30-day go-to-market plan, no-code payment loop, public usage
  signals, and release checklist.
- Kept CLI telemetry off by default; site analytics are limited to Cloudflare
  Web Analytics.

## 0.1.1 Pre-Alpha

- Severely reduced default token and credit usage:
  - `opai route` now returns compact evidence by default.
  - `opai route` has an even smaller AI-facing summary path; full evidence is opt-in.
  - `opai slim` writes AI-client ignore files and removes generated project bloat.
  - AI CLI launch wrappers print a one-line badge by default; welcome graphics are opt-in.
  - Release, deploy, publish, and security tasks start with local preflight instead of strong-model routing.
  - `op ask` no longer stores full prompts by default.
  - Default context cap lowered to 6,000 characters with a 12,000-character hard guard.
  - Default budgets lowered to `$0.50/day`, `$5/month`, and `$0.10` per-task soft limit.
- Added public repo community files, issue forms, discussion forms, support policy, PR template, and security policy.
- Updated installer docs around the single-command OPai install.
- Added OPai skills/model-routing MVP and optional disabled-by-default cost-saving tools.

## 0.1.0 Pre-Alpha

- Added OPai local-first AI hub CLI.
- Added `op` and `opai` command entry points.
- Added automatic project activation for AI coding clients.
- Added Superpowers bridge support through OPai activation.
- Added packaged hub registries, prompts, docs, MCP examples, and security policy.
- Added evidence router, safe command runner, security scans, and isolated install smoke test.
