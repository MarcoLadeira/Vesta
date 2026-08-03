# CI qualification and merge governance

Issue [#621](https://github.com/MarcoLadeira/OPai/issues/621) makes a green
result meaningful: required work must have actually run for the candidate
commit. A missing tool, missing protected evidence, cancelled stale run, or
offline trusted runner is never a passing qualification.

## CI topology

| Lane | Trigger | Trust boundary | Purpose |
| --- | --- | --- | --- |
| Required hosted fast | Pull requests to `main`; pushes to `main` | Credential-free GitHub-hosted runner; `contents: read` only | Format, lint, unit tests, registry validation, Bandit, hostile-environment Python tests, web tests and E2E |
| Scheduled hosted full | Every Monday and manual `full` dispatch | Credential-free GitHub-hosted runner | Fast profile plus pytest, dependency audit and secret scan |
| Scheduled native wheel | Every Monday and manual `full` dispatch | Ephemeral GitHub-hosted Windows, macOS and Linux runners | Build, isolated install and smoke the exact checkout on each OS |
| Trusted self-hosted fast | Push to `main` and reviewed manual dispatch | Repository-owned runner only | A post-merge local-runner signal; never a PR merge check |
| Provider canary | Protected provider workflow owned by its qualification issue | Protected environment and budgeted non-production account | Real-provider evidence; intentionally not exposed to arbitrary PR code |
| Release candidate | Protected promotion workflow | Protected environments, immutable candidate and signing boundary | Consumes current native/provider/package evidence before publication |

`ci.yml` uses a per-event/per-ref concurrency group. GitHub cancels obsolete
work in the same lane, but a scheduled/full run cannot cancel the latest fast
PR/main result. A new commit receives a distinct check run; a cancelled run
cannot qualify the latest head SHA. Evidence artifact names include both `github.run_id` and
`github.run_attempt`, so recovery/reruns do not overwrite previous evidence.

## Local profiles and evidence

```powershell
# Required PR-equivalent checks.
python scripts/ci_local.py --profile fast --manifest .opaihub/ci-evidence/fast.json

# Broader tests and network-dependent audits.
python scripts/ci_local.py --profile full --manifest .opaihub/ci-evidence/full.json

# Build and smoke an isolated wheel on this operating system.
python scripts/ci_local.py --profile native --manifest .opaihub/ci-evidence/native.json

# Release identity is checked against HEAD and fails closed without protected
# provider evidence. It is a verifier, not a way to bypass release governance.
python scripts/ci_local.py --profile release --candidate-sha <40-character-sha>
```

`--fast` and `--full` remain compatibility aliases. CI tools are pinned in
[`requirements-ci.txt`](../requirements-ci.txt); JavaScript dependencies are
installed only with `npm ci` from the committed lockfile. Python dependency
caches are deliberately not used in the qualification workflows. The Node cache
is keyed by the lockfile via `actions/setup-node`, so a lockfile change invalidates
it.

Each JSON evidence manifest has a schema and profile version, checked-out and
candidate SHA, start/end/duration, platform and Python information, selected
tool versions, an artifact path, overall verdict/reason/classification, and one
record per check. Check records contain the fixed command, whether it is
required, network use, duration, return code where applicable, and separate
execution/outcome fields:

| Execution | Outcome | Meaning |
| --- | --- | --- |
| `executed` | `passed` | The command ran and succeeded. |
| `executed` | `failed` | The command ran and found a product/quality failure. |
| `unavailable` | `failed` | A required module/executable or protected qualification was unavailable. This is a red result. |
| `unavailable` | `skipped` | An explicitly optional check could not run. No current required profile declares optional checks. |

The manifest classifies command failures as `product`, and missing tooling or
protected evidence as `infrastructure`. Release/provider profiles report
`infrastructure_blocked` rather than fabricating a product failure or a green
result. CI publishes only this bounded JSON evidence and browser failure reports
for seven days; no command stdout, credentials or provider responses are placed
in the evidence manifest.

## Required GitHub ruleset configuration

Repository YAML cannot configure branch protection. A repository administrator
must create or update the `main` branch ruleset after this change:

1. Target the default branch (`main`) and require a pull request before merge.
2. Require these exact status checks from GitHub Actions:
   - `Required - Python quality (3.13)`
   - `Required - hostile-environment Python suites`
   - `Required - web UI security and E2E`
3. Require the branch to be up to date before merge and dismiss stale approvals
   when new commits are pushed.
4. Require review/Code Owners approval for changes under `.github/workflows/`,
   `scripts/ci_local.py`, `requirements-ci.txt`, release workflow files and this
   document. Restrict required checks to the approved GitHub Actions app if the
   ruleset interface offers that control.
5. Do **not** make a self-hosted, provider, scheduled-native or release job a PR
   required check. Those jobs do not run on every untrusted PR by design. Make
   their current immutable evidence a protected-environment release requirement
   instead.

An administrator should verify that all three names appear on a test PR after
merging this workflow, then capture the ruleset export/screenshot in release
governance evidence. A required check that has not appeared at least once cannot
be selected in GitHub's UI. The canonical machine-readable list is
[`required-checks.json`](../.github/required-checks.json); its repository test
keeps the list, workflow display names and this documentation aligned.

At the time this guide was updated, GitHub returned HTTP 403 for both the
ruleset and legacy branch-protection APIs on this private repository because the
current plan does not include those features. Until the repository is made
public or moved to a plan with branch protection, this workflow still produces
automatic evidence but GitHub cannot enforce it as a merge requirement. Record
the successful ruleset/branch-protection configuration as release-governance
evidence once that platform prerequisite is available.

## Trust, release and recovery rules

- Fork PRs get only the hosted `pull_request` jobs, a read-only token and no
  provider/signing secrets. `ci-selfhosted.yml` deliberately has no
  `pull_request` trigger.
- The provider-canary and release profiles are fail-closed placeholders for
  protected evidence. They cannot claim qualification until the workflows owned
  by #290, #355, #611 and the provider-canary work attach evidence for the same
  SHA. This prevents stale Windows/macOS/provider reports from being reused.
- Promotion must verify the candidate SHA, native Windows/macOS manifests,
  package artifact hashes, scans and protected provider evidence before a
  publish credential is available. A tag that names a different commit is a
  blocker, not a rerun target.
- Runner outage, quota/network failure and artifact upload failure are
  `infrastructure_blocked`. Retry only the same immutable candidate and use a
  new artifact name; never declare a prior run's artifact as evidence for a
  newer SHA.
- Third-party Actions in the qualification workflows are pinned to immutable
  commit SHAs. Upgrade a pin in a reviewed change with the release/version noted
  beside it.

## Verification drills

Run these before marking a release process qualified:

1. Temporarily remove Ruff, Bandit, pytest or another required module in a
   disposable environment. The profile must exit non-zero and emit
   `unavailable`/`failed` evidence naming that module.
2. Introduce a controlled Python, web-token, frontend and static-security
   failure in separate non-merged PRs. Confirm the correct required hosted job
   starts for each exact head SHA and fails.
3. Open a fork PR that changes a workflow. Confirm it has no provider/signing
   environment and no self-hosted job.
4. Stop the trusted runner or interrupt artifact upload. Confirm the evidence is
   blocked/absent, no duplicate artifact is published, and a retry gets a new
   run-attempt artifact name.
5. Move a candidate SHA/tag while qualification is running. The release profile
   must reject the mismatched SHA; rebuild/requalify the new immutable candidate.
