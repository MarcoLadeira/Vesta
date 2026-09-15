# Free Public Alpha Runtime Boundary

- **Date:** 2026-07-12
- **Status:** Approved by the product direction: the whole launch is free.
- **Repository:** `MarcoLadeira/OPai`

## Decision

Vesta's alpha ships with no paid edition, entitlement, licence, checkout, or
feature-access gate. A capability may be unavailable because it has not been
implemented safely, but never because a user has not bought or self-declared a
tier.

The current edition mechanism is an executable contradiction: its default
`free` tier blocks `vesta savings --export`. That block and all upgrade language
must be removed before the public alpha can be described as fully free.

## Options considered

1. **Default every user to Enterprise.** This unblocks current gates, but keeps
   paid-tier language and would falsely imply that an Enterprise product is
   being supplied. Rejected.
2. **Delete the edition API immediately.** This removes the contradiction but
   needlessly breaks scripts and diagnostic callers during alpha. Rejected.
3. **Keep a compatibility-facing availability API and make it free-alpha-only.**
   This preserves `vesta edition show` as a clear diagnostic, makes legacy
   `set` calls harmless no-ops, removes real gates, and leaves future pricing
   for a separately approved product decision. Selected.

## Runtime contract

- `vesta savings --export PATH` always performs its local export; it contains
  no edition check and returns no upgrade-required outcome.
- `current_edition()` always returns `free`; environment variables and project
  state cannot turn alpha features on or off.
- `feature_available()` means implementation availability only. It is never a
  commercial boundary. A known planned capability may be `False`, but its
  accompanying result says `not_implemented`, never `upgrade_required`.
- `require_feature()` reports an available alpha capability without an upgrade
  hint. For a known planned capability it returns an explanatory
  `not_implemented` result without pricing language.
- `set_edition()` accepts legacy calls as a non-persisting compatibility no-op:
  its result is `free_alpha`, identifies the requested legacy value, and
  explains that all implemented alpha functionality is free.
- `edition_summary()` is an availability report with one `$0` Free Public
  Alpha entry, an explicit no-gates note, included implemented capabilities,
  and separately named planned capabilities. It has no paid catalog and no
  locked-feature list.

## Configuration and documentation

The three checked-in hub catalog copies use a free-public-alpha schema. Each
entry explicitly identifies whether it is implemented or planned, so the
product does not turn an unfinished roadmap item into a false free-launch
claim. The packaged hub is the runtime authority; the source hub and legacy
configuration mirror it exactly.

`PRICING_AND_EDITIONS.md` remains at its stable path as a short historical
redirect to the free-alpha policy so old links keep working. Public README,
CLI help, governance guidance, and launch collateral say "Free Public Alpha"
and do not invite a user to select or buy a tier.

## Error handling and compatibility

- Legacy `VESTA_EDITION` values are ignored, not persisted or silently treated
  as entitlement.
- Unknown feature IDs remain non-blocking because a missing catalog entry must
  not create a false product gate.
- Planned items are clearly unavailable due to implementation status. They do
  not receive an upgrade hint, price, payment link, or licence instruction.
- The JSON shape retains `edition`, `included_features`, and `catalog` where
  practical so existing diagnostics remain readable. Removed fields are
  replaced by truthful availability fields rather than fabricated prices.

## Test design

Tests are written before implementation and prove the user-facing boundary:

- a default project can export a savings report without changing an edition;
- all implemented alpha features are available with no upgrade hint;
- a legacy env var or `set_edition("pro")` cannot change the active free
  launch state or write an entitlement into project state;
- a planned catalog capability is reported as not implemented, never paid or
  locked;
- packaged, source, and legacy catalog copies agree on the free-alpha schema;
- CLI help and public documentation contain no active paid-tier call to action.

## Acceptance criteria

- No executable Vesta path returns `upgrade_required` for an implemented alpha
  feature.
- `vesta savings --export` succeeds at the default free-alpha state.
- No runtime configuration contains a nonzero price or `min_edition` gate.
- The availability report has no paid catalog, upgrade hint, or locked feature
  list.
- Public copy accurately distinguishes fully free implemented features from
  future, unimplemented work.
- Targeted edition/CLI tests, registry validation, the canonical Python suite,
  static checks, and release smoke checks pass.

## Rollback

This is a bounded removal of launch-time gates. Reverting the commit restores
the earlier self-declared catalog without touching user data because the new
`set_edition()` does not write state. Any later paid offering requires a new
approved design, transparent disclosure, and a separate migration plan.
