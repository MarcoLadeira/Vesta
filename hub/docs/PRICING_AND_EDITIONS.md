# OPai Editions & Pricing

OPai is **open-core**: the local-first trust layer is free forever, and paid
editions add deeper savings proof, governance, and team controls. This document
defines the Free / Pro / Team / Enterprise boundaries (issue #38).

> **Honesty note (alpha).** There is no license server or billing in OPai
> today. The active edition is *self-declared* via `OPAI_EDITION` or
> `opai edition set <name>`. These boundaries are codified in
> [`hub/editions.yaml`](../editions.yaml) so the product surface is real and
> testable, but enforcement and checkout are deliberately future work. **No paid
> feature requires telemetry; nothing leaves your machine by default.**

## Tiers

| Edition | Price | Who it's for |
| --- | --- | --- |
| **Free** | $0 forever | Individual developers who want a local-first AI coding cost firewall. |
| **Pro** | ~$12 / month ($99/yr) | Solo devs who want exportable savings proof and advanced policy. |
| **Team** | ~$19 / user / month | Teams needing shared policy, pooled budgets, team reports, and private registries. |
| **Team Governance** | ~$29 / user / month | Security-aware teams needing audit logs, approved MCP profiles, CI gates, and evidence exports. |
| **Enterprise** | Custom (from ~$24k/yr) | Orgs needing SSO/RBAC, self-host, signed evidence/registries, and security review. |

> The revenue ladder mirrors the [business strategy](../../docs/BUSINESS_STRATEGY.md):
> Free/Pro earn distribution and proof; **Team Governance and Enterprise are the
> real revenue pools** because uncontrolled AI coding creates audit, spend, and
> compliance risk. See [GOVERNANCE.md](GOVERNANCE.md) for the working controls.

## What's included

### Free — meaningful local value, no asterisks
- Local-first cost-aware routing and evidence collection (`opai route`).
- Privacy-safe local **usage ledger** and per-project **savings report**
  (`opai savings`).
- Activation across **Claude, Codex, Copilot, Gemini, Cursor, and Cline**
  (`opai activate`, `opai doctor`).
- `solo-cheap` and `solo-balanced` **policy profiles**.
- Guarded-workflow contract with fail-closed gates.

### Pro (~$12/mo) — prove and share the savings
- Export shareable savings reports: `opai savings --export report.md`.
- `team-safe` policy profile (evidence required on cloud/destructive actions).
- Retained model-eval scorecard history.

### Team (~$19/user/mo) — govern a group
- Shared/team policy distribution and seat management.
- Guarded-workflow **audit and evidence exports**.

### Enterprise (custom) — strict governance
- `enterprise-strict` policy profile (paid models fail closed without override).
- Signed internal workflow packs and enterprise audit exports.

## Why the paid tiers are legitimate

Each paid feature maps to **measurable savings or governance**, never to
artificial throttling of core value:

- Pro is tied to *proving and exporting* the savings the firewall already
  produces.
- Team/Enterprise are tied to *governance* — shared policy, audit trails, and
  fail-closed controls — which only matter at team scale.

## Using editions today

```sh
opai edition show            # current edition, included + locked features
opai edition set pro         # self-declare an edition for this project
OPAI_EDITION=team opai ...    # or declare via environment
```

When a feature is above the active edition, OPai surfaces an upgrade hint rather
than silently failing — see `opai savings --export` for an example.
