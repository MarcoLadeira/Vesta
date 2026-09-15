# Vesta Install Proof Checklist

Run this after installing to prove Vesta is active and the cost firewall works.
Every step is a real command with an observable result.

## Prerequisites

- Python 3.10+ (`python --version`)
- Git (`git --version`)

## Checklist

<!-- opai-release-identity: application_version=0.2.1a1; release_stage=alpha.1; published_tag=v0.2.1a1 -->
- [ ] **CLI resolves.** `vesta version` begins with `Vesta 0.2.1a1 alpha.1`
      and reports either the exact packaged build SHA or the honest
      `development`/`unknown` fallback.
<!-- /opai-release-identity -->
      (Fallback before PATH reloads: `python -m opai version`.)
- [ ] **Project activates.** `vesta activate` writes project instructions and
      returns `"status": "active"`.
- [ ] **All six clients active.** `vesta doctor` shows `readiness: ready` and
      `client_integrations.summary.active` lists
      `claude, codex, copilot, cursor, cline`.
- [ ] **Client rule files exist:**
  - [ ] `AGENTS.md` (Codex) contains the Vesta managed block
  - [ ] `CLAUDE.md` (Claude) contains the Vesta managed block
  - [ ] `.github/copilot-instructions.md` (Copilot)
  - [ ] `.cursor/rules/opai.mdc` (Cursor)
  - [ ] `.clinerules/opai.md` (Cline)
- [ ] **Routing is read-only.** `vesta route "show git status"` prints a compact
      decision and does **not** create `.opaihub/ledger/usage.jsonl`.
- [ ] **Recording works.** `vesta route "fix a bug" --record` then
      `vesta savings` shows `routed_tasks >= 1` and a non-zero
      `estimated_savings_usd`.
- [ ] **Privacy holds.** `.opaihub/ledger/usage.jsonl` contains only
      `task_hash` values — never your raw prompt text.
- [ ] **Policy is enforced.** `vesta policy show` reports a profile; cloud tiers
      require confirmation.
- [ ] **Guarded gates fail closed.** `vesta guard action "git push"` returns
      `decision: deny`; adding `--confirm` returns `confirm`.
- [ ] **Eval passes.** `vesta models eval --no-write` reports
      `cheapest_tier_rate: 1.0` on the bundled fixtures.
- [ ] **Clean removal works.** `vesta uninstall` (dry-run) lists managed files;
      `vesta uninstall --confirm` removes them and is safe to re-run.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `op` / `opai` not found | Restart the shell, or use `python -m opai ...` |
| A client shows `broken`/`missing` | `vesta activate --repair` |
| Moved the repo and discovery broke | `vesta doctor` reports `stale_paths`; run `vesta activate --repair` |
| Superpowers not discovered | `vesta activate --install-superpowers` |
| Update the install | `vesta update` checks (add `--apply` to fetch, fast-forward, and reinstall); the desktop Settings › About page has the same check + Update now |

When every box is checked, Vesta is installed, every client is wired, and the
cost firewall is producing real, private savings numbers.
