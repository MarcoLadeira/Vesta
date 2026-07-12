# OPai Launch Site

This folder is the static free-public-alpha funnel for OPai.

## Primary Funnel

```text
page visit -> verified release availability -> free install when published -> opai doctor -> local proof loop -> privacy-safe feedback -> evidence-led product learning
```

The page has one primary CTA:

- Get OPai Free.

The supporting CTAs are **Run the proof loop** and **See alpha readiness**.
The public offer language is **Free public alpha**: no checkout, license,
invitation, or private-link gate. The page intentionally avoids raw GitHub
installation URLs; free access does not require the project to choose an open-
source distribution posture. Until a verified artifact exists, it links only to
the GitHub Releases page and says that there is no public package installation
command yet.

Use a privacy-safe support channel for optional feedback and benchmark evidence.
Do not add payment buttons, private-access placeholders, or public issue forms
as a substitute for a support workflow.

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
