# OPai

![OPai mascot](opai/assets/opai-mascot.png)

OPai 0.1.0 pre-alpha is a local-first AI coding hub that installs into your terminal and AI coding clients so every project gets better routing, safer automation, reusable context, Superpowers skills, MCP-ready registries, testing workflows, GitOps helpers, and cost controls.

The goal is simple: make AI-assisted development feel close to "one prompt to build, one prompt to ship" without blindly burning expensive model credits.

Default posture:

```text
local tools -> cache/context -> cheap model -> strong model -> GPT-5.5 Max only when justified
```

Both `op` and `opai` launch OPai. The legacy OPcoding CLI remains available as `opcoding`.

## What OPai Gives You

- A branded `op` CLI for project activation, routing, scans, doctors, dashboards, and release checks.
- Automatic project instructions for Codex, Claude Code, and GitHub Copilot where those tools read local instruction files.
- Superpowers as part of OPai when installed at `~/.codex/superpowers/skills`.
- A local-first tool registry for coding, testing, GitOps, security, MCP, docs, local models, browser automation, and deployment helpers.
- Cost-aware routing that gathers git diffs, tests, profiles, registry data, logs, and cached context before model escalation.
- Safe command policies for destructive shell commands, Git operations, cloud calls, and secret-bearing logs.
- A packaged OP AI Hub foundation with tools, agents, workflows, prompts, model routing, MCP config examples, and docs.

## Install

From GitHub:

```powershell
git clone https://github.com/MarcoLadeira/OPai.git
cd OPai
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShellAliases
op status
```

macOS/Linux:

```sh
git clone https://github.com/MarcoLadeira/OPai.git
cd OPai
sh ./install.sh --shell-aliases
op status
```

From this folder during development:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
op doctor
```

If Windows says `op` is not on `PATH`, use `python -m opai doctor`.

To also shadow supported AI CLI commands with OPai launch wrappers:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShellAliases
```

On Windows, `-ShellAliases` installs managed PowerShell functions for `op`, `opai`, `codex`, `claude`, and `copilot`. The AI-client wrappers activate OPai in the current project, print a blue `Using OPai` badge plus the OPai mascot graphic, then launch the real CLI command. Cross-platform wrapper launch is available through `op launch <codex|claude|copilot>`.

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

After the first install, restart terminal sessions and AI coding clients so native skill discovery can see OPai and Superpowers.

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

Or use the shadowed commands after shell aliases load:

```powershell
codex
claude
copilot
```

OPai writes managed instruction blocks to `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, and `.opaihub/` so new AI sessions know to use local evidence, Superpowers, safety gates, and cost controls first.

## OPai Commands

```text
opai version          show OPai 0.1.0 pre-alpha
op version            same as opai version
op activate           attach current project and ensure Superpowers/AI instructions
op status             show activation, Superpowers, wrappers, and project state
op publish status     show git/publish readiness
opai install          create local .opaihub state and dashboards
opai statusline       print the right-aligned "Using OPai" badge
opai welcome          print the OPai mascot, badge, and quick commands
opai welcome --animate animate the OPai mascot in place
opai integrate install install global AI-client discovery files
opai launch codex     print OPai badge, then run codex
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
opai hub discover tools
opai hub sandbox check --command "git reset --hard"
opai hub schedule create daily_hub_check --cadence daily
opai hub analytics status
opai hub team init --mode solo
```

Global integration files are written under `~/.opai/`, `~/.agents/skills/opai/`, and managed client-specific instruction files where supported. Closed desktop apps may not expose a UI surface that OPai can draw into directly; OPai provides the blue statusline command, mascot welcome screen, CLI wrappers, and discovery/instruction files for clients that support them. The ANSI image renderer uses Pillow when available and falls back cleanly to ASCII.

Superpowers is treated as part of OPai when it is installed at `~/.codex/superpowers/skills`; OPai activation ensures it is visible to native skill discovery at `~/.agents/skills/superpowers`. Restart Codex/Claude/Copilot after first activation so skills are rediscovered.

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
- `hub/docs/AI_CLIENT_INTEGRATIONS.md`
- `hub/docs/OPAI_0_1_0_PRE_ALPHA.md`
- `hub/docs/OP_AI_HUB_OVERVIEW.md`
- `hub/docs/ARCHITECTURE.md`
- `hub/docs/ADDING_TOOLS.md`
- `hub/docs/ADDING_AGENTS.md`
- `hub/docs/ADDING_WORKFLOWS.md`
- `hub/docs/WORKFLOWS.md`

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
