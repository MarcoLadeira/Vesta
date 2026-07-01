# AI Client Integrations

OPai's target behavior is:

1. User installs OPai from the CLI.
2. OPai writes global discovery and instruction files.
3. AI coding clients that support local skills, memory, project instructions, wrappers, or statusline commands can detect OPai.
4. Terminal launches can display a blue `Using OPai` badge and OPai mascot graphic above the prompt area.

## Commands

```sh
opai install
opai integrate status
opai statusline
opai welcome
opai welcome --animate
opai welcome --image ansi
opai welcome --image ascii
opai welcome --image kitty
opai launch codex
opai launch claude
opai launch copilot
```

## Files Written

- `~/.opai/global.json`: OPai global integration manifest.
- `~/.opai/status.txt`: contains `Using OPai`.
- `~/.opai/instructions/OPAI.md`: shared local-first policy.
- `~/.agents/skills/opai/SKILL.md`: Codex-style skill discovery.
- `~/.codex/superpowers`: Superpowers checkout installed by the default installer.
- `~/.agents/skills/superpowers`: Superpowers discovery bridge.
- `~/.claude/CLAUDE.md`: managed OPai memory block.
- `~/.opai/integrations/copilot-instructions.md`: Copilot instruction seed.
- `~/.opai/bin/opai-codex*`, `opai-claude*`, `opai-copilot*`: terminal launch wrappers.

The terminal wrappers selectively proxy canonical one-shot calls that OPai can
reproduce without changing provider behavior. Interactive, stdin, structured
output, and unknown option sets execute through the real CLI unchanged. Agent
Readiness labels each managed wrapper `selective proxy`; older status-only
wrappers are reported as `legacy passthrough` and can be repaired with
`opai activate --repair --shell-aliases`.

For each project, `opai activate` writes the OPai managed block at the top of `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md`, plus dedicated rule files for Cursor (`.cursor/rules/opai.mdc`) and Cline (`.clinerules/opai.md`), so clients see OPai before older project notes. Folder-form rule files are used for Cursor and Cline so existing single-file user rules are never overwritten.

## Supported Clients & Readiness

OPai targets five clients and reports each one explicitly:

| Client | Project file | Global discovery |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `~/.claude/CLAUDE.md` |
| Codex | `AGENTS.md` | `~/.agents/skills/opai/SKILL.md` |
| GitHub Copilot | `.github/copilot-instructions.md` | `~/.opai/integrations/copilot-instructions.md` |
| Cursor | `.cursor/rules/opai.mdc` | — |
| Cline | `.clinerules/opai.md` | — |

`opai status` and `opai doctor` report each client as **active**, **broken**, or
**missing**, and a failing client returns a concrete repair command
(`opai activate --repair`). `opai doctor` also detects **stale paths** — a moved
repository or missing global files — and is read-only by default.

```sh
opai status      # full activation + per-client integration state
opai doctor      # branded readiness: active/broken/missing + stale-path detection
opai update      # ff-only update of ~/.opai/source
opai uninstall   # dry-run by default; --confirm removes managed blocks safely
```

## Badge Behavior

OPai provides the badge text through:

- `opai statusline`
- `opai welcome`
- `OPAI_ACTIVE=1`
- `OPAI_STATUS=Using OPai`
- wrapper scripts under `~/.opai/bin`

`opai welcome` uses the packaged OPai mascot image. `opai welcome --animate` plays a short in-place terminal animation. Terminals with image support can request inline graphics with `--image kitty` or `--image iterm`; other terminals use `--image ansi` to render the PNG as ANSI color blocks when Pillow is available, then fall back to a blue ASCII mascot.

Some AI apps do not expose a plugin surface for a bottom-right label above their input window. OPai cannot safely force UI changes into closed apps. For those clients, OPai installs instruction files and wrappers so the client can opt in when it supports local discovery.

## Shell Aliases

PowerShell aliases can be installed with:

```powershell
opai integrate install --shell-aliases
```

This adds a managed block to the PowerShell profile. It can be rerun safely; OPai replaces its own managed block instead of duplicating it.
