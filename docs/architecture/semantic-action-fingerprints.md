# Semantic action fingerprints

Status: implemented for the provider tool-loop controller  
Derivation version: `semantic-action-v1`  
Issue: [#649](https://github.com/MarcoLadeira/OPai/issues/649)

## Purpose

The convergence controller used to identify repetition with
`tool_name + raw_arguments`. That treated `read_file`, `cat`, `Get-Content`,
and Python `open()` as four unrelated actions even when they read the same
unchanged file. The same gap applied to search wrappers, Git aliases,
verification commands, provider prompts, and cosmetically reworded failed
strategies.

`opaihub.action_fingerprint` now derives a deterministic semantic identity for
those actions. The controller uses it to:

- warn on the second equivalent success;
- suppress the next low-value equivalent success;
- count equivalent failures against one retry bound;
- require a new verification after repository freshness changes;
- allow an explicit caller override while recording its measured latency; and
- explain every allow, warning, block, override, and freshness decision in the
  tool trace.

This mechanism prevents execution. It never grants execution, retry authority,
consent, or permission.

## Fingerprint schema

The public fingerprint contains only fixed labels, numeric line ranges, booleans,
and truncated SHA-256 digests:

| Field | Meaning |
| --- | --- |
| `version` | Derivation contract version. Different versions compare as unknown. |
| `operation_family` | File read, repository search, verification, repository read, GitHub read, provider query, mutation, external effect, or unknown. |
| `capability` | A fixed capability label such as `read_source`, `search_text`, `test`, or `status`. |
| `target_digest` | One-way identity of the canonical path, repository, or remote resource. |
| `arguments_digest` | One-way identity of canonical ranges, query, scope, and safe flags. |
| `repository_digest` | One-way freshness boundary for HEAD and working state, or a caller-owned equivalent boundary. |
| `expected_effect_digest` | One-way identity of the expected information or effect. |
| `side_effect_class` | Read-only, verification, provider call, local mutation, external effect, or unknown. |
| `failure_class_digest` | Case-normalized failure/result class, never raw error text. |
| `plan_digest` | One-way plan or hypothesis identity. |
| `trajectory_digest` | Semantic identity without repository freshness, used only to detect stale prior evidence. |
| `semantic_digest` | Complete semantic identity including freshness. |
| `exact_digest` | Tool and canonical raw-argument identity used for exact comparison. |
| `scope_start`, `scope_end` | Numeric file range, when applicable. |
| `freshness_known` | Whether the freshness boundary is safe to reuse. |
| `suppressible` | Whether this known, non-mutating action may be semantically suppressed. |

The digest framing includes the version, field label, canonical JSON length
boundaries, sorted keys, and UTF-8 replacement behavior. The same input under
the same version therefore produces the same output.

## Equivalence levels

Comparisons return exactly one of:

1. `exact_duplicate`
2. `semantically_equivalent`
3. `overlapping_or_subsuming`
4. `related_but_materially_different`
5. `unknown`

Repository freshness is checked before exact syntax. An identical command after
a relevant repository change is therefore materially different, not a reusable
exact duplicate. Different versions, unknown operations, and unrelated families
return `unknown` and do not create semantic suppression authority.

File reads with the same target and overlapping line ranges are classified as
overlapping or subsuming. Disjoint ranges remain related but materially
different, so the controller can retain both pieces of evidence.

## Canonicalization corpus

The checked-in corpus in `tests/test_action_fingerprint.py` contains 28 named
derivation scenarios plus five controller scenarios. It covers:

- `read_file` with `cat`, `Get-Content`, and Python `open()`;
- `search_code` with `rg`, `grep`, and `Select-String`;
- native Git status with safe Git CLI aliases and presentation flags;
- GitHub issue reads through the native tool and `gh issue view`;
- unchanged and stale test/lint verification;
- absolute, relative, dotted, slash-normalized, and argument-order variants;
- repeated provider prompts and failed hypothesis wording;
- overlapping and disjoint file ranges;
- mutation and unknown-operation fail-closed behavior;
- adversarial private values and distinct-query collision checks; and
- deterministic mapping-order and random-private-value properties.

The corpus is a compatibility boundary for version 1. New aliases may be added
without changing existing identities. A semantic change to existing derivation
requires a new version and migration notes; versions never compare as equivalent.

## Freshness

`RepositoryToolExecutor` fingerprints use the existing evidence-cache repository
assessment: Git HEAD plus safely bounded dirty and untracked content. If that
assessment cannot prove a complete state, its bypass digest changes on every
assessment and `freshness_known` is false. Uncertain state therefore causes a
fresh execution rather than stale suppression.

The controller may also consume an executor-provided
`action_freshness_boundary()` for deterministic adapters and tests. A changed
boundary produces `required_fresh` in the trace and starts a new success count.

Remote reads have no invented freshness guarantee. Unless their executor
supplies one, they retain a canonical family and target for diagnostics but do
not gain semantic suppression authority.

## Side effects and exact identity

Semantic similarity never merges local mutations or external effects. Those
actions retain an exact tool-and-arguments digest and continue through the
idempotency, reconciliation, dispatch-proof, consent, and repository-safety
controls introduced by #616 and related work.

The important asymmetry is deliberate:

- a semantic fingerprint may stop a known low-value read or verification;
- it may not authorize, retry, reconcile, or approve any operation; and
- an unknown classification never becomes permission or a semantic block.

`run_command` is classified by its inner command. Recognized reads and
verification commands may use semantic comparison. Unknown or effectful command
forms remain on the existing exact-only path.

## Privacy review

Raw repository paths, source, search expressions, prompts, plan text, command
text, provider output, error text, and credentials are not fields on
`ActionFingerprint`. They exist only as temporary derivation inputs and are
replaced by domain-separated SHA-256 digests before the object is constructed.

`ActionFingerprint.to_dict()` is an explicit allowlist rather than a generic
dataclass serializer. The test corpus serializes fingerprints containing paths,
prompts, and generated secret-like values and verifies that none appear.

Fingerprints are correlation identifiers, not secrets or access tokens. They
must not be used to retrieve raw values, approve an action, or expand a tool's
scope.

## Collision posture

Public digests retain 128 bits of SHA-256 output. A collision cannot authorize
an operation because fingerprints only suppress known non-mutating actions.
Mutation identity never uses semantic suppression, and unknown classifications
fail open for evidence collection.

The corpus verifies distinct search queries and randomized canonical mappings.
If a future collision is observed, the safe response is to increment the
derivation version and disable reuse across versions, not to store raw inputs.

## Controller evidence and cost accounting

Each tool-trace row records:

- fingerprint and derivation version;
- operation family and side-effect class;
- equivalence level;
- `repeat_decision` and a fixed, privacy-safe reason; and
- freshness boundary.

The result-level repetition summary records warning, block, override, and failed
repeat counts, measured latency spent on repeats, and estimated latency avoided
by a block. Estimates use only prior measured durations for that fingerprint.

These values are operational telemetry aligned with #565. They are deliberately
separate from provider tokens, provider spend, and monetary savings. A blocked
call does not claim unknown tokens or money were saved.

Before #649, a three-step `read_file` / `cat` / `read_file` trajectory executed
all three actions because their raw signatures differed. After #649, the second
execution carries a warning and the third is suppressed. In the deterministic
controller fixture, that changes executions from three to two and records the
prior measured 7 ms as estimated avoided latency. An explicit override executes
the third action and records 14 ms of measured repeated latency across the two
repeat executions.
