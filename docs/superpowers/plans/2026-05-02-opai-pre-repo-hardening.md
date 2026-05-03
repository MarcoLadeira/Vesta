# OPai 0.1.0 Pre-Alpha Pre-Repo Hardening Plan

Date: 2026-05-02

Goal: make OPai safe to publish as a one-command installable, local-first AI coding hub that materially improves coding speed and quality while minimizing paid model usage.

This plan is based on a live repository audit, local verification commands, isolated wheel install testing, registry validation, security scans, and packaging checks.

## North Star

OPai should feel like a quiet supercharger for any AI coding session:

1. The user installs it with one simple command.
2. OPai appears automatically in supported CLI/AI coding clients.
3. It gathers local evidence before any expensive model reasoning.
4. It routes tasks through deterministic tools, caches, project context, tests, and cheap/local models first.
5. It escalates to stronger AI only when the evidence says the cheap path is insufficient.
6. It never hides risky actions. Destructive commands, cloud spend, deploys, pushes, merges, and secret-bearing operations require confirmation.
7. It keeps getting smarter by storing reusable, redacted project summaries, workflow outcomes, health history, and cost telemetry.

## Audit Snapshot

Current repo state:

- Branch: `codex/opcoding-final-third`.
- OPai version: `0.1.0 pre-alpha`.
- Main packages: `opai`, `opaihub`, `opcoding`.
- Registries exist under `hub/registry`.
- Docs exist under `hub/docs` and `docs`.
- Tests exist under `tests`.
- Root package config exists in `pyproject.toml`.
- OPai global integration has been installed locally for Codex/Claude/Copilot-style shell wrappers.

Current capabilities found:

- CLI entry points: `opai`, `op`, `op-hub`.
- Hub registries: tools, agents, workflows, MCP servers, models.
- Cost files: `hub/cost/budget.yaml`, `hub/models/routing.yaml`, `hub/context/context_policy.yaml`.
- Security files: `hub/security/permissions.yaml`, `hub/security/risky_commands.yaml`, `hub/security/secrets_policy.md`.
- MCP example config: `hub/mcp/mcp_config.example.json`.
- OPai terminal UX: blue `Using OPai` badge, mascot asset, ANSI/ASCII/kitty/iTerm rendering paths, animated compact welcome.
- Global AI-client discovery files: OPai Codex skill, Claude memory block, shell wrapper scripts.
- Local tool registry includes useful free tools such as ruff, pyright, bandit, detect-secrets, gitleaks, markdownlint, actionlint, and local SQLite.

Verification that passed:

```powershell
python -m compileall opcoding opaihub opai tests
python -m unittest discover -s tests
python -m opaihub validate
python -m opai doctor
python -m opai integrate status
python -m opai hub analytics status
python -m opcoding tools . run ruff
python -m opcoding tools . run markdownlint
python -m opcoding tools . run detect-secrets
python -m opcoding tools . run gitleaks
python -m opcoding tools . run pyright
python -m opcoding tools . run actionlint
python -m pip wheel . --no-deps -w .opaihub\wheelhouse
python -m opai --help
python -m opai welcome --compact --animate --image ascii --frames 2 --delay 0
python -m opai hub discover tools
python -m opai hub sandbox check --command "git reset --hard"
python -m opai hub schedule list
```

Important pass/fail notes:

- Unit tests: 40 passing.
- Hub validation in source tree: passing.
- Wheel build: passing.
- Built wheel size: about 1.18 MB.
- Cost analytics: estimated spend was 0.0 USD and cloud models were disabled.
- Secret scans: no committed live secrets detected in the scanned workspace output.
- Sandbox policy check classified `git reset --hard` as confirmation-required.
- `actionlint` skipped because no `.github/workflows` directory exists yet.

Failures and warnings:

- `ruff-format` reported 42 files would be reformatted.
- `bandit` reported several low-risk findings and one high-risk `shell=True` finding.
- `pip-audit` is installed/known but not wired into `opcoding tools . run pip-audit`.
- `osv-scanner` returned "No package sources found"; OPai needs clearer dependency source files or a better skip reason.
- A clean wheel install from outside the source tree fails `python -m opaihub validate` because `hub/` data is not packaged.
- `rg.exe` failed with "Access is denied" in this environment; OPai should detect broken tool binaries instead of assuming installed means usable.
- The terminal animation prints raw ANSI when output is captured by non-interactive logs; OPai needs TTY/CI detection.

## P0 Release Blockers

These must be fixed before creating a public repository or telling users to install OPai.

### P0.1 Package The Hub Data Correctly

Problem:

- The built wheel includes `opai/assets/opai-mascot.png`.
- The built wheel does not include `hub/registry/*.yaml`, `hub/docs/*.md`, MCP examples, templates, prompts, or security configs.
- `opaihub.loader.hub_root()` falls back to `site-packages/hub`, but that directory does not exist in the wheel.
- Running `python -m opaihub validate` outside the source repo crashes with `FileNotFoundError`.

Fix:

1. Move distributable hub data into a package-owned location:

```text
opaihub/
  data/
    hub/
      registry/
      docs/
      mcp/
      prompts/
      security/
      cost/
      models/
      context/
      templates/
```

1. Keep root `hub/` as the editable source of truth during development, or generate/sync package data from root `hub/`.
1. Update `opaihub.loader.hub_root()` resolution order:

```text
1. explicit --project/--hub-root argument
2. OPAI_HUB_ROOT environment variable
3. nearest ./hub in current project parents
4. packaged opaihub/data/hub via importlib.resources
5. fail with a useful remediation message
```

1. Add `pyproject.toml` package data:

```toml
[tool.setuptools.package-data]
opai = ["assets/*"]
opaihub = ["data/hub/**/*"]
```

1. Add a test that installs the wheel into a temporary venv, changes cwd outside the repo, and runs:

```powershell
python -m opai version
python -m opaihub validate
python -m opai hub list-tools
python -m opai doctor
```

Definition of done:

- `python -m pip wheel . --no-deps -w .opaihub\wheelhouse` succeeds.
- Wheel contains hub registry/docs files.
- Clean venv outside the repo can run `opaihub validate`.
- `opai install --project <some-project>` can bootstrap from packaged defaults.

### P0.2 Replace String Shell Execution With A Policy-Gated Runner

Problem:

- `opcoding/utils.py` uses `subprocess.run(..., shell=True)` in shared `run_command`.
- `opaihub/health.py` uses shell string health checks with `shell=True`.
- `opai/cli.py` uses `shell=True` on Windows launch.
- `opcoding/integrate.py` uses process execution patterns that Bandit flags.

Why this matters:

- A hub that launches tools, workflows, agents, and project commands needs excellent command safety.
- Registry-provided command strings can become injection surfaces.
- OPai should be able to classify commands before execution, redact logs, and require confirmation for destructive operations.

Fix:

1. Add a shared command policy module:

```text
opaihub/command_policy.py
opaihub/command_runner.py
```

1. Represent commands as argv lists in registries where possible:

```yaml
health_check:
  command:
    - python
    - -m
    - ruff
    - --version
```

1. Keep string commands only for display or explicitly trusted shell adapters.
1. Use `subprocess.run(argv, shell=False)` by default.
1. Add command classification:

```text
safe: read-only local checks, version checks, lint/test commands
confirm: git reset, delete/move, install, network, package publish, deploy
deny: credential exfiltration patterns, recursive delete outside project, shell profile edits without install command
```

1. Enforce project path boundaries before recursive file operations.
1. Redact stdout/stderr before logging.
1. Add tests for:

- shell metacharacters are not interpreted.
- risky commands require confirmation.
- allowed commands run as argv.
- logs redact secrets.
- Windows paths with spaces work.

Definition of done:

- Bandit no longer reports high severity `shell=True`.
- Health checks run through the same policy.
- `opai hub sandbox check --command "git reset --hard"` and actual execution agree on risk classification.

### P0.3 Format The Codebase And Lock Formatting In CI

Problem:

- `ruff-format` reported 42 files would be reformatted.

Fix:

1. Run:

```powershell
python -m ruff format .
python -m ruff check .
python -m unittest discover -s tests
```

1. Add CI and pre-commit checks:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests
```

1. Add a root `ruff.toml` or `[tool.ruff]` config if style needs to be explicit.

Definition of done:

- Formatting check passes locally and in CI.
- No unrelated generated artifacts are formatted or committed.

### P0.4 Add Real Dependency Audit Wiring

Problem:

- `pip-audit` appears in install/doctor paths but is not runnable through `opcoding tools . run pip-audit`.
- `osv-scanner` found no package sources.

Fix:

1. Add `pip-audit` to `opcoding.free_tools.run_tool`.
2. Add dependency source files:

```text
requirements-dev.txt
requirements-tools.txt
```

or a locked dev environment using `uv.lock` if adopting `uv`.

1. Make OSV scan either:

- `pyproject.toml` and lock/source files, or
- return a useful "skipped, no lockfile" status with remediation.

1. Add optional SBOM generation:

```text
cyclonedx-py
pip-audit --format=json
```

Definition of done:

- `python -m opcoding tools . run pip-audit` works.
- `python -m opcoding tools . run osv-scanner` is either meaningful or cleanly skipped.
- Dependency audit status appears in `opai doctor`.

### P0.5 Clean The Repo Boundary Before Publishing

Problem:

- `codingOP` is untracked inside a parent repo.
- Parent repo has unrelated modified/untracked files.
- Generated artifacts exist under the workspace, including many `__pycache__` directories.

Fix:

1. Treat `codingOP` as the OPai repo root.
1. Do not push from the parent website repo.
1. Before initializing/pushing OPai:

```powershell
python -m opai doctor
python -m unittest discover -s tests
python -m opaihub validate
git status --short
```

1. Ensure `.gitignore` excludes:

```text
__pycache__/
.ruff_cache/
.opaihub/
.opcoding-tools/
*.egg-info/
dist/
build/
.pytest_cache/
.mypy_cache/
.coverage
htmlcov/
```

1. Optionally clean ignored artifacts after verifying `.gitignore`:

```powershell
git clean -X -d -n
```

Only run the destructive version after reviewing the dry run:

```powershell
git clean -X -d -f
```

Definition of done:

- A fresh repo rooted at `codingOP` contains only OPai files.
- No parent website files appear in OPai history.
- Generated caches are ignored and absent from the initial commit.

### P0.6 Add An Install Smoke Test For The Promised UX

Problem:

- OPai's brand promise is "simple CLI install, automatic AI coding upgrade".
- Current tests cover source-tree behavior better than install-tree behavior.

Fix:

Add a smoke test script:

```text
scripts/smoke-install.ps1
scripts/smoke-install.sh
```

Minimum checks:

```powershell
python -m pip wheel . --no-deps -w .opaihub\wheelhouse
python -m venv .opaihub\smoke-venv
.opaihub\smoke-venv\Scripts\python.exe -m pip install --no-index --find-links .opaihub\wheelhouse opai==0.1.0
Push-Location $env:TEMP
.opaihub\smoke-venv\Scripts\python.exe -m opai version
.opaihub\smoke-venv\Scripts\python.exe -m opaihub validate
.opaihub\smoke-venv\Scripts\python.exe -m opai welcome --compact --no-color --image ascii
Pop-Location
```

Definition of done:

- Install smoke test runs on Windows.
- POSIX version exists for macOS/Linux CI.
- CI runs at least one install smoke test.

### P0.7 Rotate Any API Keys Pasted During Development

Problem:

- During development, a cloud API key was pasted into the conversation.
- Even if it was never committed, assume it is exposed.

Fix:

1. Rotate that key at the provider.
2. Keep all cloud providers optional and disabled by default.
3. Add `.env.example` with placeholder names only.
4. Add `OPAI_ALLOW_CLOUD=1` or per-provider enable flags for any cloud path.
5. Make `opai doctor` show missing keys without printing values.

Definition of done:

- No live keys in repo.
- Secret scanners pass.
- Docs tell users to set env vars locally, never in committed config.

## P1 High-Impact Improvements

These are the changes that make OPai genuinely stronger than "a CLI with some YAML".

### P1.1 Build The OPai Evidence Router

Purpose:

Before any AI reasoning, OPai should gather cheap local evidence and choose the cheapest useful next step.

Add:

```text
opaihub/router.py
opaihub/evidence.py
opaihub/task_classifier.py
opaihub/cache_keys.py
hub/registry/routing_rules.yaml
```

Task types:

- feature_build
- bug_fix
- failing_test
- code_review
- refactor
- dependency_update
- docs_update
- security_audit
- release_prepare
- deploy_prepare
- research
- unknown

Evidence pack:

```json
{
  "project_profile": "...",
  "git_status": "...",
  "changed_files": [],
  "recent_errors": [],
  "test_framework": "...",
  "targeted_test_command": "...",
  "lint_status": "...",
  "dependency_status": "...",
  "cached_answers": [],
  "risk_flags": []
}
```

Routing logic:

```text
1. If deterministic command can answer, run command.
2. If cached answer matches project hash + task hash, reuse.
3. If missing context, build context summary.
4. If task is small and local model is available, use local/cheap model.
5. If task affects architecture/security/release and cheap route failed, ask before stronger model.
6. GPT-5.5 Max tier only for ambiguous multi-file design, severe debugging dead ends, major architecture, or high-risk reviews.
```

CLI:

```powershell
opai route "fix failing auth test"
opai explain-route "build a Stripe webhook endpoint"
opai plan --budget cheap "add settings page"
```

Definition of done:

- Every workflow starts with `route`.
- Route decisions are logged with cost tier, evidence sources, and skipped expensive paths.
- Repeated tasks hit cache.

### P1.2 Build Project Context Packs

Purpose:

Make OPai useful across projects without dumping huge context into AI.

Add:

```text
.opai/project-profile.yaml
.opai/context/summary.md
.opai/context/code-map.json
.opai/context/test-map.json
.opai/context/dependency-map.json
.opai/context/commands.yaml
.opai/cache/
```

Context pack rules:

- Prefer file tree, git diff, touched files, test failures, and short summaries over full files.
- Store language/framework detection.
- Store known commands.
- Store architecture summaries per directory.
- Store per-file hashes so unchanged summaries are reused.
- Redact secrets before persistence.

CLI:

```powershell
opai project attach .
opai context build
opai context diff
opai context show --changed
```

Definition of done:

- OPai can attach any project and produce a reusable project context summary.
- Context summaries update incrementally.
- No cloud model call is required to build baseline context.

### P1.3 Add Test Selection And Failure Intelligence

Purpose:

Most AI coding waste comes from broad context and broad test runs. OPai should run the smallest useful checks first.

Add:

```text
opaihub/test_selector.py
opaihub/failure_analyzer.py
```

Capabilities:

- Detect pytest, unittest, npm test, vitest, jest, playwright, dotnet test, cargo test, go test.
- Map changed files to likely tests.
- Run targeted tests first.
- Escalate to full test only when target passes or risk is broad.
- Store flaky test history.
- Summarize failures with stack, command, changed files, and suspected owners.

CLI:

```powershell
opai test --changed
opai test --target tests/test_cli.py
opai fix-tests
```

Definition of done:

- A failing test workflow can run without paid AI until local diagnostics fail.
- Test failure summaries are compact and model-ready.

### P1.4 Strengthen GitOps Guardrails

Purpose:

OPai should help users commit safely without ever surprising them.

Add:

- `opai git status`
- `opai git summary`
- `opai git message`
- `opai git pr`
- `opai git risks`
- `opai git preflight`

Rules:

- Never push, merge, delete branches, reset hard, or force-push without explicit confirmation.
- Detect secrets before commit.
- Detect generated artifacts.
- Detect missing tests for source changes.
- Detect package metadata/version inconsistencies.
- Generate PR text from actual diff, tests, and risk notes.

Definition of done:

- `opai git preflight` becomes the command to run before pushing.
- Commit messages can be generated locally from diff summaries.
- Risky diffs are visible before a commit.

### P1.5 Make Terminal UX Strong But Respectful

Purpose:

The blue `Using OPai` and mascot should feel polished, not noisy.

Fixes:

- Detect non-TTY, CI, `TERM=dumb`, and `NO_COLOR`.
- Disable animation in logs unless `--force-animate`.
- Add `--plain`.
- Avoid base64 image protocols during animation; render inline image once, then animate lightweight ASCII/status if needed.
- Add terminal capability report:

```powershell
opai ux doctor
```

Status surfaces:

- CLI wrappers show `Using OPai`.
- Codex skill instructs sessions to use OPai.
- Claude memory block instructs sessions to use OPai.
- Copilot instruction file can be copied into project instructions.

Reality check:

- Closed apps may not allow a true bottom-right status line. OPai can provide wrappers, shell preamble, env vars, and instruction files; exact UI placement depends on each client.

Definition of done:

- In an interactive terminal, welcome is polished.
- In logs/CI, output is clean text.
- UX tests cover no-color/plain/compact behavior.

### P1.6 Add CI Before Public Repo

Add:

```text
.github/workflows/ci.yml
.github/workflows/security.yml
```

CI jobs:

- install package
- ruff format check
- ruff lint
- unit tests
- registry validation
- install smoke test
- pyright
- bandit
- detect-secrets
- pip-audit
- optional OSV
- actionlint for workflow files

Definition of done:

- PRs cannot pass with broken package data.
- Security failures are visible.
- CI remains free/cheap by using GitHub-hosted runners and no paid APIs.

### P1.7 Add Pre-Commit Without Making It Annoying

Add `.pre-commit-config.yaml` with fast hooks:

- trailing whitespace
- end of file fixer
- YAML/JSON/TOML check
- ruff format
- ruff check
- detect-secrets

Manual or pre-push hooks:

- bandit
- pyright
- pip-audit
- full tests

Reasoning:

- Fast checks at commit time.
- Slower/security checks at pre-push or manual stage.
- Avoid global hooks by default because global automatic hooks can be risky in untrusted repos.

Definition of done:

```powershell
pre-commit run --all-files
```

passes after setup.

### P1.8 Create A One-Command Installer Story

Supported installation paths:

1. Recommended stable path after PyPI:

```powershell
pipx install opai
opai install --global --shell-aliases
```

1. Pre-alpha GitHub path:

```powershell
pipx install git+https://github.com/<owner>/opai.git
opai install --global --shell-aliases
```

1. Fallback no-pipx path:

```powershell
python -m pip install --user opai
python -m opai install --global --shell-aliases
```

1. Local dev path:

```powershell
python -m pip install -e .
python -m opai install --global --shell-aliases
```

Add docs:

```text
hub/docs/INSTALL.md
README.md install quickstart
```

Definition of done:

- A new user can install with one recommended command plus one activation command.
- `opai doctor` explains what is active and what is missing.
- Uninstall instructions are explicit.

### P1.9 Add Uninstall And Rollback

Problem:

- OPai writes global files and shell profile blocks.
- Users need a clean exit path.

Add:

```powershell
opai integrate uninstall
opai integrate repair
opai integrate status
```

Rules:

- Only remove managed blocks.
- Do not delete user-authored profile content.
- Back up files before modifying profiles.
- Show dry run by default for destructive uninstall.

Definition of done:

- Install/uninstall/install cycle is idempotent.
- Tests cover existing profile content preservation.

### P1.10 Add AI Quality Feedback Loops

Purpose:

OPai should improve over time without spending more.

Add:

```text
.opai/runs/
.opai/analytics/
.opai/lessons/
```

Record for each workflow:

- task
- route decision
- tools run
- files touched
- tests run
- failures
- final outcome
- estimated cost
- reused cache hits
- user confirmations

Generate:

```powershell
opai improve report
opai improve suggest
opai improve apply-safe
```

Safe automatic improvements:

- update project command map
- update flaky test list
- update context summaries
- add discovered tools as disabled registry entries

Requires confirmation:

- changing workflow rules
- installing tools
- enabling cloud providers
- writing global files

Definition of done:

- OPai can tell the user why it chose a cheap path and how much it saved.
- Repeated project work gets faster.

## P2 Advanced AI Coding Multiplier

These make OPai feel closer to an "AI operating layer" while still staying local-first.

### P2.1 Local Model Layer

Add optional adapters:

- Ollama
- llama.cpp
- LM Studio local server
- vLLM local server

Rules:

- Never auto-download large models without confirmation.
- Detect installed runtimes.
- Recommend small coding models for cheap triage and summarization.
- Use local models for summaries, test-failure first pass, code maps, and docs drafts.
- Keep stronger cloud models for architecture, very hard bugs, and final review.

CLI:

```powershell
opai models detect
opai models recommend --task bug_fix
opai models enable ollama --model <name>
opai models benchmark
```

### P2.2 Semantic Code Search Without Cloud Spend

Add optional local vector search:

- SQLite FTS for baseline search.
- Optional local embeddings if a local embedding model exists.
- File-hash based incremental indexing.

CLI:

```powershell
opai index build
opai search "auth middleware"
opai context related path/to/file.py
```

### P2.3 Workflow Orchestrator

Implement registry-driven workflow runner:

- DAG steps.
- Retry policy.
- tool/agent budget gates.
- cache keys.
- artifact outputs.
- approval gates.

Example:

```powershell
opai workflow run feature_plan --task "add OAuth login"
opai workflow run bug_fix --error-file .opai/errors/latest.log
opai workflow run release_prepare --confirm
```

### P2.4 MCP Manager

Add:

- MCP server discovery.
- Per-project MCP enable/disable.
- Allowed path enforcement.
- Env var validation.
- Health checks.
- Config export for supported clients.

CLI:

```powershell
opai mcp list
opai mcp enable filesystem --project .
opai mcp export codex
opai mcp export claude
opai mcp doctor
```

### P2.5 Dashboard

Add a local dashboard:

- tools
- agents
- workflows
- cost
- health
- local model status
- MCP status
- recent runs
- project profiles
- security warnings

Rules:

- Localhost only by default.
- No telemetry by default.
- No external analytics.

### P2.6 "One Prompt To App, Two To Ship" Flow

Add a high-level app workflow:

```powershell
opai app create "todo app with auth and deployment"
opai app ship
```

Under the hood:

1. Project generator/adapters.
2. Requirements clarification only if critical.
3. Plan from templates.
4. Local scaffold.
5. Tests.
6. Review.
7. Security scan.
8. CI setup.
9. Deployment adapter selected but disabled until user confirms.
10. PR/release notes generated.

This is not one giant AI call. It is a sequence of deterministic steps with tiny AI assists only where valuable.

### P2.7 Tool Marketplace Registry

Add registry metadata quality gates:

- schema validation
- duplicate detection
- category normalization
- install safety
- license field
- source URL
- trust level
- maintainer
- last checked

CLI:

```powershell
opai tool search playwright
opai tool add ./tool.yaml
opai tool enable ruff
opai tool health --all
opai tool doctor --category security
```

### P2.8 Team Mode

Optional:

- shared project profiles
- shared safe workflow templates
- shared tool registry overlays
- no shared secrets
- redacted run reports

## Cost Architecture Upgrade

### Model Tiers

```text
tier 0: deterministic scripts and cached answers
tier 1: local model / small cheap model for summaries and classification
tier 2: medium model for bounded implementation or review
tier 3: strong model for complex debugging, architecture, security, release risk
tier 4: GPT-5.5 Max only for the hardest tasks or explicit user escalation
```

### Default Routing Rules

Use tier 0 when:

- command output, static analysis, tests, docs, or cache can answer.
- the task is formatting, linting, scanning, dependency listing, git status, or registry validation.

Use tier 1 when:

- summarizing local evidence.
- classifying task type.
- drafting docs from known context.
- explaining a small failure with a clear stack trace.

Use tier 2 when:

- changing a few files.
- writing tests from a clear spec.
- reviewing small diffs.

Use tier 3 when:

- cross-module architecture changes.
- subtle concurrency/data/security bugs.
- failing tests after local debugging.
- deployment incidents.

Use tier 4 only when:

- lower tiers failed and evidence is preserved.
- the work has high ambiguity or high blast radius.
- the user confirms the escalation or has configured an explicit budget.

### Token And Cost Budgets

Add budget scopes:

```yaml
global:
  daily_usd: 0.00
  ask_before_cloud: true
project:
  default_daily_usd: 0.00
workflow:
  feature_plan:
    max_tier: local_or_cheap
  release_prepare:
    max_tier: strong_with_confirmation
agent:
  cost_controller:
    max_tier: deterministic
```

Default should be zero-cloud-spend unless the user opts in.

### Context Compression

Rules:

- Load changed files before broad project files.
- Summarize directories once and reuse until hashes change.
- Use code maps rather than full files for discovery.
- Store failure summaries separately from full logs.
- Clip logs by default, but preserve full log artifact locally.
- Include exact command, cwd, return code, and timestamps in artifacts.

### Cache Keys

Use hashes:

```text
task text hash
project profile hash
git diff hash
relevant file hashes
tool version hashes
model tier
prompt template version
```

Cache hit:

- reuse answer if all key parts match.
- otherwise reuse partial context summaries.

### Batching

Rules:

- One route decision per user task.
- One evidence pack shared by all agents in a workflow.
- No duplicate scans by separate agents.
- Agents consume artifacts rather than re-running commands unless stale.

## Security Architecture Upgrade

### File Access

- Default allowed path: current project.
- Default forbidden paths: home secrets, SSH keys, cloud credentials, browser profiles, password stores.
- Global files can be written only by explicit install/integrate commands.
- Recursive delete/move requires path-boundary verification.

### Shell Execution

- Commands represented as argv arrays.
- Risk classifier runs before execution.
- Destructive commands require confirmation.
- Network/install commands require confirmation unless explicitly part of install workflow.
- Shell profile edits require backup and managed blocks.

### Secrets

- Redact logs before writing.
- Detect common tokens and provider keys.
- Do not print env var values.
- Add secret scanning to pre-commit and CI.
- Rotate any key pasted in development.

### Git

- Read-only Git commands are safe.
- Commit can be allowed after preflight.
- Push/merge/rebase/reset/delete/force require confirmation.
- Detect generated artifacts and secrets before commit.

### MCP

- Disabled by default unless local and low-risk.
- Filesystem MCP limited to project paths.
- Shell MCP confirmation for non-read-only commands.
- Cloud MCP requires env vars and explicit enable.
- Export client configs from OPai, do not ask users to hand-edit risky JSON unless needed.

## Tooling Roadmap

### Keep/Strengthen Existing Free Tools

- ruff: formatting/linting.
- pyright: type checking.
- bandit: Python security SAST.
- detect-secrets: baseline secret detection.
- gitleaks: secret scanning.
- markdownlint-cli2: docs linting.
- actionlint: GitHub workflow linting.
- pip-audit: Python dependency vulnerability scan.
- osv-scanner: dependency vulnerability scan when lock/source files exist.

### Add Soon

- pre-commit: local quality gates.
- build: standards-based wheel/sdist build.
- twine check: package metadata validation.
- pytest/pytest-cov if test suite moves from unittest or expands.
- coverage.py: quantify test coverage.
- deptry: detect unused/missing Python dependencies.
- vulture: dead code candidates, manual review only.
- radon: complexity metrics.
- semgrep: optional security/static analysis, disabled by default if heavy.
- shellcheck: POSIX shell wrapper quality where available.
- shfmt: POSIX shell formatting where available.
- markdown-link-check or lychee: docs link checking.

### Optional AI Coding Tools

All optional and disabled by default:

- local model runtimes: Ollama, llama.cpp, LM Studio.
- codebase indexing: SQLite FTS first, optional local embeddings later.
- browser automation: Playwright for local UI tests.
- deployment adapters: Vercel/Cloudflare/etc. only with explicit confirmation.
- Morph-style fast code-edit provider: cloud, API-key required, disabled by default.

## Repository Publishing Plan

### Before `git init` Or GitHub Repo Creation

1. Finish P0.1 through P0.7.
2. Run full verification:

```powershell
python -m compileall opcoding opaihub opai tests
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests
python -m opaihub validate
python -m opai doctor
python -m opcoding tools . run pyright
python -m opcoding tools . run bandit
python -m opcoding tools . run detect-secrets
python -m opcoding tools . run gitleaks
python -m opcoding tools . run pip-audit
python -m pip wheel . --no-deps -w .opaihub\wheelhouse
```

1. Run isolated install smoke test outside the source tree.
1. Confirm no live secrets.
1. Confirm `.gitignore` catches generated files.
1. Initialize repository inside `codingOP`, not the parent website repo.

### Initial Repo Structure

Recommended tracked root:

```text
README.md
LICENSE
CHANGELOG.md
pyproject.toml
.gitignore
.pre-commit-config.yaml
.github/workflows/ci.yml
opai/
opaihub/
opcoding/
hub/
docs/
tests/
scripts/
```

Generated/untracked:

```text
.opaihub/
.opcoding-tools/
__pycache__/
*.egg-info/
dist/
build/
```

## CI Matrix

Minimum:

- Windows latest, Python 3.10 and 3.13.
- Ubuntu latest, Python 3.10 and 3.13.
- macOS latest, Python 3.13 if budget allows.

Jobs:

```text
lint
test
security
package
install-smoke
docs
```

No paid APIs in CI.

## Documentation Gaps

Add or expand:

- Quickstart with `pipx`.
- Pre-alpha warning and stability expectations.
- "What OPai can and cannot do" for client status-line integration.
- Install/uninstall.
- Cost model and default zero-cloud-spend behavior.
- Security and secret handling.
- How to attach a project.
- How to add a tool.
- How to add an agent.
- How to add a workflow.
- How to enable local models.
- How to export MCP config.
- Troubleshooting terminal image/animation.

## Immediate Build Order

### Step 1: Packaging Fix

Files:

- `opaihub/loader.py`
- `opaihub/data/hub/**`
- `pyproject.toml`
- `tests/test_packaging.py`
- `scripts/smoke-install.ps1`

Commands:

```powershell
python -m pip wheel . --no-deps -w .opaihub\wheelhouse
python scripts\smoke-install.ps1
```

### Step 2: Safe Command Runner

Files:

- `opaihub/command_policy.py`
- `opaihub/command_runner.py`
- `opcoding/utils.py`
- `opaihub/health.py`
- `opai/cli.py`
- `hub/security/risky_commands.yaml`
- `tests/test_command_policy.py`

Commands:

```powershell
python -m unittest discover -s tests
python -m opcoding tools . run bandit
```

### Step 3: Formatting And CI

Files:

- `pyproject.toml`
- `.pre-commit-config.yaml`
- `.github/workflows/ci.yml`
- `.github/workflows/security.yml`

Commands:

```powershell
python -m ruff format .
python -m ruff check .
python -m unittest discover -s tests
```

### Step 4: Dependency Audit Wiring

Files:

- `opcoding/free_tools.py`
- `opcoding/doctor.py`
- `requirements-dev.txt`
- `requirements-tools.txt`
- `tests/test_tools_and_morph.py`

Commands:

```powershell
python -m opcoding tools . run pip-audit
python -m opai doctor
```

### Step 5: Install/Uninstall UX

Files:

- `opai/integrations.py`
- `opai/cli.py`
- `tests/test_opai_integrations.py`
- `hub/docs/INSTALL.md`

Commands:

```powershell
python -m opai integrate install --shell-aliases
python -m opai integrate status
python -m opai integrate uninstall --dry-run
```

### Step 6: Evidence Router MVP

Files:

- `opaihub/router.py`
- `opaihub/evidence.py`
- `opaihub/task_classifier.py`
- `hub/registry/routing_rules.yaml`
- `tests/test_router.py`

Commands:

```powershell
python -m opai route "fix failing tests"
python -m opai explain-route "ship a release"
```

## Definition Of Ready For Public Repo

OPai is ready to push publicly when:

- Source-tree tests pass.
- Install-tree smoke test passes.
- Hub data is packaged.
- Formatter is clean.
- High-severity Bandit issues are gone or documented with narrow suppressions.
- Dependency audit command works.
- Secret scans pass.
- CI exists.
- README has quickstart, warning, and uninstall.
- No live API keys or local generated artifacts are committed.
- The repo root is clean and not mixed with the parent website repo.

## Definition Of Ready For First Users

OPai is ready for brave pre-alpha users when:

- One-command install path is documented.
- `opai doctor` is reliable.
- `opai install --global --shell-aliases` is idempotent.
- `opai integrate uninstall` exists.
- Shell wrapper UX works on Windows and at least one POSIX shell.
- Project attach/context build works in a separate sample repo.
- Cloud providers remain disabled by default.
- Destructive actions require confirmation.
- Clear issue templates exist.

## External Documentation Checked

- Setuptools is the packaging backend used here and supports distributing reusable code/installable programs: [setuptools docs](https://setuptools.pypa.io/en/latest/).
- pipx is the right user-facing install story for Python CLI applications: [pipx installation docs](https://pipx.pypa.io/latest/installation/).
- uv is a strong optional tool/runtime manager, and its docs recommend isolated installation via pipx when installing from PyPI: [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/).
- pre-commit supports repo-local hooks, manual stages, and template-dir/global approaches; OPai should prefer project-local hooks by default for safety: [pre-commit docs](https://pre-commit.com/).
