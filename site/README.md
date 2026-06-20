# OPai install page (`site/`)

`site/index.html` is the first public funnel described in
[`docs/BUSINESS_STRATEGY.md`](../docs/BUSINESS_STRATEGY.md): a single,
self-contained, dependency-free, **telemetry-free** page whose only job is to
turn interest into a successful install.

## What it contains

- Primary message: *OPai is the AI coding cost firewall.*
- One-command install for Windows and macOS/Linux.
- The 60-second proof commands (`opai quickstart`, `opai doctor`, `opai savings`).
- Before/after savings proof (matches [`docs/PROOF.md`](../docs/PROOF.md)).
- Honest pricing (matches [`hub/editions.yaml`](../hub/editions.yaml)).

## Deploy (GitHub Pages)

It is plain static HTML with inline CSS - host it anywhere. For GitHub Pages:

1. Settings -> Pages -> Source: deploy from a branch.
2. Choose the branch and the `/site` folder (or copy `index.html` to `/docs`).

No build step, no external scripts, no analytics. Keep it that way - the privacy
stance is part of the pitch.

## Keep claims grounded

Every number and price on the page must match shipped behavior:
install commands = `README.md`, savings = `docs/PROOF.md`, pricing =
`hub/editions.yaml`. A CI test (`tests/test_site_funnel.py`) checks the
positioning and that the page stays script-free.
