# Issue #383 Composer Design

## Decision

Use a lightweight frontend task-setup layer around the existing composer. It
reads the current mode, model, autonomy snapshot, accounts, and build state;
it does not introduce routing or spend calculations in the browser.

## Interaction

- A keyboard-reachable send-context row shows Mode, autonomy consequence,
  model, and cost posture before every send. Mode and model chips focus their
  existing selects; the cost chip opens Cost Firewall settings.
- Cost posture is deliberately conservative: local and free models are "No
  provider spend"; Auto is "Routes local first"; account models are "May spend
  within your limits". The latter opens Settings rather than claiming a price.
- The composer describes Enter to send and Shift+Enter for a newline. Drafts
  remain in the textarea through view switches; only a user send clears them.
- "Add file context" creates removable `@relative/path` references. Dropping
  files adds their names only, never file contents. The bridge receives these
  as `contextHints` so existing backend context selection remains authoritative.
- Send disables only for a known unconfigured selected account or an active
  resume choice; each reason names its user-initiated remedy. Busy state keeps
  the existing canonical Send/Stop path.

## Safety and accessibility

All summary controls are buttons with labels, focus the existing controls or
open Settings, and do not trigger a request. Context references are text hints,
not uploads. No action automatically reconnects a provider, retries, or spends.

## Verification

Playwright covers every mode and local/free/account/Auto posture, disabled
states and Settings link, manual context references/drop, Enter/Shift+Enter,
draft persistence, and the existing Send/Stop lifecycle.
