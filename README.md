# PR #817 review evidence — images only

This branch exists **only to host screenshots** referenced from the review comment on
[PR #817](https://github.com/MarcoLadeira/OPai/pull/817). It contains no code and is not
intended to be merged into anything.

Every image under `pr-817/` was captured live on 2026-09-10 by booting the real web UI
(`opai/assets/web/index.html`) through the repository's own e2e mock bridge and
deterministic fixtures — the same harness the committed Playwright gallery uses — at:

- desktop 1440x900
- tablet 768x1024
- phone 390x844

"BEFORE" panes are rendered from `ab4e26e`, the merge base the PR targets.
"AFTER" panes are rendered from `807a067`, the PR head.

`12-design-tokens-stale-baseline.png` is the baseline / actual / diff triptych produced by
`npx playwright test design-tokens` on the PR head.
