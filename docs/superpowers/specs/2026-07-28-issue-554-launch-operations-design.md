# Issue #554 Launch Operations Design

**Status:** Approved by the maintainer on 2026-07-28.

## Purpose

Create one evidence-led operating system for deciding whether Vesta may launch,
adapting launch communication to each community, responding to incidents, and
turning post-launch evidence into explicit product and channel decisions. This
implements canonical child issue #554 and supports, but does not close, parent
epic #530.

## Constraints

- A launch stays blocked while any unwaived P0 release gate is open.
- A waiver belongs to #518 and must record an owner, evidence, impact,
  mitigation, expiry, and rollback plan.
- Public claims require reproducible evidence or a clear hypothesis/early
  observation label.
- The current repository must not imply that a public artifact is available or
  that Vesta is launch-ready before the release gates are met.
- Attribution is channel-level, consent-compatible, and separate from product
  telemetry. Impressions, votes, downloads, and raw sign-ups are diagnostic
  signals rather than launch-success metrics.

## Considered approaches

1. Extend only `docs/LAUNCH_CHECKLIST.md`. This keeps one small file but makes
   the channel, incident, evidence, and review procedures hard to use and
   validate.
2. **Recommended: create one canonical operations runbook and retain the
   existing checklist as its concise entry point.** This gives maintainers one
   operational source of truth while preserving the existing documentation
   link.
3. Build a launch dashboard or product telemetry feature. That would create
   unsupported product behaviour and is out of scope for a documentation and
   operating-process issue.

## Design

### Canonical runbook

Create `docs/LAUNCH_OPERATIONS.md` with clearly named, machine-checkable
sections:

1. A go/no-go scorecard covering #518, #293, #515, #529, #526–#528, #557, and
   #558, including minimum-evidence thresholds and the rule that a small sample
   is inconclusive.
2. A waiver record template that links the decision to #518 and includes every
   required field.
3. A public-claim register, benchmark/savings pre-registration checklist, and
   launch-surface consistency matrix for installation, privacy, support, known
   limitations, and rollback information.
4. A launch kill switch and rollback checklist covering downloads, package
   publication, update channels, website claims, and incident messaging.
5. Distinct playbooks for Hacker News, Reddit, GitHub, LinkedIn/X, and direct
   founder outreach. Every playbook requires local rule review, maker
   disclosure, adapted copy, evidence links, and a response path; none permits
   copy-paste promotion.
6. Separate support, security-report, and public-incident paths, including
   rehearsed responses for secret exposure, repository mutation, billing/cost
   discrepancies, and provider outages.
7. Privacy-safe channel attribution and cohort-review rules based on qualified
   install, first verified outcome, retained verified use, useful feedback,
   and support burden.
8. 24-hour, 7-day, and 30-day retrospective templates that assign an owner and
   record a continue/change/stop decision plus a canonical backlog update.

### Existing navigation

Update `docs/LAUNCH_CHECKLIST.md` so it points to the canonical runbook,
states that it is a pre-launch gate rather than launch permission, and uses the
same evidence-first claims posture. Add the runbook to the README's linked
launch material so it is discoverable without duplicating its procedures.

### Automated documentation contract

Add `tests/test_launch_operations.py`. It will load the canonical runbook and
the updated navigation documents, then assert the required scorecard,
playbooks, escalation paths, attribution rules, retrospective cadence, and
cross-document links. This is intentionally a content contract: changing a
heading or removing a safety requirement requires an explicit test update.

## Validation

- Run the new documentation-contract test alone after first observing it fail
  because the runbook does not exist.
- Run the targeted launch-document tests and the repository's documentation
  validation command.
- Run static format/lint checks and the applicable broader Python suite.
- Inspect the final diff for unsupported launch claims, duplicate operating
  sources, secret-like content, and stale payment/private-launch language.

## Non-goals

- Publishing an artifact, opening launch channels, creating telemetry, making
  paid promotion decisions, or declaring Vesta launch-ready.
- Closing #530, #519, #362, or any release-gate issue. The implementation PR
  will close #554 and identify #530 as its parent epic.
