# Archived Pre-Free-Launch 30-Day Go-To-Market Plan

Last updated: 2026-06-20

> **Archived on 2026-07-12.** This plan assumes a controlled paid/private
> launch and is no longer current. Vesta's alpha is fully free; use the current
> launch checklist and product identity instead. Retain this file only as a
> record of hypotheses to revisit after real free-alpha usage.

Goal: get Vesta online, get real users, start revenue conversations, and learn
whether the cost-firewall positioning converts.

## Public Offer

| Offer | Price | Conversion path |
| --- | --- | --- |
| Controlled Alpha | Invite or paid access | Private install access only; no public source URL on the site. |
| Founding Pro | $12/month or $99/year | Lemon Squeezy or Gumroad checkout before public push. |
| Team Pilot | $199/month/team for 3 months | Private application form, direct invoice, or payment link after qualification. |

Future Team pricing target: $19/user/month. Future Team Governance target:
$29-39/user/month.

## Launch Promise

Use the same proof loop everywhere:

```sh
vesta doctor
vesta benchmark run --suite max --mode both
vesta benchmark gate --min-effectiveness-index 95 --require-risk-blocks
vesta savings --markdown
```

Public wording:

```text
Vesta is the AI coding cost firewall. It proves local max benchmark results in
your repo without silent CLI telemetry.
```

Do not claim official SWE-bench, Terminal-Bench, or Aider leaderboard results
until Vesta has actually submitted there.

## Days 1-3: Launch Readiness

- Verify `site/` locally and replace the Cloudflare Web Analytics placeholder.
- Decide whether Vesta source remains public. If IP protection matters, make the
  product repo/private package distribution private before public launch.
- Create Lemon Squeezy checkout links for Founding Pro and Team Pilot, or use
  Gumroad if account setup is faster.
- Replace `PRIVATE_FOUNDING_PRO_CHECKOUT_URL`,
  `PRIVATE_TEAM_PILOT_APPLY_URL`, and `PRIVATE_BENCHMARK_PROOF_URL` hooks in
  `site/index.html`.
- Run release checks:

```sh
python -m unittest discover -s tests
python -m ruff check .
python -m ruff format --check .
python -m vestahub validate
python -m vesta doctor
python -m vesta benchmark run --suite max --mode both
python -m vesta benchmark gate --min-effectiveness-index 95 --require-risk-blocks
```

- Tag release candidate as `v0.2.0-alpha.1` only after checks pass.

## Days 4-7: Get Online

- Deploy `site/` to Cloudflare Pages.
- Publish GitHub Release `v0.2.0-alpha.1`.
- Use a private form/support channel for benchmark proof reports. Do not collect
  paid customer proof through public GitHub issues.
- Update repo description:

```text
Vesta is the AI coding cost firewall: local-first routing, benchmarks, savings,
and governance for Claude, Codex, Copilot, Gemini, Cursor, and Cline.
```

## Days 8-14: First Users

- Post GitHub Discussion launch thread.
- Post Hacker News `Show HN`.
- Submit Product Hunt upcoming page.
- Post in relevant Reddit communities with proof commands, not hype.
- Direct message or email 50 AI-heavy developers or founders.
- Book 10 calls and capture exact install failures, confusing copy, benchmark
  skepticism, and willingness to pay.

## Days 15-30: Revenue And Pilots

- Convert interested users to Founding Pro.
- Recruit 3 Team Pilot conversations.
- Close 1 paid Team Pilot.
- For each pilot, run:

```sh
vesta doctor
vesta benchmark run --suite max --mode both
vesta benchmark gate --min-effectiveness-index 95 --require-risk-blocks
vesta savings --markdown
vesta policy check
vesta team report
```

Only ship blockers that affect install, benchmark trust, payment conversion, or
team pilot delivery.

## Metrics

By day 30:

- 100 site visitors.
- 25 installs or install attempts.
- 10 submitted benchmark/proof reports.
- 5 paying Founding Pro users.
- 3 Team Pilot conversations.
- 1 paid Team Pilot.

Failure thresholds:

- Fewer than 10 installs: prioritize install friction and positioning.
- Installs but no payment: prioritize stronger proof and clearer savings
  examples.
- Payments but weak pilot interest: reposition Team around governance, audit,
  and policy rather than solo cost savings.

## Usage Signals

Allowed:

- Cloudflare Web Analytics for the site.
- GitHub traffic, stars, issues, discussions, and releases only if the repo is
  intentionally public.
- Payment-link conversions.
- Email or issue-form signups.
- Opt-in user-submitted benchmark reports through private forms or support.

Not allowed:

- Silent CLI telemetry.
- Prompt upload by default.
- Secret, private log, or private path collection.
