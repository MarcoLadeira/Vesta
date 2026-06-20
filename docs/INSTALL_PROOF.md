# OPai Install Proof Checklist

Run this after installing to prove OPai is active and the cost firewall works.
Every step is a real command with an observable result.

## Prerequisites

- Python 3.10+ (`python --version`)
- Git (`git --version`)

## Checklist

- [ ] **CLI resolves.** `opai version` prints `OPai 0.1.1 pre-alpha`.
      (Fallback before PATH reloads: `python -m opai version`.)
- [ ] **Project activates.** `opai activate` writes project instructions and
      returns `"status": "active"`.
- [ ] **All five clients active.** `opai doctor` shows `readiness: ready` and
      `client_integrations.summary.active` lists
      `claude, codex, copilot, cursor, cline`.
- [ ] **Client rule files exist:**
  - [ ] `AGENTS.md` (Codex) contains the OPai managed block
  - [ ] `CLAUDE.md` (Claude) contains the OPai managed block
  - [ ] `.github/copilot-instructions.md` (Copilot)
  - [ ] `.cursor/rules/opai.mdc` (Cursor)
  - [ ] `.clinerules/opai.md` (Cline)
- [ ] **Routing is read-only.** `opai route "show git status"` prints a compact
      decision and does **not** create `.opaihub/ledger/usage.jsonl`.
- [ ] **Recording works.** `opai route "fix a bug" --record` then
      `opai savings` shows `routed_tasks >= 1` and a non-zero
      `estimated_savings_usd`.
- [ ] **Privacy holds.** `.opaihub/ledger/usage.jsonl` contains only
      `task_hash` values — never your raw prompt text.
- [ ] **Policy is enforced.** `opai policy show` reports a profile; cloud tiers
      require confirmation.
- [ ] **Guarded gates fail closed.** `opai guard action "git push"` returns
      `decision: deny`; adding `--confirm` returns `confirm`.
- [ ] **Eval passes.** `opai models eval --no-write` reports
      `cheapest_tier_rate: 1.0` on the bundled fixtures.
- [ ] **Clean removal works.** `opai uninstall` (dry-run) lists managed files;
      `opai uninstall --confirm` removes them and is safe to re-run.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `op` / `opai` not found | Restart the shell, or use `python -m opai ...` |
| A client shows `broken`/`missing` | `opai activate --repair` |
| Moved the repo and discovery broke | `opai doctor` reports `stale_paths`; run `opai activate --repair` |
| Superpowers not discovered | `opai activate --install-superpowers` |
| Update the install | `opai update` (ff-only pull of `~/.opai/source`) |

When every box is checked, OPai is installed, every client is wired, and the
cost firewall is producing real, private savings numbers.
