# Vesta Client Demos: normal AI vs Vesta-routed (#52)

Reproducible before/after demos for each AI client. The "before" is the agent
spending freely; the "after" is the same task routed through Vesta's local-first
firewall. All six clients consume the same Vesta policy, skills, wrappers, and
repair flow.

## Readiness check (all clients)

```sh
opai doctor        # active / broken / missing per client + a repair command
opai activate --repair   # fix stale paths after moving the repo or install
```

## The shared demo (run in any repo)

```sh
# BEFORE: what an agent would do unrouted (send the whole task to a strong model)
#   -> large context, paid cloud call, no policy gate

# AFTER: route the same task through Vesta
opai route "fix the failing auth test" --record
opai why "fix the failing auth test"      # workflow, tier, policy, savings
opai savings --markdown                    # dollars saved, cloud calls avoided
```

## Per-client

### Claude Code
- Vesta writes `CLAUDE.md` (managed block) + global `~/.claude/CLAUDE.md`.
- Demo: open Claude Code in the repo; it reads the Vesta policy and prefers
  `opai route`/`opai context pack` before sending whole files.

### Codex
- Vesta writes `AGENTS.md` + `~/.agents/skills/opai/SKILL.md`.
- Demo: `opai launch codex` activates Vesta, prints the `Using Vesta` badge, then
  launches Codex with the policy in scope.

### Cursor
- Vesta writes `.cursor/rules/opai.mdc` and `.cursorignore`.
- Demo: `opai context ignores --clients cursor` then show Cursor no longer
  sends `node_modules/`, build output, or caches to the model.

### Cline
- Vesta writes `.clinerules/opai.md` and `.clineignore`.
- Demo: same context-slim proof via `.clineignore`; `opai context profile`
  shows the bytes/tokens removed.

### GitHub Copilot
- Vesta writes `.github/copilot-instructions.md` and `.copilotignore`.
- Demo: `opai doctor` shows Copilot active; `.copilotignore` keeps generated
  files out of Copilot context.

### Gemini CLI
- Vesta writes `GEMINI.md`, `.geminiignore`, and `opai-gemini` wrappers.
- Demo: launch Gemini through the wrapper; Plan stays read-only, Auto Edit may
  edit repository files, and YOLO maps to acknowledged Full Auto behavior.

## Verify the wrappers (Windows + POSIX)

```powershell
# Windows PowerShell
opai activate --repair --shell-aliases
op status
```

```sh
# POSIX shell
opai activate --repair --shell-aliases
op status
```

Wrapper generation for PowerShell and POSIX profiles is covered by the
integration test suite.
