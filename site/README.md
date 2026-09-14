# Vesta Landing Page

Static single-file site for Vesta — the AI coding cost firewall — built around the
**free public alpha** narrative. Vesta is free. Install it in one command.

## The Funnel

```text
visit -> pip install opai -> opai doctor -> opai savings -> share
```

The page has exactly one goal: **install**. The single CTA is the install
command itself (`pip install opai`, copy-to-clipboard). Secondary links go to
GitHub and Discussions. After install, `opai doctor` verifies the setup and
`opai savings` proves the value in numbers.

Hard rule: **no paid-tier or gated-access CTAs on this page.** No pricing
sections, no checkout links, no invite walls, no "apply" forms. Vesta is free
during alpha — no credit card, no invite.

## Files

- `index.html` — the whole site (inline CSS + JS, no frameworks, no external
  dependencies except the mascot image)
- `assets/opai-mascot.png` — mascot (also used as favicon and og:image)
- `_headers` — Cloudflare Pages security headers (already present)
- `serve.py` — local preview helper

## Preview Locally

```sh
cd site
python serve.py            # serves on http://localhost:7100 for 10 minutes
# or
python -m http.server 7100 # standard library, any port you like
```

## Deploy

Fully static — no build step. On Cloudflare Pages: connect the repo and set

```text
Build command: none
Build output directory: site
```

or direct-upload with Wrangler:

```sh
npx wrangler pages deploy site --project-name opai --branch main
```

`_headers` ships with the site and is applied automatically by Cloudflare Pages.

## Analytics

None. The site is privacy-first, like the tool: no trackers, no beacons, no
third-party requests of any kind.
