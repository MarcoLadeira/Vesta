# OPai — the AI coding cost firewall

![OPai mascot](opai/assets/opai-mascot.png)

**OPai is the AI coding cost firewall.** It sits in front of Claude, Codex,
Copilot, Cursor, and Cline and routes every task to the cheapest safe path —
deterministic tools and local models first, paid cloud models only with your
confirmation — then proves the savings in numbers, locally and privately.

OPai 0.2.0 alpha.1 is a local-first AI coding hub that installs into your terminal and AI coding clients so every project gets better routing, safer automation, reusable context, Superpowers skills, MCP-ready registries, testing workflows, GitOps helpers, governance controls, benchmark proof, and a real cost ledger.

The goal is simple: make AI-assisted development feel close to "one prompt to build, one prompt to ship" without blindly burning expensive model credits.

## Prove it in 60 seconds

```sh
opai gui                                        # local, code-only coding cockpit (web GUI)
opai ask "summarize my changes"                # answer a cheap task locally for $0 (no cloud)
opai quickstart                                # guided first run: activate, route, savings
opai doctor                                    # which clients are active/broken/missing
opai route "fix the failing test" --record     # cheapest safe route + ledger entry
opai why "fix the failing test"                # explain the route and its savings
opai savings --markdown                        # estimated AI spend saved on this project
opai benchmark run --suite local --mode both   # compare normal AI vs OPai-routed use
opai benchmark run --suite max --mode both     # leaderboard-aligned local max suite
opai benchmark gate --min-context-reduction 10 # CI gate for benchmark proof
opai share --markdown                          # a shareable savings badge for your README
```

**New to OPai?** The one-page install funnel lives in [`site/index.html`](site/index.html).
Read the [Business Strategy](docs/BUSINESS_STRATEGY.md) for positioning and the
open-core model.

See also the [Quickstart](docs/QUICKSTART.md), the grounded
[before/after proof](docs/PROOF.md), the
[install proof checklist](docs/INSTALL_PROOF.md), the
[effectiveness benchmark guide](hub/docs/BENCHMARKS.md), the
[launch checklist](docs/LAUNCH_CHECKLIST.md), the
[30-day go-to-market plan](docs/GO_TO_MARKET_30_DAY_PLAN.md), the
[launch revenue runbook](docs/LAUNCH_REVENUE_RUNBOOK.md), the
[commercial access/IP protection plan](docs/COMMERCIAL_ACCESS_AND_IP_PROTECTION.md), the
[effectiveness/security audit](docs/EFFECTIVENESS_AND_SECURITY_AUDIT_2026_06_21.md), the
[alpha release notes](docs/RELEASE_0_2_0_ALPHA_1.md), and
[editions & pricing](hub/docs/PRICING_AND_EDITIONS.md).

### More efficiency & adoption commands

```sh
opai context pack --changed   # tiny, redacted context (changed files + adjacent tests)
opai test --changed           # run only the tests likely to cover your changes
opai metrics                  # local product metrics: tokens/escalations avoided, cache rate
opai benchmark report         # latest local OPai Efficiency Score
opai edition show             # Free / Pro / Team / Team-Governance / Enterprise
```

### Team & enterprise governance

OPai is also the control plane for teams ([GOVERNANCE.md](hub/docs/GOVERNANCE.md)):

```sh
opai team init                # committable opai-team-policy.yaml (shared policy)
opai team apply               # apply the team policy locally
opai policy check             # fail-closed CI gate (exits non-zero on violation)
opai policy check --require-team-policy  # strict team CI: policy file required
opai guard evidence <wf> --sign   # signed, tamper-evident evidence packet
opai audit log                # tamper-evident governance audit trail
opai team report              # who routed what, did it follow policy, spend avoided
```

Default posture:

```text
deterministic tools -> compact cache/context -> local model -> confirmed cheap cloud -> strong model only after evidence
```

Both `op` and `opai` launch OPai. The legacy OPcoding CLI remains available as `opcoding`.

## What OPai Gives You

- A branded `op` CLI for project activation, routing, scans, doctors, dashboards, and release checks.
- Automatic project instructions for Codex, Claude Code, and GitHub Copilot where those tools read local instruction files.
- Superpowers as part of OPai; the default installer fetches the free open-source Superpowers repo and exposes its skills through native discovery.
- A local-first tool registry for coding, testing, GitOps, security, MCP, docs, local models, browser automation, and deployment helpers.
- Cost-aware routing that gathers git diffs, tests, profiles, registry data, logs, and cached context before model escalation.
- Compact-by-default AI-facing output: route summaries, launcher badges, and instruction blocks stay tiny unless you opt into full evidence or welcome graphics.
- Safe command policies for destructive shell commands, Git operations, cloud calls, and secret-bearing logs.
- A packaged OP AI Hub foundation with tools, agents, workflows, prompts, model routing, MCP config examples, and docs.

## Controlled Alpha Access

OPai alpha access is controlled while the product and pricing are being tested.
Paid users and Team Pilot customers receive a private install command or release
package after checkout or onboarding.

After install, restart your terminal and AI clients once, then check:

```sh
op status
```

The installer installs the `op`/`opai` CLI, activates the project you ran it
from, writes OPai discovery files, installs and enables Superpowers discovery,
and installs persistent AI-client shell wrappers by default.

From this folder during development:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
op doctor
```

If Windows says `op` is not on `PATH` before your shell profile reloads, use `python -m opai doctor`.

Controlled alpha install links may include options for heavier local tools or
skipping the Superpowers network clone in locked-down environments.

The installer writes managed shell functions for `op`, `opai`, `codex`, `claude`, and `copilot` in PowerShell plus POSIX profiles where available. The AI-client wrappers activate OPai in the current project, print a blue one-line `Using OPai` badge, then launch the real CLI command. Set `OPAI_WELCOME=1` or pass `op launch <tool> --welcome` when you want the mascot graphic.

Developer install:

```powershell
python -m pip install -e . --no-deps
op activate --repair --shell-aliases
op status
```

Optional free local tools:

```powershell
op install --with-tools
```

These tools install into an external OPai tool cache instead of dropping venvs
or `node_modules` trees into every project.

After the first install, restart terminal sessions and AI coding clients so native skill discovery can see OPai and Superpowers.

## Ultra-Low Credit Mode

OPai 0.2.0 alpha.1 is tuned to spend less than normal AI coding by default:

- `opai route` returns compact local evidence instead of large logs and full diffs.
- `opai slim` writes AI-client ignore files and reports generated context bloat.
- Release, deploy, security, and publish tasks start at local preflight, not strong AI.
- Model prompts are not stored in cache unless `OPAI_STORE_PROMPTS=1`.
- Default generated project budgets are `$0.50/day`, `$5/month`, and `$0.10` soft limit per task.
- Context is capped to a compact 6,000 characters by default, with a hard 12,000-character guard.
- Cloud model use, paid tools, deploys, destructive commands, and large contexts require confirmation.

Use `opai route "<task>" --full-evidence` only when you need the larger diagnostic payload.

## Use OPai In Any Project

Run this in a project once:

```powershell
op activate --repair --shell-aliases
op status
```

Then start coding through an OPai-aware wrapper:

```powershell
op launch codex
op launch claude
op launch copilot
```

The default launch path prints only the one-line badge. To see the mascot:

```powershell
op launch claude --welcome
```

Or use the shadowed commands after shell aliases load:

```powershell
codex
claude
copilot
```

OPai writes compact managed instruction blocks to the top of `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, and `.opaihub/` so new AI sessions know to use local evidence, Superpowers, safety gates, and cost controls first. It also writes `.claudeignore`, `.cursorignore`, `.aiderignore`, `.continueignore`, `.geminiignore`, and `.opaiignore` so generated caches stay out of model context.

## OPai Commands

```text
opai version          show OPai 0.2.0a1 alpha.1
op version            same as opai version
op activate           attach current project and ensure Superpowers/AI instructions
op status             show activation, Superpowers, wrappers, and project state
op slim               write AI ignore files and report generated context bloat
op slim --clean       remove generated caches that waste AI context
op publish status     show git/publish readiness
opai install          create local .opaihub state and dashboards
opai statusline       print the right-aligned "Using OPai" badge
opai welcome          print the OPai mascot, badge, and quick commands
opai welcome --animate animate the OPai mascot in place
opai integrate install install global AI-client discovery files
opai launch codex     print one OPai badge line, then run codex
opai route "<task>"   print compact local-first routing decision
opai route --full-evidence "<task>" print full evidence only when needed
opai benchmark list    list local benchmark suites and optional harnesses
opai benchmark run     compare normal AI use with OPai-routed use
opai benchmark run --suite max run the leaderboard-aligned local max suite
opai benchmark gate    fail CI if proof metrics regress below thresholds
opai benchmark compare compare latest two benchmark runs
opai benchmark export  write optional promptfoo handoff config
opai benchmark report  render the latest OPai Efficiency Score
opai models recommend "<task>" choose the cheapest capable model tier
opai skills list      list OPai-managed skills exposed to Codex discovery
opai skills doctor    verify OPai skill files and registry paths
opai scan             summarize hub registries
opai doctor           validate registries and tool health
opai tools            list registered tools
opai agents           list registered agents
opai workflows        list registered workflows
opai dashboard --html write a local HTML dashboard
opai hub <command>    pass through to the full hub CLI
```

Examples:

```powershell
opai welcome
opai welcome --animate
opai welcome --compact --animate --frames 7
opai welcome --image ansi
opai welcome --image ascii
opai welcome --image kitty
opai models recommend "fix failing tests cheaply"
opai skills doctor
opai hub discover tools
opai hub sandbox check --command "git reset --hard"
opai hub schedule create daily_hub_check --cadence daily
opai hub analytics status
opai hub team init --mode solo
```

Global integration files are written under `~/.opai/`, `~/.agents/skills/opai/`, and managed client-specific instruction files where supported. Closed desktop apps may not expose a UI surface that OPai can draw into directly; OPai provides the blue statusline command, mascot welcome screen, CLI wrappers, and discovery/instruction files for clients that support them. The ANSI image renderer uses Pillow when available and falls back cleanly to ASCII.

Superpowers is treated as part of OPai. The default installer clones or updates it under `~/.codex/superpowers`, then OPai activation ensures it is visible to native skill discovery at `~/.agents/skills/superpowers`. Restart Codex/Claude/Copilot after first activation so skills are rediscovered.

OPai also publishes its own skill library under `~/.agents/skills/opai/`. That folder contains the root `opai` skill plus 35 focused OPai skills for routing, model selection, testing, debugging, GitOps, security, refactors, MCP setup, local models, and release preflight.

## Legacy Name

OPcoding is a reusable, local-first coding workspace. Its job is to make every project easier to build with AI agents while avoiding wasteful model calls.

## OP AI Hub

This repo now includes `hub/`, the Phase 1 foundation for OP AI Hub: a low-cost, modular, local-first registry for AI tools, agents, MCP servers, workflows, prompts, models, security policies, and project integrations.

Start with:

```powershell
python -m opaihub scan
python -m opaihub list-tools
python -m opaihub list-agents
python -m opaihub list-workflows
python -m opaihub tool health --id ruff
python -m opaihub doctor
```

Phase 2 foundation commands:

```powershell
python -m opaihub project attach
python -m opaihub validate
python -m opaihub tool enable playwright-optional
python -m opaihub mcp render --write
python -m opaihub dashboard
python -m opaihub models discover-local
python -m opaihub tool add --id my-tool --description "What it does"
```

Docs:

- `hub/docs/INSTALL.md`
- `hub/docs/COST_REDUCTION_0_1_1.md`
- `hub/docs/AI_CLIENT_INTEGRATIONS.md`
- `hub/docs/OPAI_0_1_0_PRE_ALPHA.md`
- `hub/docs/OP_AI_HUB_OVERVIEW.md`
- `hub/docs/ARCHITECTURE.md`
- `hub/docs/ADDING_TOOLS.md`
- `hub/docs/ADDING_AGENTS.md`
- `hub/docs/ADDING_WORKFLOWS.md`
- `hub/docs/SKILLS_AND_MODEL_ROUTING.md`
- `hub/docs/WORKFLOWS.md`

Community:

- [Discussions](https://github.com/MarcoLadeira/OPai/discussions): questions, ideas, tool suggestions, and install help.
- [Issues](https://github.com/MarcoLadeira/OPai/issues): reproducible bugs, cost regressions, security hardening, and roadmap tasks.
- [CONTRIBUTING.md](CONTRIBUTING.md): local development and PR guidance.
- [SECURITY.md](SECURITY.md): reporting and safety defaults.
- [SUPPORT.md](SUPPORT.md): where to ask for help.

This first-third implementation includes:

- `op` CLI with project onboarding, scanning, context, routing, cost, git, test, review, fix, and doctor commands.
- Local-first project profiles in `.opcoding/project.json`.
- Compact reusable context in `.opcoding/context.md`.
- Cost routing from deterministic tools to GPT-5.5 Max only when warranted.
- Safe GitOps helpers that never push, merge, or delete branches.
- Local review, secret scanning, test command detection, and failure clue extraction.
- Agent, prompt, adapter, workflow, MCP, budget, and permission definitions for later orchestration.

## Quick Start

PowerShell:

```powershell
$env:PYTHONPATH = "$PWD"
python -m opcoding doctor .
python -m opcoding init C:\path\to\project
python -m opcoding scan C:\path\to\project
python -m opcoding auto "add login page" --project C:\path\to\project
```

Using the wrapper:

```powershell
.\bin\op.ps1 init C:\path\to\project
.\bin\op.ps1 route "fix failing auth tests" --project C:\path\to\project
.\bin\op.ps1 test C:\path\to\project --dry-run
.\bin\op.ps1 git C:\path\to\project summary
```

## Core Commands

```text
op init <project>          create .opcoding profile/context
op scan <project>          detect stack, commands, docs, CI, git
op context <project>       build compressed project context
op route "<task>"          choose local/cheap/strong/Max route
op plan "<task>"           print local-first planning checklist
op refactor <scope> <goal> prepare safe refactor route
op auto "<task>"           route task and print local-first steps
op cost <project> report   show budget/usage policy
op git <project> summary   summarize local diff safely
op git <project> secrets   scan diff for secret-like values
op git <project> pr        draft PR description from diff
op test <project>          detect and run test command
op test <project> --dry-run
op review <project>        local-first diff review
op fix <project> --cmd "pytest"
op doctor <project>        check tool readiness
op agents "<task>"         batch selected agents with shared context
op ask "<task>"            prepare/cache a routed model prompt
op index <project> build   build a local symbol/file index
op memory <project> list   inspect local project memory
op mcp <project> doctor    validate MCP profile
op ship "<task>"           create a two-prompt shipping plan
op deploy <project>        prepare deploy and rollback checks
op dashboard <project>     write .opcoding/dashboard.md
op hooks <project> install install secret-checking pre-commit hook
op ci <project> github     generate GitHub Actions checks
op tools <project> install --set core
op morph <project> doctor
```

## Design Principle

The router always starts at `L0`: scripts, git, tests, scanners, and cache. AI is only considered after local evidence is collected and redacted.

```text
L0 local tool -> L1 cache/local model -> L2 cheap model -> L3 strong model -> L4 GPT-5.5 Max
```

Phase 2 should add real model adapters, MCP runners, project index MCP servers, and multi-agent batching on top of this spine.

Those advanced pieces now exist as safe local-first commands. Model execution is still opt-in and environment-variable driven so this project does not burn credits by accident.
