# Issue #621 CI qualification design

## Purpose

OPai must treat qualification as evidence about one immutable candidate, not as
the presence of a green-looking workflow. Every required lane must run
automatically in its trust domain, record the checked-out commit, and fail
closed when a tool, runner, credential, artifact, or prerequisite is absent.

## Audited failure modes

- Pull-request jobs currently check out GitHub's synthetic merge ref, so their
  evidence is not bound to the pull-request head SHA.
- The local `fast` profile covers only the Python portion of the hosted gate;
  hostile-environment and web checks can therefore be omitted locally.
- Local commands have no timeout and classify every non-zero exit as a product
  failure. Provider and release profiles are placeholders rather than usable
  contracts.
- The release preflight can report ready while tests and artifacts are skipped,
  and artifact verification trusts metadata rather than immutable same-SHA
  qualification evidence.
- The self-hosted workflow can queue forever and manual dispatch can select an
  unmerged ref for a privileged runner.
- Required-check consumers accept skipped/neutral checks and do not verify the
  exact check inventory or candidate identity.
- Release artifact names are not rerun-idempotent, provider qualification has no
  owning workflow, and browser failure output is neither bounded nor proven
  redacted.

GitHub currently adds three external blockers: Actions jobs cannot start because
of account billing/spend limits, the only self-hosted runner is offline, and this
private repository's plan rejects branch protection/ruleset configuration. The
repository must expose those as blocked infrastructure; it cannot manufacture a
successful status around them.

## Qualification contract

`.github/required-checks.json` is the versioned source of truth. It defines:

- stable PR-required check names;
- the local component and required check IDs behind each hosted job;
- automatic trigger requirements for PR, main, scheduled, native, provider, and
  release-candidate workflows;
- protected trust domains and exact-SHA evidence expectations.

`tests/test_ci_architecture.py` executes the contract against workflow YAML. It
rejects trigger drift, mutable action references, excessive permissions,
privileged PR execution, missing exact-head checkout, hidden failures,
non-idempotent artifact names, and release/provider trust-boundary regressions.

## Hosted and local topology

The credential-free hosted workflow owns three stable mandatory jobs:

1. Python quality: formatting, lint, generated projections, maintained Python
   tests, registry validation, architecture checks, and Bandit.
2. Hostile environment: the Python suites with populated fake credentials and a
   hostile keyring, proving tests do not reach providers or ambient credentials.
3. Web security/E2E: the locked npm install, audit, token checks, Vitest, and
   Playwright contract tests.

Every job checks out the exact PR head or push SHA and passes it to the evidence
runner. Pushes to `main`, release branches, and RC branches create fresh evidence
for their own SHA. Scheduled qualification adds dependency/security scans and a
native wheel matrix. A local `fast` invocation runs all mandatory components;
`--component` lets hosted jobs run them in parallel without changing the
contract.

The evidence schema records the expected inventory, executed/unavailable state,
failure class, bounded redacted diagnostics, tool versions, platform, timing,
candidate SHA, run identity, and final typed verdict. Required checks cannot be
optional. Missing executables/modules and timeouts are infrastructure failures;
test, security, build, and policy failures retain their own classifications.

## Trust boundaries

- Pull requests receive read-only tokens, no protected environments, and only
  GitHub-hosted runners.
- The self-hosted workflow runs only the immutable `main` commit. A hosted
  health preflight first checks the required labelled runner and emits
  `runner_unavailable` evidence instead of leaving the authoritative gate queued.
- Provider canaries run only on a schedule or reviewed manual dispatch in the
  `opai-provider-canary` environment. They require explicit non-production,
  budget, and cloud-test acknowledgements and never run on pull requests.
- Signing remains in `opai-production-signing`; untrusted code never receives
  signing/provider credentials. Production release qualification consumes only
  evidence built in the same run for the exact tag commit.

## Release identity and reruns

Release and native evidence are keyed by candidate SHA, GitHub run ID, run
attempt, and platform. Upload names are therefore immutable across reruns.
Every consumer validates both the declared candidate and the checked-out source.
Release preflight treats missing tests, artifacts, provider evidence, native
evidence, and provenance as blocked rather than ready. Unsigned tag rehearsals
run automatically; production signing remains a protected manual operation.

## Failure semantics

The canonical terminal states are `qualified`, `test_failed`, `security_failed`,
`build_failed`, `infrastructure_blocked`, `runner_unavailable`,
`credential_unavailable`, `artifact_upload_failed`, and
`cancelled_superseded`. Only `qualified` exits zero. Skipped or neutral required
checks, stale evidence, and unknown state never qualify a candidate.

## Administrative enforcement

The repository contract and a drift-check command provide the exact branch-rule
check names. GitHub must require all three checks on `main`, require current-branch
approval, dismiss stale approvals, require conversation resolution, prohibit
bypass/force-push/deletion, and require the branch to be up to date. Until the
private repository is upgraded or made public and Actions billing is restored,
those settings and real hosted evidence remain externally blocked and must be
reported as such.
