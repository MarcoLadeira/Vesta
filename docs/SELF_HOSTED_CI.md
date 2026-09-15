# Trusted self-hosted CI

The repository-owned Windows runner is post-merge evidence, never a pull-request
merge gate. Untrusted code must stay on GitHub-hosted read-only runners.

## Execution contract

`.github/workflows/ci-selfhosted.yml` runs after a push to `main` or a dispatch
from `main`. It has two jobs:

1. `Trusted - runner health preflight` runs on `ubuntu-latest`, queries the
   Actions runner API through the `vesta-runner-health` environment, and requires
   an idle, online runner carrying `self-hosted`, `Windows`, and `X64`. It writes
   exact-SHA evidence. Offline, busy, missing, malformed, or inaccessible
   inventory exits non-zero as `runner_unavailable`/`infrastructure_blocked`
   before a long-lived job is queued.
2. `Trusted - self-hosted fast qualification` depends on that preflight, checks
   out exactly `github.sha` without credentials, creates a per-run venv, installs
   pinned dependencies, and runs the Python component with an explicit
   candidate SHA.

Both evidence artifact names include run ID, attempt, and candidate SHA. A
non-main dispatch cannot execute the trusted job. Do not add `pull_request`,
`pull_request_target`, a generic unlabelled runner target, or checkout token
persistence.

## One-time Windows setup

1. Create the `vesta-runner-health` environment, restrict deployment branches to
   `main`, and add `VESTA_RUNNER_HEALTH_TOKEN`. Use a short-lived fine-grained
   token scoped only to this repository with repository Administration **read**
   (plus Metadata read); GitHub's default workflow token cannot list repository
   runners. Do not grant write permission.
2. Open <https://github.com/MarcoLadeira/OPai/settings/actions/runners/new>,
   select Windows x64, and follow GitHub's current download instructions.
3. Configure with the one-time token and add labels `Windows,X64` if they are not
   already automatic. The final label set must include all three required labels.
4. Ensure `python` is on the service account's `PATH`.
5. Install/start the runner as a Windows service so recovery does not depend on
   an interactive terminal:

   ```powershell
   ./config.cmd --url https://github.com/MarcoLadeira/OPai --token <one-time-token> --labels Windows,X64
   ./svc.cmd install
   ./svc.cmd start
   ```

Registration tokens are secrets: never paste one into an issue, log, config
file, or workflow. Rotate/re-register after suspected exposure.

## Health and recovery

- Confirm the runner is **Online / Idle** in repository settings.
- Dispatch `Vesta CI (trusted self-hosted)` from `main`. The hosted health job
  should qualify before the Windows job starts.
- If health reports `runner_unavailable`, inspect the Windows service, outbound
  HTTPS/DNS, runner version, disk space, and label spelling. Do not reroute the
  job to an unlabelled machine or mark it optional.
- If GitHub Actions billing prevents the hosted health job from starting, the
  workflow is infrastructure blocked even if the local service is healthy.

The runner needs outbound access to GitHub but no inbound port. Keep the service
account non-administrative where practical, isolate its work directory, do not
store provider/signing credentials on it, and periodically replace the work
directory. See [CI qualification and merge governance](CI_QUALIFICATION.md) for
required hosted checks and external GitHub configuration.
