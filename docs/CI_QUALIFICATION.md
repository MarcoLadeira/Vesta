# CI qualification and merge governance

Issue [#621](https://github.com/MarcoLadeira/OPai/issues/621) defines one rule:
qualification is evidence about an exact commit, not merely a green workflow.
Missing tools, skipped required checks, stale evidence, runner outages, absent
credentials, and failed artifact uploads are non-success states.

## Automatic topology

| Lane | Automatic trigger | Trust boundary | Evidence |
| --- | --- | --- | --- |
| Required Python | PRs/merge queue targeting `main`, `release/**`, or `rc/**`; every push to those branches; weekly full run | GitHub-hosted, read-only, credential-free | Ruff format/lint, generated lifecycle drift, unittest, registry validation, Bandit including release scripts, enforcing secret scan |
| Required hostile environment | Same as required Python | GitHub-hosted, read-only, fake provider keys and hostile keyring only | unittest and pytest; any skip/failure remains non-green |
| Required web | Same as required Python | GitHub-hosted Windows, read-only, no secrets | locked `npm ci`, audit, tokens, Vitest, locked Chromium install, Playwright with no retry |
| Full supply chain | Monday 03:17 UTC and manual `full` dispatch | GitHub-hosted, credential-free | full pytest plus `pip-audit` |
| Native wheel | Monday 03:17 UTC and manual `full` dispatch | Ephemeral Windows/Linux/macOS runners | isolated wheel install/smoke for the exact SHA |
| Trusted self-hosted | Every push to `main`; main-only dispatch | Long-lived labelled Windows runner after a hosted health preflight | exact-SHA Python component; never PR code |
| Provider canary | Tuesday 04:43 UTC and main-only dispatch | `vesta-provider-canary` protected environment, non-production accounts, bounded budget | exact-SHA selected-provider fixed-response evidence |
| Desktop/release | Every annotated `v*` tag for unsigned rehearsal; protected main dispatch for production | Hosted source/web/native; provider and signing environments only after ancestry checks | same-run source, web, provider, platform build, signature, smoke, archive and attestation evidence |
| Release source preflight | Every `release/**`/`rc/**` push and manual dispatch | Credential-free, read-only source qualification | exact candidate, mandatory `full` profile, typed source evidence; final artifact qualification remains pending |

Ordinary branch concurrency is keyed by workflow plus PR/ref and cancels
superseded work. Desktop release concurrency is keyed by tag and channel and
does not cancel in-progress candidates. Every uploaded artifact name includes
`github.run_id` and `github.run_attempt`; qualification evidence also records
the candidate and checked-out SHA. A rerun cannot overwrite a prior attempt.

The source-preflight workflow deliberately has no tag trigger and does not read
an artifact manifest from candidate-controlled source. It qualifies the exact
source SHA without claiming that release artifacts exist. Final Windows/macOS
qualification is owned by the protected `desktop-artifacts.yml` workflow, which
builds the outputs and validates native signing/notarisation, provider evidence,
archive smoke, and attestations in the same run. The standalone preflight can
audit bounded schema-3 files, but it returns
`infrastructure_blocked` / `artifact_verifier_unavailable` unless a trusted
native/cryptographic verifier is injected by that protected workflow; structural
metadata by itself is never proof.

## Required merge checks

The source of truth is [`.github/required-checks.json`](../.github/required-checks.json).
The exact checks for `main` are:

- `Required - Python quality (3.13)`
- `Required - hostile-environment Python suites`
- `Required - web UI security and E2E`

The manifest maps each stable check name to its workflow, job, local component,
trusted GitHub app, and required internal check IDs. `tests/test_ci_architecture.py`
rejects drift between the manifest, workflows, local components, triggers,
permissions, action pins, trust boundaries, and artifact names.

Vesta's GitHub check consumer resolves the repository default branch to an exact
commit, then loads this manifest from that immutable tree/blob rather than from
the candidate checkout. Deleting or shrinking the manifest in a PR therefore
cannot downgrade the gate. It requires every exact name from the current PR
head, verifies the `github-actions` producer and workflow run
path/name/event/SHA, rejects duplicate/missing/skipped/neutral/stale checks, and
re-reads the head to close a moving-head race.

On a pull request, required jobs checkout GitHub's synthetic `github.sha` merge
candidate so they exercise the head together with the current target branch.
Evidence separately records `pull_request.head.sha` as `source_sha` and proves
that source is a direct parent of the tested merge commit. Main, release-branch,
and merge-queue runs bind source and candidate to the event commit itself.

## Local profiles

The aggregate `fast` profile is the same contract as all three PR jobs. Hosted
CI uses `--component` only to parallelize it.

```powershell
# Full PR-equivalent contract: Python + hostile + web.
python scripts/ci_local.py --profile fast --manifest .vestahub/ci-evidence/fast.json

# One parallelizable component.
python scripts/ci_local.py --profile fast --component python --manifest .vestahub/ci-evidence/python.json
python scripts/ci_local.py --profile fast --component hostile --manifest .vestahub/ci-evidence/hostile.json
python scripts/ci_local.py --profile fast --component web --manifest .vestahub/ci-evidence/web.json

# Adds full pytest and dependency audit.
python scripts/ci_local.py --profile full --manifest .vestahub/ci-evidence/full.json

# Current-platform clean wheel install.
python scripts/ci_local.py --profile native --manifest .vestahub/ci-evidence/native.json

# Protected only: requires explicit non-production/provider/model/budget gates.
python scripts/ci_local.py --profile provider-canary --candidate-sha <sha>

# Aggregate release contract; it intentionally fails without protected inputs.
python scripts/ci_local.py --profile release --candidate-sha <sha>
```

Local runs infer `HEAD` and record that the identity was inferred. An inferred
run may exercise an edited working tree, but its manifest sets
`candidate.promotable` to false. Any run with an explicit `--candidate-sha`
(including every hosted lane) also requires a clean tracked and untracked
working tree, so evidence cannot name `HEAD` while silently testing other
content. When `CI` is set, `--candidate-sha` is mandatory and must exactly equal
`git rev-parse HEAD`. The only successful verdict is `qualified`.

Each schema-v2 manifest records profile/component/version, declared inventory,
candidate, source, parent and checkout SHA, whether a merge candidate was
tested, workspace cleanliness/promotability, run ID/attempt/event, platform, tool versions,
timestamps, bounded artifact locations, and one record per check. Failure output
is passed through Vesta's canonical credential redactor and capped at 4,000
characters. Raw browser HTML, traces, screenshots, provider responses, and
credentials are not uploaded.

Terminal verdicts distinguish `test_failed`, `policy_failed`, `security_failed`,
`build_failed`, `infrastructure_blocked`, `runner_unavailable`, and
`credential_unavailable`. A required unavailable executable/module is
`unavailable` + `failed`, never skipped. Command timeouts and known registry/
network outages are infrastructure rather than product failures.

## Reproducible tools and caches

- Python CI/build bootstrap tools are pinned in `requirements-ci.txt`; editable
  Vesta installation uses `--no-deps --no-build-isolation`, followed by
  `pip check`.
- JavaScript uses only the committed `package-lock.json` through `npm ci`.
- setup-python/setup-node caches are keyed by the relevant lock/requirements
  files. They cache downloads, never test results or qualification verdicts.
- `scripts/check_secrets.py` enforces findings because bare
  `detect-secrets scan` exits zero even when it finds secrets. The committed
  baseline contains reviewed synthetic test fixtures by hash only; a new or
  changed fingerprint is a security failure.
- All third-party Actions are full commit-SHA pins.

## Fork, self-hosted, provider, and signing boundaries

- `ci.yml` has no secrets, environment, write permission, self-hosted runner, or
  `pull_request_target` trigger. Fork code executes only on ephemeral hosted
  runners.
- `ci-selfhosted.yml` has no PR trigger. A hosted job queries the Actions runner
  API for an idle online runner with exactly the required labels before the
  long-lived machine is queued. Dispatch from a non-`main` ref cannot reach it.
- Provider jobs run only from trusted `main` and require the protected
  `vesta-provider-canary` environment. The runner enforces a non-production
  acknowledgement, an explicit provider/model allowlist, and exactly one fixed
  remote prompt per selected provider. Qualification requires the sandbox
  ledger to bind that call to the exact provider/model, provider-observed token
  usage, and a known non-negative cost; local, cached, fallback, estimated, or
  missing evidence fails closed. The cumulative observed cost must not exceed
  `VESTA_PROVIDER_CANARY_MAX_USD`, which itself may not exceed USD 1.00. This is
  a post-call qualification threshold, not a preventive billing control, so the
  non-production account must also enforce a hard provider-side spend cap.
- Production provider code is checked out only after a credential-free job
  proves the annotated tag commit is reachable from the reviewed main workflow
  revision. Signing remains in `vesta-production-signing`.

## GitHub administrator action

Configure a ruleset targeting `main`, `release/**`, and `rc/**` with:

1. Pull requests required before merge.
2. All three exact checks above, from GitHub Actions, required and up to date.
3. At least one approval, stale approvals dismissed, Code Owner approval for
   workflow/CI/release files, and conversation resolution.
4. No bypass, force push, or branch deletion.
5. Protected `vesta-runner-health`, `vesta-provider-canary`, and
   `vesta-production-signing` environments, main-only deployment branches,
   required reviewers where credentials can spend/sign, and self-review
   disabled. The runner-health environment needs a read-only fine-grained
   `VESTA_RUNNER_HEALTH_TOKEN` with repository Administration read permission.

As audited on 2026-08-09, GitHub returned HTTP 403 for both rulesets and legacy
branch protection because this is a private repository on a plan without those
features. `main` is therefore currently unprotected. Upgrade the plan or make
the repository public, then apply the settings above; do not remove required
checks to fit the plan.

The same audit found Actions jobs failing before their first step due account
billing/spend limits, the only self-hosted runner offline, and no protected
environments/secrets/variables configured. Those are external
`infrastructure_blocked` conditions, not repository success. The macOS desktop
lock and real Windows/macOS signing/install evidence remain owned by
[#290](https://github.com/MarcoLadeira/OPai/issues/290),
[#355](https://github.com/MarcoLadeira/OPai/issues/355), and
[#624](https://github.com/MarcoLadeira/OPai/issues/624).

## Failure drills and diagnosis

The focused test suite proves missing Ruff/pytest/Bandit/Node-style executables,
timeouts, inventory deletion, candidate mismatch/head movement, runner outage,
provider prerequisite failure, stale artifacts, security findings, skipped/
neutral checks, rerun artifact identity, and fork/permission boundaries.

For a real failure:

1. Read the manifest `verdict`, `classification`, `reason`, candidate and
   checkout SHA before reading individual checks.
2. If evidence is absent, treat the artifact/upload/install/startup path as
   infrastructure blocked; never reuse another run's evidence.
3. Fix product/security/policy failures on a new commit and requalify that SHA.
4. Retry infrastructure only for the same immutable candidate; the new
   `run_attempt` keeps both records.
5. For release evidence, verify every downloaded manifest names the exact tag
   commit and current run before approving a protected job.
