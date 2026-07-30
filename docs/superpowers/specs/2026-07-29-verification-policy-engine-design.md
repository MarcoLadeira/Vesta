# Verification policy engine design

Issue: #538, the first implementation dependency of epic #522.

## Purpose

Before an edit-capable OPai task starts, resolve and persist a deterministic
verification contract. The contract states what must be checked, why each
requirement applies, which source introduced it, what evidence execution must
later produce, and whether unresolved human review remains. It is a policy
decision, not a command runner or a terminal completion verdict.

## Scope boundary

This change delivers policy resolution, linting, durable effective-policy
artifacts, CLI inspection, and GUI-pipeline capture. Issue #539 will execute
the declared checks, collect immutable command evidence, and compute terminal
verification verdicts. Issue #377 owns visual presentation; this change only
exposes structured data to that surface.

## Architecture

`opaihub.verification_policy` will be the sole resolver. It exposes immutable
schema objects, policy loading and linting, repository classification,
acceptance-criteria parsing, effective-policy resolution, and artifact
persistence. All public payloads are JSON-compatible and include both a
schema version and resolver-semantics version.

The resolver receives only trusted inputs: explicit OPai task metadata,
repository metadata captured by OPai, and policy files. Provider prose, prompt
text, tool output, and package-script labels are untrusted and cannot remove
or satisfy required checks.

## Deterministic source hierarchy

Resolution starts with versioned built-in safe defaults, then applies a team
overlay, repository overlay, and task-derived risk/acceptance overlay. Each
applied operation records its source and reason. An overlay may add a check,
promote an optional check to required, add a condition, or add a human-review
requirement. It cannot remove or downgrade an inherited required check.

Conflicts, unsupported conditions, unknown check kinds, duplicate check IDs,
invalid commands, version incompatibility, and attempts to weaken inherited
requirements produce a blocked policy artifact. The resolver never falls back
to permissive defaults after a malformed policy is discovered.

Repository policy lives in the committed `opai-verification-policy.yaml`.
Team policy may carry a `verification_policy` overlay. The effective artifact
lists every contributing source, including the absence of optional overlays.

## Defaults and task classification

Repository classification uses only local structural facts such as Python
package metadata, Node manifests, source layout, changed-path categories, and
the task mode. It does not trust repository-defined script names as proof of
tool availability. Built-in defaults declare suitable check kinds and
evidence/environment requirements; unavailable tooling is explicit rather
than silently ignored.

Task classification recognizes documentation-only work, ordinary code edits,
mixed frontend/backend work, migrations, security-sensitive work, CI/release
changes, and policy changes. High-risk categories require stronger static or
human-review gates. Acceptance text is represented as normalized automated
assertions where recognized and as explicit unresolved human-review
requirements otherwise.

## Durable artifact and surfaces

An effective-policy artifact contains its digest, policy/resolver versions,
input classification, source hierarchy, check decisions, acceptance criteria,
human reviews, lint findings, blocked/degraded status, and storage reference.
It is atomically persisted under OPai's local task state before an
edit-capable GUI run dispatches a provider.

The GUI pipeline adds the artifact reference and safe policy summary to the
task packet and run payload. `opai verify policy --project <path> --task
<text> --mode <mode> --delivery <outcome> --json` calls the same pure resolver
for CLI and CI use. A dry run never executes repository commands.

## Validation

Tests will cover schema validation, source provenance, deterministic digests,
malformed and weakening overlays, task/risk defaults, automated versus human
acceptance requirements, no-test repositories, Python and Node fixtures,
docs-only and migration fixtures, policy-upgrade compatibility, artifact
atomicity, CLI/pipeline parity, and provider-prose non-interference.

The existing completion-contract suite is the baseline regression guard;
issue #539 will add command-execution and terminal-verdict fixtures.
