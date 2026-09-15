# Vesta Install

Vesta 0.2.0 alpha.1 installs as a small Python CLI with local registry files. The default install creates `.vestahub/` project state, validates registries, and writes dashboards. It does not enable paid APIs, cloud model calls, or destructive automation.

## One Command Install

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

macOS/Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.sh | sh
```

The remote installer clones or updates Vesta under `~/.vesta/source`, installs the `op`/`vesta` CLI, activates the project you ran it from, writes global AI-client discovery files, clones or updates the free open-source Superpowers repo, enables Superpowers discovery, and installs persistent AI-client shell wrappers by default.

After first install, restart terminals and AI coding clients once, then run:

```sh
op status
```

To download the heavier free local tool bundle during install:

```powershell
$env:VESTA_WITH_TOOLS = "1"
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

To skip the Superpowers network clone in locked-down environments:

```powershell
$env:VESTA_NO_SUPERPOWERS = "1"
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
python -m vesta doctor
```

## AI Client Badge Setup

The default `vesta install` writes safe global discovery files so supported AI coding clients can find Vesta:

- `~/.vesta/status.txt`
- `~/.vesta/instructions/VESTA.md`
- `~/.agents/skills/vesta/SKILL.md`
- `~/.codex/superpowers`: Superpowers source checkout
- `~/.agents/skills/superpowers`: native skill discovery bridge
- `~/.claude/CLAUDE.md` managed Vesta block
- `~/.vesta/integrations/copilot-instructions.md`

For terminal CLIs, Vesta can print the badge before launching the AI client:

```sh
vesta launch codex
vesta launch claude
vesta launch copilot
```

The one-command installer writes managed shell aliases that shadow `op`, `vesta`, `codex`, `claude`, and `copilot` with Vesta wrappers by default. PowerShell profiles are supported on Windows; `.profile`, `.bashrc`, and `.zshrc` are written for POSIX shells. To opt out from a local checkout:

Managed wrappers proxy conservative one-shot forms and fail open to the real
CLI for interactive or advanced invocations. Passthrough preserves the original
argument vector and exit code; wrapper status is sent to stderr so pipes and
JSON output remain machine-readable.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoShellAliases
```

The visible badge text is:

```text
Using Vesta
```

It is blue in ANSI-capable terminals. The stronger CLI welcome screen is:

```sh
vesta welcome
vesta welcome --animate
vesta welcome --compact --animate --frames 7
vesta welcome --image ansi
vesta welcome --image ascii
```

Terminals that support inline graphics can try:

```sh
vesta welcome --image kitty
vesta welcome --image iterm
```

For packaged installs, install the optional terminal image renderer with:

```sh
pip install "vesta[terminal-ui]"
```

Closed desktop apps may not expose a place for Vesta to draw a bottom-right status label. Vesta still installs discovery/instruction files for clients that support local skills, memory, or project instructions.

Project activation writes Vesta managed blocks at the top of `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md`. This keeps Vesta visible even in projects that already have long instruction files.

## Python Editable Install

```sh
python -m pip install -e .
vesta install --no-tools
```

## Optional Free Tool Bootstrap

```sh
vesta install --with-tools
```

This may download free open-source tools such as linters and scanners. It still does not enable paid model APIs.

## Future Package Install Shape

When Vesta is published, the intended user flow is:

```sh
pipx install vesta
vesta install
```

For a Git repository before package publishing:

```sh
pipx install git+https://github.com/<owner>/vesta.git
vesta install
```

Vesta keeps remote script install small and reviewable; the script clones a normal Git checkout instead of hiding the project inside opaque shell logic.
