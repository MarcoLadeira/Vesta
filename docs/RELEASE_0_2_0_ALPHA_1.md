# OPai v0.2.0-alpha.1 Release Notes

OPai v0.2.0-alpha.1 is the public alpha launch candidate for the AI coding cost
firewall.

## What Shipped

- Public launch site under `site/`.
- Cloudflare Pages deployment instructions.
- Founding Pro, Team Pilot, and benchmark proof GitHub intake forms.
- Local max benchmark proof loop:

```sh
opai benchmark run --suite max --mode both
opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks
```

- Team governance, policy, audit, signed evidence, and benchmark docs from the
  merged governance and benchmarking work.
- Package metadata updated to `0.2.0a1`.

## Install

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.ps1 | iex
```

macOS/Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/MarcoLadeira/OPai/main/install.sh | sh
```

Then run:

```sh
opai quickstart
opai doctor
opai benchmark run --suite max --mode both
opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks
opai savings --markdown
```

## Caveats

- OPai is alpha software.
- Checkout links are external no-code links, not an in-repo license system.
- Cloud/provider-backed benchmarks are opt-in only.
- CLI telemetry is off by default.
- Do not claim official external benchmark leaderboard placement from local
  benchmark results.

## Release Checklist

- [ ] `python -m unittest discover -s tests`
- [ ] `python -m ruff check .`
- [ ] `python -m ruff format --check .`
- [ ] `python -m opaihub validate`
- [ ] `python -m opai doctor`
- [ ] `python -m opai benchmark run --suite max --mode both`
- [ ] `python -m opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks`
- [ ] Replace Cloudflare Web Analytics token.
- [ ] Replace Founding Pro checkout URL.
- [ ] Deploy `site/` to Cloudflare Pages.
- [ ] Publish GitHub Release `v0.2.0-alpha.1`.
