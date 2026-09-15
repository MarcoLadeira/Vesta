# AI Client Integrations

Vesta's target behavior is:

1. User installs Vesta from the CLI.
2. Vesta writes global discovery and instruction files.
3. AI coding clients that support local skills, memory, project instructions, wrappers, or statusline commands can detect Vesta.
4. Terminal launches can display a blue `Using Vesta` badge and Vesta mascot graphic above the prompt area.

## Commands

```sh
vesta install
vesta integrate status
vesta statusline
vesta welcome
vesta welcome --animate
vesta welcome --image ansi
vesta welcome --image ascii
vesta welcome --image kitty
vesta launch codex
vesta launch claude
vesta launch copilot
vesta launch gemini
```

## Files Written

- `~/.opai/global.json`: Vesta global integration manifest.
- `~/.opai/status.txt`: contains `Using Vesta`.
- `~/.opai/instructions/OPAI.md`: shared local-first policy.
- `~/.agents/skills/opai/SKILL.md`: Codex-style skill discovery.
- `~/.codex/superpowers`: Superpowers checkout installed by the default installer.
- `~/.agents/skills/superpowers`: Superpowers discovery bridge.
- `~/.claude/CLAUDE.md`: managed Vesta memory block.
- `~/.opai/integrations/copilot-instructions.md`: Copilot instruction seed.
- `~/.opai/bin/opai-codex*`, `opai-claude*`, `opai-copilot*`, `opai-gemini*`: terminal launch wrappers.

For each project, `vesta activate` writes the Vesta managed block at the top of `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.github/copilot-instructions.md`, plus dedicated rule files for Cursor (`.cursor/rules/opai.mdc`) and Cline (`.clinerules/opai.md`), so clients see Vesta before older project notes. Folder-form rule files are used for Cursor and Cline so existing single-file user rules are never overwritten.

## Supported Clients & Readiness

Vesta targets six clients and reports each one explicitly:

| Client | Project file | Global discovery |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `~/.claude/CLAUDE.md` |
| Codex | `AGENTS.md` | `~/.agents/skills/opai/SKILL.md` |
| GitHub Copilot | `.github/copilot-instructions.md` | `~/.opai/integrations/copilot-instructions.md` |
| Gemini CLI | `GEMINI.md` | `~/.opai/integrations/gemini-instructions.md` |
| Cursor | `.cursor/rules/opai.mdc` | — |
| Cline | `.clinerules/opai.md` | — |

`vesta status` and `vesta doctor` report each client as **active**, **broken**, or
**missing**, and a failing client returns a concrete repair command
(`vesta activate --repair`). `vesta doctor` also detects **stale paths** — a moved
repository or missing global files — and is read-only by default.

```sh
vesta status      # full activation + per-client integration state
vesta doctor      # branded readiness: active/broken/missing + stale-path detection
vesta update      # ff-only update of ~/.opai/source
vesta uninstall   # dry-run by default; --confirm removes managed blocks safely
```

## Badge Behavior

Vesta provides the badge text through:

- `vesta statusline`
- `vesta welcome`
- `OPAI_ACTIVE=1`
- `OPAI_STATUS=Using Vesta`
- wrapper scripts under `~/.opai/bin`

`vesta welcome` uses the packaged Vesta mascot image. `vesta welcome --animate` plays a short in-place terminal animation. Terminals with image support can request inline graphics with `--image kitty` or `--image iterm`; other terminals use `--image ansi` to render the PNG as ANSI color blocks when Pillow is available, then fall back to a blue ASCII mascot.

Some AI apps do not expose a plugin surface for a bottom-right label above their input window. Vesta cannot safely force UI changes into closed apps. For those clients, Vesta installs instruction files and wrappers so the client can opt in when it supports local discovery.

## Shell Aliases

PowerShell aliases can be installed with:

```powershell
vesta integrate install --shell-aliases
```

This adds a managed block to the PowerShell profile. It can be rerun safely; Vesta replaces its own managed block instead of duplicating it.
