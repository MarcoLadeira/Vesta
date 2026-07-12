# OPai Team & Enterprise Governance

Per the [business strategy](../../docs/BUSINESS_STRATEGY.md), the highest-value
revenue is **team and enterprise governance**, not solo subscriptions. This is
the working control layer that makes that real: shared policy, a tamper-evident
audit trail, signed evidence, approved MCP allowlists, and a fail-closed CI gate.

Everything here is **local and honest**: no telemetry, no fake SSO/billing.
Signing is HMAC integrity (a shared secret), not public-key identity.

## Committed team policy

A team policy is one file committed to the repo so everyone routes under the
same rules (unlike per-project state under the gitignored `.opaihub/`):

```sh
opai team init --team acme --profile team-safe   # writes opai-team-policy.yaml
opai team apply                                  # set local policy from it
```

`opai-team-policy.yaml` pins the profile, budgets, paid posture, and an optional
approved-MCP allowlist:

```yaml
schema_version: 1
team: acme
profile: team-safe
allow_paid: true
# approved_mcp_servers: [filesystem, git]   # uncomment to enforce an allowlist
budgets:
  monthly_usd_limit: 50.0
  per_task_hard_limit_usd: 3.0
```

## Fail-closed CI gate

Drop one command into CI. It exits non-zero on any governance regression, so a
PR cannot merge with a policy drift, an unapproved MCP server, an ungated cloud
model, or a broken guarded-workflow contract:

```sh
opai policy check          # exits 1 on violation
opai policy check --audit  # also record the result to the audit trail
opai policy check --require-team-policy  # strict CI: missing team policy fails
```

Example GitHub Actions step:

```yaml
- name: OPai governance gate
  run: python -m opai policy check
```

## Tamper-evident audit trail

Every governance action (denied risky command, applied policy, evidence packet,
edition change, CI check) appends to a **hash-chained** log under
`.opaihub/audit/`. Editing or deleting a middle entry breaks the hash chain.
Tail truncation is detected by the local checkpoint head file written beside the
log.

```sh
opai audit log              # recent events
opai audit status           # counts + chain validity
opai audit verify           # verify the hash chain (exit 1 if broken)
opai audit export --out audit.json   # signed bundle for review/compliance
```

The log redacts secrets and stores no raw prompts.

## Signed evidence

Guarded-workflow evidence packets can be HMAC-signed and verified, so a reviewer
knows a packet was not altered after it was produced:

```sh
opai guard evidence release_preflight --sign
opai guard verify .opaihub/evidence/release_preflight.json
```

Teams share one key out-of-band via `OPAI_SIGNING_KEY` (e.g. a CI secret); solo
users get an auto-generated local key under `.opaihub/keys/` (gitignored).

## Team report

A team lead can answer "who routed what, did it follow policy, what spend was
avoided, is the audit intact?":

```sh
opai team report
```

## Free Public Alpha coverage

The governance controls implemented in OPai alpha are free: shared local team
policy, team reports, audit logs, approved MCP checks, CI policy gates, and
evidence exports. They have no checkout, licence, or tier requirement.

Hosted identity, supported private deployment packaging, and private registry
distribution remain planned implementation work. They are not paid alpha
tiers.

See [PRICING_AND_EDITIONS.md](PRICING_AND_EDITIONS.md) for the current free
launch boundary.
