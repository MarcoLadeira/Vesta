# OPai Install

OPai 0.2.0 alpha.1 installs as a small Python CLI with local registry files. The default install creates `.opaihub/` project state, validates registries, and writes dashboards. It does not enable paid APIs, cloud model calls, or destructive automation.

## One Command Install

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

macOS/Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.sh | sh
```

The remote installer clones or updates OPai under `~/.opai/source`, installs the `op`/`opai` CLI, activates the project you ran it from, writes global AI-client discovery files, clones or updates the free open-source Superpowers repo, enables Superpowers discovery, and installs persistent AI-client shell wrappers by default.

After first install, restart terminals and AI coding clients once, then run:

```sh
op status
```

To download the heavier free local tool bundle during install:

```powershell
$env:OPAI_WITH_TOOLS = "1"
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

To skip the Superpowers network clone in locked-down environments:

```powershell
$env:OPAI_NO_SUPERPOWERS = "1"
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

## Local Checkout Install

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS/Linux:

```sh
sh ./install.sh
```

Then run:

```sh
op doctor
```

If your Python scripts directory is not on `PATH`, use:

```sh
python -m opai doctor
```

## AI Client Badge Setup

The default `opai install` writes safe global discovery files so supported AI coding clients can find OPai:

- `~/.opai/status.txt`
- `~/.opai/instructions/OPAI.md`
- `~/.agents/skills/opai/SKILL.md`
- `~/.claude/CLAUDE.md` managed OPai block
- `~/.opai/integrations/copilot-instructions.md`
- `~/.codex/superpowers`: Superpowers source checkout
- `~/.agents/skills/superpowers`: native skill discovery bridge

For terminal CLIs, OPai can print the badge before launching the AI client:

```sh
opai launch codex
opai launch claude
opai launch copilot
```

The one-command installer writes managed shell aliases that shadow `op`, `opai`, `codex`, `claude`, and `copilot` with OPai wrappers by default. PowerShell profiles are supported on Windows; `.profile`, `.bashrc`, and `.zshrc` are written for POSIX shells. To opt out from a local checkout:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoShellAliases
```

The visible badge text is:

```text
Using OPai
```

It is blue in ANSI-capable terminals. The stronger CLI welcome screen is:

```sh
opai welcome
opai welcome --animate
opai welcome --compact --animate --frames 7
opai welcome --image ansi
opai welcome --image ascii
```

Terminals that support inline graphics can try:

```sh
opai welcome --image kitty
opai welcome --image iterm
```

For packaged installs, install the optional terminal image renderer with:

```sh
pip install "opai[terminal-ui]"
```

Closed desktop apps may not expose a place for OPai to draw a bottom-right status label. OPai still installs discovery/instruction files for clients that support local skills, memory, or project instructions.

Project activation writes OPai managed blocks at the top of `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md`. This keeps OPai visible even in projects that already have long instruction files.

## Python Editable Install

```sh
python -m pip install -e .
opai install --no-tools
```

## Optional Free Tool Bootstrap

```sh
opai install --with-tools
```

This may download free open-source tools such as linters and scanners. It still does not enable paid model APIs.

## Future Package Install Shape

When OPai is published, the intended user flow is:

```sh
pipx install opai
opai install
```

For a Git repository before package publishing:

```sh
pipx install git+https://github.com/<owner>/opai.git
opai install
```

OPai keeps remote script install small and reviewable; the script clones a normal Git checkout instead of hiding the project inside opaque shell logic.
