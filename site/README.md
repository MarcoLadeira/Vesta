# OPai Launch Site

This folder is the static public install funnel for OPai.

## Primary Funnel

```text
page visit -> install OPai -> opai doctor -> opai benchmark max -> opai savings -> issue/payment/pilot signal
```

The page has one primary CTA:

- Install OPai.

It has two revenue CTAs:

- Buy Founding Pro.
- Apply for Team Pilot.

The revenue buttons currently fall back to GitHub intake issue forms until the
real Lemon Squeezy or Gumroad checkout URLs exist. Replace both
`FOUNDING_PRO_CHECKOUT_URL` hooks in `index.html` after checkout creation.

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
