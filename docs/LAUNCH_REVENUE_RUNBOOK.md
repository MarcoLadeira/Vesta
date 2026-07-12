# Archived Pre-Free-Launch Revenue Runbook

> **Archived on 2026-07-12.** OPai's alpha launches fully free. This document
> records an earlier pricing experiment only; it must not be used to create
> checkout links, access gates, licenses, or paid launch copy. Future pricing
> is evidence-led post-launch discovery.

This runbook keeps the first revenue loop simple: use no-code payments, route
buyers into human onboarding, and do not build licensing before launch.

## Payment Setup

Preferred provider: Lemon Squeezy.

Fallback provider: Gumroad.

Create:

- Founding Pro monthly: `$12/month`.
- Founding Pro yearly: `$99/year`.
- Team Pilot: `$199/month/team` for 3 months.

After creating checkout links:

1. Replace `PRIVATE_FOUNDING_PRO_CHECKOUT_URL` in `site/index.html`.
2. Replace `PRIVATE_TEAM_PILOT_APPLY_URL` with a private Tally/Typeform/HubSpot
   form or direct booking link.
3. Replace `PRIVATE_BENCHMARK_PROOF_URL` with a private proof-submission form.
4. Add the Team Pilot payment link to the Team Pilot follow-up message after
   qualifying the team.

Do not use public GitHub issues for paid access, pilot applications, customer
proof, or private benchmark output.

## Founding Pro Fulfillment

Deliver manually at first:

- Priority install help.
- One savings report review.
- One benchmark proof review.
- Private roadmap access through a private channel.
- Early Pro export/template feedback.

## Team Pilot Fulfillment

For each pilot:

```sh
opai doctor
opai benchmark run --suite max --mode both
opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks
opai savings --markdown
opai policy check
opai team report
```

Sell the pilot around governance and cost control:

```text
Let every developer keep their favorite AI coding agent, but put all of them
under one local policy, benchmark, savings, and audit layer.
```

## Do Not Build Yet

- Custom auth.
- License servers.
- Hosted dashboards.
- Token resale.
- Silent telemetry.
- Enterprise claims that the shipped alpha cannot enforce.
- Public source distribution unless OPai is intentionally open source.
