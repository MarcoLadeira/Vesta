# OPai Launch Site

This folder is the static public install funnel for OPai.

## Primary Funnel

```text
page visit -> paid/private access -> install OPai -> opai doctor -> opai benchmark max -> opai savings -> private proof/payment/pilot signal
```

The page has one primary CTA:

- Get OPai Access.

It has two revenue CTAs:

- Buy Founding Pro.
- Apply for Team Pilot.

The public offer language is **Controlled Alpha**, not free public source
distribution. Do not publish raw GitHub install URLs on this page unless OPai is
intentionally open source.

The revenue buttons must not fall back to GitHub issue forms. Replace these
private-link placeholders in `index.html` before public hosting:

- `PRIVATE_FOUNDING_PRO_CHECKOUT_URL`
- `PRIVATE_TEAM_PILOT_APPLY_URL`
- `PRIVATE_BENCHMARK_PROOF_URL`

Use Lemon Squeezy or Gumroad for checkout, and a private form or private support
channel for Team Pilot and benchmark proof submissions.

## Cloudflare Pages

Current Cloudflare Pages direct-upload commands, from the official Wrangler
Pages docs:

```sh
npx wrangler pages project create opai --production-branch main
npx wrangler pages deploy site --project-name opai --branch main
```

If the project is connected to Git instead, set:

```text
Build command: none
Build output directory: site
```

## Analytics

The site includes Cloudflare Web Analytics only:

```html
const token = "REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN";
// When the placeholder is replaced, index.html injects:
// https://static.cloudflareinsights.com/beacon.min.js
```

Replace the placeholder token in Cloudflare before public launch. Do not add
Google Analytics, Mixpanel, or silent CLI telemetry.
