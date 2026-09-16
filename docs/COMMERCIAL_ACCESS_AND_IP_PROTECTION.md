# Archived Pre-Free-Launch Commercial Access And IP Protection

> **Archived on 2026-07-12.** This proposal assumes paid/private distribution.
> Vesta's alpha is fully free, so it must not be used to create checkout links,
> licenses, invitations, private-access requirements, or paid feature gates.
> Retain it only as a post-launch distribution and IP hypothesis, after free-
> alpha evidence establishes whether any of those choices are warranted.
>
> This document must not be used as current launch policy.

Vesta cannot be protected by payments alone if the full source remains publicly
available. A public repository is useful for trust and adoption, but it also
means competitors can inspect and copy the implementation.

## CEO Decision

For the next launch, use **controlled alpha distribution**:

- Public site: positioning, proof commands, pricing, and payment/application
  links.
- Private distribution: install command, release package, or source access only
  after payment, approval, or pilot onboarding.
- Private customer signals: benchmark proof, pilot applications, and paid-user
  support do not go through public GitHub issues.

## Recommended Structure

Use a split model:

| Surface | Visibility | Purpose |
| --- | --- | --- |
| Public site | Public | Explain Vesta, prove value, convert visitors. |
| Public docs excerpt | Public | High-level positioning, screenshots, benchmark claims. |
| Core product repo/package | Private during alpha | Protect implementation while product-market fit is tested. |
| Paid checkout | Public link, private fulfillment | Lemon Squeezy/Gumroad purchase path. |
| Pilot intake | Private form | Team qualification without exposing customer data. |
| Proof reports | Private form/support | User-submitted benchmark evidence without leaking repos. |

## Distribution Options

### Option A: Private Repo Access

Grant paid users access to a private GitHub repo or private release package.
This is the fastest path, but access can still be copied by a user.

### Option B: Private Package Or Signed Release

Publish paid wheels, zip files, or installers through private links after
checkout. This hides casual source access better than a public repo, but Python
packages are still inspectable.

### Option C: Open Core Later

After traction, reopen a smaller free core and keep Pro/Team/Governance packs
private. This preserves adoption while protecting the highest-value logic:
routing policies, governance packs, dashboards, reports, and enterprise tools.

## What Not To Do

- Do not put paid access, team applications, or customer proof in public GitHub
  issue forms.
- Do not publish raw install commands that pull directly from a public source
  repo if Vesta is meant to be paid/closed during alpha.
- Do not promise perfect copy protection for Python code. Use controlled access,
  license terms, signed releases, and commercial speed instead.

## Before Hosting

- Replace `PRIVATE_FOUNDING_PRO_CHECKOUT_URL`.
- Replace `PRIVATE_TEAM_PILOT_APPLY_URL`.
- Replace `PRIVATE_BENCHMARK_PROOF_URL`.
- Replace the Cloudflare Web Analytics token.
- Decide whether `MarcoLadeira/Vesta` should be private before launch.
- If the repo becomes private, replace public install commands with paid/private
  release links.
