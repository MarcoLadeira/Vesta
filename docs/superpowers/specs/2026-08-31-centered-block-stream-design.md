# Centered block stream

## Goal

Replace the left-anchored, concatenated live response with a centered conversation lane. Provider updates appear as separate short blocks, like ChatGPT, while OPai keeps its dark surfaces, green status accents, structured evidence, and low-overhead rendering.

## Interaction

- Center ordinary assistant prose, live status, and the composer on one readable lane.
- Keep user prompts right-aligned inside that same lane.
- Keep wide artifacts such as diffs, code, and tables able to use the wider response canvas.
- Treat every complete provider agent message as a new progress block instead of joining it directly to the preceding text.
- Keep the latest three progress blocks visible. Fold older blocks into one “Earlier progress” disclosure.
- Keep the final answer fully visible when the run finishes; activity remains available through the existing disclosure.
- Raw token streams stay within the current block and continue to render progressively.

## Data flow

The account-provider parser already knows when a completed Claude or Codex agent message arrives. It will mark that callback as a block boundary. The Qt bridge will carry the optional boundary in the existing token payload. The browser will maintain a bounded block model for the active response instead of one ever-growing text string.

Providers that only expose token deltas will omit the boundary and continue using one active block. Blocking fallbacks will create one block. The stored and terminal answer will preserve block separation with Markdown paragraph breaks.

## Rendering and performance

The live renderer will create a stable DOM node per message block and update only the active node on the existing animation-frame cadence. It will not re-render the full accumulated response for each token. Only three recent blocks are expanded; earlier blocks move behind a native disclosure without losing text or copy access.

The final response renderer remains the source of truth for structured evidence, changes, warnings, and completion state. Wide artifacts keep their existing width while prose receives automatic inline margins.

## Safety and recovery

Malformed or missing boundary metadata degrades to the current single streaming block. Stale request guards remain unchanged. Cancellation preserves the blocks already received. Copying the answer returns the complete Markdown source in chronological order.

## Verification

- Parser tests prove completed provider messages receive boundaries and final text contains paragraph separators.
- Browser tests prove blocks do not concatenate, only the latest three are initially visible, older blocks expand on demand, token deltas stay in one block, and cancellation preserves text.
- Responsive tests prove the centered lane at desktop, tablet, and phone widths while wide artifacts remain usable.
- A focused render-count test proves additional blocks do not restore full-response re-rendering.
