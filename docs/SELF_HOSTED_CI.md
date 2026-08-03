# Self-hosted CI runner (trusted post-merge evidence)

The self-hosted runner provides a post-merge trusted-machine signal. The hosted
credential-free workflow is the mandatory PR gate; this runner never evaluates
untrusted pull-request code.

- `.github/workflows/ci-selfhosted.yml` runs on `[self-hosted]` after each push
  to `main`, and by deliberate manual dispatch. It creates an isolated venv,
  installs the pinned `requirements-ci.txt` toolchain and runs the fail-closed
  `fast` profile with a JSON evidence artifact.
- `.github/workflows/ci.yml` automatically runs the hosted PR/main gate and the
  scheduled full/native lanes. Its runners have read-only repository access and
  no protected provider or signing credentials.

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
- Trigger a run: Actions tab → **OPai CI (trusted self-hosted)** → **Run
  workflow**, or push a commit to `main`. The job runs on your machine and
  reports green/red with an evidence artifact.

## Security notes

- A self-hosted runner executes whatever trusted `main` code defines. Do not add
  a `pull_request` trigger or expose it to fork PRs: a contributor could alter a
  workflow and execute arbitrary code in the long-lived workspace.
- The runner needs outbound network to reach GitHub; it does not open any inbound
  ports.

For the required check names, release evidence rules and GitHub ruleset setup,
see [CI qualification and merge governance](CI_QUALIFICATION.md).
