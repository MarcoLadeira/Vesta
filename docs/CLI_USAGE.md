# Vesta CLI Usage

The CLI and the desktop GUI share **one core**: `vesta ask --model …` runs the
exact same pipeline (`opaihub.gui_pipeline.handle_gui_message`) the GUI chat
uses — same routing, same safety gates, same ledger, same receipts.

## Ask (the everyday command)

```sh
# Free, local-first (unchanged classic behavior — $0, cache + local model):
vesta ask "summarize my changes"

# Stream your connected account with live activity (same core as the GUI):
vesta ask --model claude:opus "why is this test flaky?"
vesta ask --model codex "refactor the auth module"        # default codex model
vesta ask --model copilot:gpt-5.2 --mode plan "plan the migration"
vesta ask --model auto "quick question"                    # Vesta routes cheapest

# Machine-readable (for scripts/automation):
vesta ask --model claude:sonnet --json "list the public API of opai/activity.py"
```

What you see while it runs (Claude Code-style activity):

```
✓ Preparing request
✓ Read project context  (3 routing step(s))
✓ Selected model: account:claude:opus
◐ Sending to Claude
✓ Read file: opai/activity.py
… Waiting for Claude · 16s elapsed (Ctrl+C to stop)

<answer streams here>

✓ done in 42s · $0.0312 spent
```

- **Ctrl+C really cancels** — it sets the cancel flag, the provider subprocess
  is terminated, partial output is kept, and the exit code is `130`.
- The footer is **honest**: real cost when the provider reports it (claude),
  estimates labeled as estimates, and paid calls are never shown as "savings".
- Exit codes: `0` answered · `130` cancelled · `2` anything else — scriptable.

### Model shorthand

| You type | Runs |
| --- | --- |
| `--model claude` / `claude:sonnet\|opus\|haiku` | your Claude account CLI |
| `--model codex[:gpt-5.5\|…]` | your Codex account CLI |
| `--model copilot[:model]` | your Copilot account CLI |
| `--model auto` | Vesta routes the cheapest safe path |
| (no `--model`) | classic free local-only path, `$0` |

### Run modes (`--mode`, with `--model`)

`ask` (default, read-only) · `plan` (read-only) · `safe-auto` (edits after safe
checks) · `approve-edits` · `full-auto`. Same semantics and safety gates as the
GUI mode picker; panic mode still blocks paid calls.

## The rest of the toolbox

| Command | What it does |
| --- | --- |
| `vesta gui` | the desktop app (web-rendered); `--classic` for the Qt fallback |
| `vesta route "task"` | route + record a task through the cost firewall |
| `vesta cost` / `vesta savings` | spend + savings from the ledger |
| `vesta receipt [--svg]` / `receipt verify` | signed, shareable savings receipt |
| `vesta budget …` | caps, panic mode, gates |
| `vesta models list` | Auto, Claude, Codex, Copilot, and local model choices |
| `vesta doctor` | client/wrapper readiness + repair |
| `vesta proxy <agent> "task"` | inline-capture shim (gate → route → record) |
| `vesta context pack` | tiny targeted context instead of whole files |
| `vesta slim` | write AI ignore files; report context bloat |

Every command supports `--project` and most support `--json`.

## Guarantees

- **No hidden spend:** paid models run only when you name them (or explicitly
  confirm); Auto never auto-fires a paid call; panic mode blocks paid entirely.
- **No secrets:** prompts are never stored raw; ledger/receipts are redacted.
- **Tested without paid calls:** the CLI streaming path is covered by
  `tests/test_cli_stream.py` with injected fakes — the real CLIs are never
  launched in tests.
