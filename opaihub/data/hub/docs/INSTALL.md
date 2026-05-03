# OPai Install

OPai 0.1.0 pre-alpha installs as a small Python CLI with local registry files. The default install creates `.opaihub/` project state, validates registries, and writes dashboards. It does not enable paid APIs, cloud model calls, or destructive automation.

## One Command From A Local Checkout

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
opai doctor
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

For terminal CLIs, OPai can print the badge before launching the AI client:

```sh
opai launch codex
opai launch claude
opai launch copilot
```

To install PowerShell command aliases that shadow `codex`, `claude`, and `copilot` with OPai wrappers:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ShellAliases
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

## Python Editable Install

```sh
python -m pip install -e . --no-deps
opai install --no-tools
```

## Optional Free Tool Bootstrap

```sh
opai install --with-tools
```

This may download free open-source tools such as linters and scanners. It still does not enable paid model APIs.

## Future Public Install Shape

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

Avoid copy-paste installers that pipe remote scripts directly into a shell. OPai's own sandbox policy treats that pattern as denied.
