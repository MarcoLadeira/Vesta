# One runtime truth (issue #818)

> One request → one task identity → one ordered event history → one
> authoritative state → one evidence-backed terminal result.

`#613` built the kernel: nine `opaihub/journal_*.py` modules, a SQLite store,
lifecycle adapter, comparator, canonical reader, operation idempotency, a
retirement gate and a CLI. What it did **not** do is make anything depend on
it. The journal is a faithful mirror running beside the authorities it was
meant to replace, which means every fact OPai reports still has at least two
sources that can disagree.

This document is the working record for closing that gap. It is written as
work lands, not in advance: each section states what was measured, not what
is intended.

## Method

Every claim here is either a file/line reference or a reproduction. A section
with neither is a plan, and is labelled as one.

## Status

| Migration step (per #818) | State |
| --- | --- |
| 1. Inventory every authoritative writer/reader | in progress |
| 2. Parity assertions, legacy vs canonical | not started |
| 3. Cut over one local-provider path | not started |
| 4. Cut over one account-provider path | not started |
| 5. Cut over cancellation, verification, cost, delivery | not started |
| 6. Switch GUI/CLI/background readers to canonical projections | not started |
| 7. Migrate persisted state | not started |
| 8. Delete legacy authoritative writes | not started (user's call) |
