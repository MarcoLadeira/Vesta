# Error, Empty, and Loading State Framework Design

## Goal

Make every core OPai surface explain its real state, why it occurred, and the
safest next action without inventing new backend error taxonomies or fake
progress.

## Chosen approach

Introduce a browser-side `stateCardHtml` / `renderViewState` helper in
`opai/assets/web/app.js`. It accepts one small canonical state object:
`kind`, `title`, `reason`, `action`, `actionLabel`, and optional technical
detail. It produces accessible status, error, and empty cards with an action
that is disabled or absent when it would cause spend. The existing in-chat
`renderErrorCard` remains the rich streaming variant, but receives the same
role, reason/action anatomy, and expandable-detail safety rules.

The dashboard and settings data loaders will render queued/loading cards while
the bridge is pending; error cards when parsing or bridge data fails; and
empty cards when a valid payload contains no KPIs, cards, actions, or useful
subtitle. The recents sidebar gets an actionable empty card that starts a new
chat. Toasts become polite live announcements, while errors use `role=alert`
only at the point a terminal failure is surfaced.

## State map

| Surface | Loading | Empty | Error/offline | Safe action |
| --- | --- | --- | --- | --- |
| Chat workspace | canonical prepare/connect/stream/verify phase | existing starter prompts | in-stream typed error card | recovery action or switch model |
| Dashboard | waiting for dashboard bridge result | no insight data yet | dashboard bridge/data failure | retry dashboard request |
| Settings | waiting for settings bridge result | sparse but valid settings data | settings bridge/data failure | retry settings request |
| Sidebar recents | n/a (boot data is already local) | no saved chats | clear-history failure remains in chat | start a new chat |
| Global toast | n/a | n/a | informational confirmation only | none; polite announcement |

## Accessibility and privacy

- Error cards use `role=alert`; loading, empty, and toast announcements use
  `role=status` with `aria-live=polite`.
- Loading copy names an observed operation and makes no progress claim.
- Only already-redacted technical diagnostics are expandable; raw prompts,
  credentials, and stack traces never appear by default.
- Retry only reissues read-only dashboard/settings fetches. It never retries a
  model call or any spend-triggering operation automatically.

## Verification

Playwright mock-bridge scenarios cover provider failure, loading, empty
dashboard/settings payloads, bridge failures, empty recents, and live-region
semantics. Existing error-recovery, loading-state, and accessibility coverage
remain green.
