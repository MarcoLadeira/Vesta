# Vesta Effectiveness, Security, And Hosting Audit

Date: 2026-06-21

Branch: `codex/private-launch-access`

> **Historical record — superseded for launch planning.** This audit describes
> a pre-free-launch private/paid distribution proposal. Vesta's public alpha
> launches fully free; do not use its checkout, private-access, or source-
> restriction recommendations as current release instructions.

## Executive Result

Vesta is effective on its local max benchmark, but it is **not ready to host as a
paid/private product** until access links, Cloudflare auth, and source
distribution are finalized.

The most important product/security correction from this audit:

```text
Do not route paid users, team pilots, or benchmark proof reports through public
GitHub issue forms.
```

This branch removes those public intake forms and changes the launch page to a
controlled-alpha model with private checkout/application/proof placeholders.

## Effectiveness

Command:

```sh
python -m vesta benchmark run --suite max --mode both
python -m vesta benchmark gate --min-effectiveness-index 95 --require-risk-blocks
python -m vesta benchmark report --format markdown
```

Result:

| Metric | Result |
| --- | --- |
| Run id | `bench-127fe09dadfe` |
| Suite | `max` |
| Tasks | `16` |
| Vesta effectiveness index | `100.0` |
| Leaderboard grade | `A+` |
| Context reduction | `50.0x` capped |
| Paid-call avoidance | `50.0x` capped |
| Estimated cost reduction | `50.0x` capped |
| Estimated cost saved | `$3.612` |
| Paid calls avoided | `16` |
| Risk events blocked | `6` |
| Success rate | `1.0` |
| Time to evidence | `11.833s` |

Allowed public claim:

```text
Vesta reduced context by 50x and avoided 16 paid calls on the 16-task local
benchmark suite.
```

Caveat: this is a local fixture-backed result, not a SWE-bench, Terminal-Bench,
or Aider leaderboard result.

## Release Checks

| Check | Result |
| --- | --- |
| `python -m unittest tests.test_site_funnel tests.test_vesta_finish tests.test_positioning_and_cli` | Pass, 21 tests |
| `python -m unittest discover -s tests` | Pass, 217 tests |
| `python -m ruff check .` | Pass |
| `python -m ruff format --check .` | Pass |
| `python -m vestahub validate` | Pass |
| `python -m vesta publish status` | Ready |
| `python -m vesta doctor` | Executes; Claude/Codex/Copilot active, Cursor/Cline missing in this worktree until `vesta activate --repair` |

## Security Audit

### Public Access Leakage

Public launch page:

- No `issues/new` links.
- No `founding-pro-interest.yml`, `team-pilot.yml`, or `benchmark-proof.yml`
  issue templates.
- No raw `raw.githubusercontent.com/MarcoLadeira/Vesta/main/install...` source
  install links.
- Private placeholders remain intentionally:
  - `PRIVATE_FOUNDING_PRO_CHECKOUT_URL`
  - `PRIVATE_TEAM_PILOT_APPLY_URL`
  - `PRIVATE_BENCHMARK_PROOF_URL`

Docs now warn that Vesta cannot be protected by payments alone if the whole repo
stays public.

### Secret Scan

Command:

```sh
rg -n --hidden --glob '!.git' --glob '!.vestahub' --glob '!tests/**' --glob '!**/__pycache__/**' --glob '!site/assets/**' --glob '!*.png' "(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|-----BEGIN (RSA|OPENSSH|DSA|EC|PRIVATE) KEY-----|CLOUDFLARE_API_TOKEN=|LEMON_SQUEEZY_API_KEY=|GUMROAD_ACCESS_TOKEN=)" .
```

Result: pass outside tests. Test fixtures intentionally contain fake keys to
verify redaction behavior.

### SAST

Command:

```sh
python -m bandit -r vesta vestahub opcoding -q
```

Result: pass. Bandit reports only existing `nosec` warnings for the local-model
bind constant in `vestahub/local_models.py`.

### Dependency Audit

Command:

```sh
python -m pip_audit
```

Result: fail on the current Python environment, with 43 vulnerabilities across
15 installed packages.

Important nuance: `pyproject.toml` declares no Vesta runtime dependencies, so this
is an environment/tooling risk rather than a declared Vesta dependency risk. Do
not ship from this environment without either using a clean release environment
or upgrading the vulnerable tooling packages.

## Hosting Readiness

Cloudflare status:

```sh
npx --yes wrangler whoami
```

Result: not authenticated.

```text
You are not authenticated. Please run `wrangler login`.
```

The site itself is static and Cloudflare Pages-ready, but hosting is blocked by:

- Cloudflare login or `CLOUDFLARE_API_TOKEN`.
- Cloudflare Web Analytics token replacement.
- Private Founding Pro checkout URL.
- Private Team Pilot application URL.
- Private benchmark proof submission URL.
- Source distribution decision: make the product repo/private package private,
  or intentionally accept public-source copying risk.
- Optional custom domain and DNS.

## Browser Check

Local static preview:

```sh
python -m http.server 8766 --directory site
```

Playwright results:

- Page title: `Vesta - AI Coding Cost Firewall`.
- Console errors: `0`.
- Mobile viewport width: `390`.
- Mobile document scroll width: `390`.
- `Controlled Alpha` text present.
- `issues/new` absent.

## CEO Recommendation

Do not host publicly until the payment/access links are real and the source
distribution decision is made. The safest next sequence is:

1. Make the repo/package distribution private if Vesta should not be copied.
2. Create Lemon Squeezy or Gumroad Founding Pro checkout.
3. Create private Team Pilot application form.
4. Create private benchmark proof form.
5. Replace the three private placeholders in `site/index.html`.
6. Authenticate Cloudflare and deploy `site/`.
7. Run the max benchmark and publish the local benchmark claim with caveats.
