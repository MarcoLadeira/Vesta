# Guarded Workflows — Vesta's Trust Moat

Competitors race toward more autonomous agents. Vesta's edge is making autonomy
**safer, bounded, auditable, and human-controlled** (issue #39). Every guarded
workflow follows one shared contract and risky actions **fail closed**.

## The contract

Defined in [`hub/workflows/guarded-contract.yaml`](../workflows/guarded-contract.yaml).
Each guarded workflow must declare:

| Field | Meaning |
| --- | --- |
| `permission_boundaries` | Read/write scope. No push/deploy/submit without approval. |
| `path_locks` | Allowed write paths; `.git`, `.env`, secrets, and keystores are forbidden by default. |
| `evidence_artifacts` | The consistent, hashable artifacts every run emits. |
| `stop_conditions` | Explicit conditions that halt the workflow instead of escalating. |
| `fail_closed` | Risky actions denied unless a human explicitly confirms. |

Validate that every template satisfies the contract:

```sh
opai guard check        # exits non-zero if any template is missing a field
opai guard list         # list templates + the reference implementation
```

## Fail-closed gate

Risky actions (`git push`, `deploy`, `app store submit`, `npm publish`, …) are
**denied by default**. They require recorded human confirmation, and the sandbox
command policy is consulted so denied commands stay denied:

```sh
opai guard action "git push origin main"            # -> deny (fail closed)
opai guard action "git push origin main" --confirm  # -> confirm (human approved)
```

Path locks are enforced the same way — writing to `.git` or `.env` is refused.

## Evidence packets

Every guarded run emits a consistent, hashable evidence packet under
`.opaihub/evidence/<workflow>.json` (sha256 over the body), so runs are
auditable and comparable:

```sh
opai guard evidence release_preflight
```

## Reference implementation: Mobile Readiness

`mobile_readiness` is the canonical guarded workflow. It demonstrates the full
contract: bounded write paths (`android`, `ios`, `lib`, `test`, evidence only),
forbidden secrets/keystores, signed evidence packets, explicit stop conditions
(unsigned/expired evidence, failing compliance gate, missing privacy
declarations), and fail-closed submission (`app store submit` never runs without
approval). New guarded workflows should mirror its shape.

## Reusable templates

Defined in [`hub/workflows/templates.yaml`](../workflows/templates.yaml):

- `mobile_readiness` (reference)
- `release_preflight`
- `ci_fixer`
- `pr_review`
- `security_audit`
- `dependency_update`

Each is contract-valid and ready to extend per project.
