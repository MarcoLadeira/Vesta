# Self-hosted CI runner (green checks at $0)

This private repo's GitHub-hosted Actions minutes are capped, so hosted runners
abort every job at startup (`0 steps`, ~2s) and show red — even though the code
is fine and merges land normally. A **self-hosted runner** runs the CI gate on
your own machine instead, so checks go **green at no cost** while the repo stays
private.

- `.github/workflows/ci-selfhosted.yml` runs on `[self-hosted]` for every push,
  pull request, and manual dispatch. It installs OPai + the check tools and runs
  `python scripts/ci_local.py` (ruff format + check, the full unittest suite,
  `opaihub validate`, bandit).
- `.github/workflows/ci.yml` (the hosted matrix) is **manual-only** now, so it no
  longer fills the Actions tab with red. Trigger it from the Actions tab when you
  want the full Windows + cross-OS wheel matrix (e.g. before a release, or once
  you raise the Actions spending limit).

## One-time setup (~2 minutes)

Register a runner on your machine — this is the only step that needs you, because
it uses a registration token tied to your account.

1. Open **`https://github.com/MarcoLadeira/OPai/settings/actions/runners/new`**
   (Repo → **Settings** → **Actions** → **Runners** → **New self-hosted runner**).
2. Pick your OS (Windows) and follow the **Download** commands shown on that page.
   They look like this (use the exact token GitHub shows you):

   ```powershell
   # In a folder like C:\actions-runner
   mkdir C:\actions-runner; cd C:\actions-runner
   Invoke-WebRequest -Uri https://github.com/actions/runner/releases/download/<ver>/actions-runner-win-x64-<ver>.zip -OutFile runner.zip
   Expand-Archive runner.zip -DestinationPath .
   ./config.cmd --url https://github.com/MarcoLadeira/OPai --token <TOKEN_FROM_THE_PAGE>
   ```

3. When `config.cmd` asks for labels, just press Enter (the default `self-hosted`
   label is what the workflow targets). Make sure `python` is on this machine's
   `PATH` (`python --version` should work).
4. Start the runner:

   ```powershell
   ./run.cmd
   ```

   Leave that window open — it processes jobs while running. To make it a
   background Windows service instead (starts with the machine, no window):

   ```powershell
   ./svc.cmd install
   ./svc.cmd start
   ```

## Verify it works

- The runner shows **Idle** at
  `https://github.com/MarcoLadeira/OPai/settings/actions/runners`.
- Trigger a run: Actions tab → **OPai CI (self-hosted)** → **Run workflow**, or
  just push a commit. The job runs on your machine and reports green/red.

## Security notes

- A self-hosted runner executes whatever CI a branch defines. Keep it on a repo
  where you trust the code (your own solo repo is fine). If you ever accept
  outside pull requests, require approval before running workflows on them
  (Settings → Actions → *Fork pull request workflows*).
- The runner needs outbound network to reach GitHub; it does not open any inbound
  ports.

## Turning hosted CI back on

If you raise the Actions spending limit or make the repo public later, restore
automatic hosted CI by adding the `pull_request:` and `push:` triggers back to
`.github/workflows/ci.yml` (see the comment at the top of that file), and you can
retire the self-hosted runner if you no longer want it.
