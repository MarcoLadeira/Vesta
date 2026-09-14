# Launch Operations Runbook

This runbook implements the operating process for [issue #554](https://github.com/MarcoLadeira/OPai/issues/554), under the parent [launch and community epic #530](https://github.com/MarcoLadeira/OPai/issues/530). It is a pre-launch control: it does not declare Vesta ready, authorise a launch, or replace a release decision.

Use it with the concise [launch checklist](LAUNCH_CHECKLIST.md). The checklist collects evidence; this runbook defines how that evidence becomes a go/no-go decision, a responsible launch, and a product-learning loop.

## Operating principles

- Retained verified value is the outcome. Impressions, votes, downloads, and raw sign-ups are diagnostic signals, not launch-success metrics.
- A public statement must be reproducible, or it must be labelled as a hypothesis or early observation.
- Each community deserves an adapted, disclosed contribution—not a broadcast adapted only by changing its URL.
- Launch evidence never waives reliability, privacy, security, or support obligations.
- A launch can be stopped. The owner, rollback path, and public response are decided before public activity begins.

## Launch readiness scorecard

No launch decision may be marked ready while an unwaived P0 gate is open.

The review owner records the evidence link, status, decision owner, and review date for every row. A missing result, a low sample, or an unreviewed claim is **inconclusive**, never evidence of readiness.

| Gate | Required evidence | Ready only when |
| --- | --- | --- |
| [#518 release governance](https://github.com/MarcoLadeira/OPai/issues/518) | Approved release decision and current blocker/waiver register | No unwaived P0 release gate is open. |
| [#293 installation and distribution](https://github.com/MarcoLadeira/OPai/issues/293) | Tested package, install, upgrade, uninstall, and rollback evidence on supported platforms | A qualified stranger can follow the published path and recover safely. |
| [#515 OPaiBench evidence](https://github.com/MarcoLadeira/OPai/issues/515) | Reproducible benchmark receipt for every quality, cost, latency, or productivity statement | Public claims match the source dataset, method, version, and known limitations. |
| [#529 onboarding and support](https://github.com/MarcoLadeira/OPai/issues/529) | First-use, documentation, and support readiness evidence | A user can reach a first verified outcome and obtain help without founder-only knowledge. |
| [#526–#528 security, privacy, and evidence](https://github.com/MarcoLadeira/OPai/issues/526) | Current privacy, security, and evidence explanations | Public copy matches the implemented controls and escalation paths. |
| [#557 customer economics](https://github.com/MarcoLadeira/OPai/issues/557) | Cohort-level verified-value and support-burden evidence | Significant paid promotion or broad commercial investment has an evidence-backed decision. |
| [#558 name and brand clearance](https://github.com/MarcoLadeira/OPai/issues/558) | Recorded naming, trademark, and discoverability decision | Significant brand investment does not outrun the clearance decision. |

### Minimum evidence thresholds

These are internal release controls, not public performance claims. The review may raise a threshold for a release, but it may lower one only through a documented #518 waiver. The release owner records the cohort definition, observation period, numerator, denominator, sample size, missing data, and known limitations beside each result.

| Evidence | Minimum threshold before a broad launch | Inconclusive or no-go condition |
| --- | --- | --- |
| successful install | At least 10 independent clean install attempts on each supported platform, with >= 90% completing the published journey. | Fewer than 10 attempts, any unresolved P0, or a failure without a documented classification and recovery path. |
| first verified outcome | At least 10 observable or consenting qualified installs, with >= 60% completing the documented first verified outcome within the stated support window. | Fewer than 10 qualified installs, an unknown outcome, or evidence that support cannot reproduce the journey. |
| receipt completeness | 100% of at least 10 sampled verified outcomes include the task, route, cost/evidence state, verification result, and terminal outcome. | Any receipt is missing a required field or distinguishes an estimate from fact incorrectly. |
| support readiness | At least five rehearsed support cases cover install help, known limitation, privacy question, recovery, and escalation; 100% reach a named owner and safe next step. | A rehearsal cannot identify the owner, safe diagnostic data, or escalation route. |
| Day-7 retention | At least 10 observable or consenting users who reached first verified outcome, with >= 40% showing retained verified use at Day-7. | Fewer than 10 eligible users, missing consent-compatible evidence, or retention measured only by attention signals. |

A low sample size remains inconclusive. An inconclusive commercial or retention metric does not override a release gate, but it blocks treating attention as launch success or increasing channel investment without a #518 decision.

### Decision record

The go/no-go review records one of these outcomes:

| Outcome | Meaning | Required next action |
| --- | --- | --- |
| **No-go** | A gate is incomplete, evidence is inconclusive, or a risk is unaccepted. | Keep public launch activity paused; create or update the canonical issue with an owner and priority. |
| **Conditional go** | Every release-critical gate is satisfied, with time-bounded non-P0 follow-up. | Record the owner and review date for each follow-up; enable only the approved surfaces. |
| **Go** | Every applicable gate has reproducible evidence and no unwaived P0 remains. | Start the approved channel plan and the 24-hour review clock. |

## Waiver record

Only [#518](https://github.com/MarcoLadeira/OPai/issues/518) can record a release waiver. A channel post, a launch document, or a verbal decision never creates an implicit waiver.

For each waiver, record all of the following in #518:

| Field | Required record |
| --- | --- |
| owner | The named person accountable for the risk and expiry. |
| evidence | Links to the reproduction, test, receipt, or review supporting the decision. |
| impact | The affected users, trust boundary, and worst credible outcome. |
| mitigation | The specific temporary control, monitoring, or narrowed release scope. |
| expiry | A date or measurable condition that ends the waiver. |
| rollback plan | The owner and tested path for reversing the affected release surface or artifact. |

An expired waiver is an open gate. A waiver does not permit unsupported claims or remove the requirement to tell affected users about a known limitation.

## Public claims and launch-surface consistency

### Claim register and pre-registration

Before drafting a public benchmark, cost, savings, quality, or productivity claim, create a claim record containing:

- Exact proposed wording and the launch surface where it will appear.
- The source dataset, task fixture, baseline, method, model/provider version, price snapshot, and result receipt.
- The known limitations, sample size, counterfactual assumptions, and reproducibility command or link.
- The accountable reviewer and review date.

Every claim record must link to reproducible evidence. If that record is incomplete, use no numeric or comparative claim. A tentative statement must be visibly labelled as a hypothesis or early observation and must not be presented as proof of a general result.

### Required launch surfaces

Every approved launch link is checked against this matrix before publication and again after any copy change:

| Information | Canonical source | Launch-surface rule |
| --- | --- | --- |
| Installation | [install proof](INSTALL_PROOF.md) and the verified release artifact | Never invent an install command before the artifact exists; identify contributor-only instructions as contributor-only. |
| Privacy | [README](../README.md), [security guidance](security.md), and the current privacy controls | State what is local, consented, redacted, or unknown; do not imply silent product telemetry. |
| Support | [SUPPORT.md](../SUPPORT.md) and the support-ready path from #529 | Give users a public support destination and tell them what diagnostic information is safe to share. |
| Known limitations | Release evidence and the relevant gate issue | Name material limitations next to the claim or link; do not hide them in a separate channel. |
| Rollback | [release documentation](RELEASE.md) and the launch kill switch below | Give the current reversal path and distinguish a planned rollback from a tested one. |

## Demo and content asset checklist

No asset is a launch deliverable until its owner, status, evidence link, and known limitations are recorded. A polished recording or post never substitutes for reproducible evidence.

| Asset | Required content | Evidence link and review | Owner and status |
| --- | --- | --- | --- |
| Public demo | A tested installation-to-first-verified-outcome journey using the published artifact or a clearly labelled contributor path. | Link the install proof, recorded version, known limitations, and rollback information; verify that every displayed result is reproducible. | Name the demo owner and mark draft, reviewed, approved, paused, or retired. |
| Technical explainer | The architecture, routing, privacy, receipt, or failure-mode detail needed to evaluate one specific claim. | Link source dataset, method, version, receipt, and known limitations where it makes a comparative statement. | Name the technical reviewer, content owner, and current status. |
| Evidence-backed case study | A before/after verified-outcome and cost comparison using the #515 methodology, not a hand-picked anecdote. | Link the task fixture, baseline, result receipt, reproducibility steps, and known limitations. | Name the evidence owner and mark pending until the required #362 evidence exists. |
| Launch FAQ or known-limitations note | Current install, privacy, support, limitations, incident, and rollback answers. | Link the canonical sources in the launch-surface matrix and date the review. | Name the support owner and mark the review status. |

## Kill switch and rollback

Before public activity, name one launch incident owner and one backup. Both must have the authority to pause public claims while the relevant release owner handles the artifact or service.

The kill switch covers downloads, package publication, update channel, website claims, and incident messaging:

1. Stop new downloads or unpublish the affected package/release according to the tested release path.
2. Pause automatic or promoted update channel messaging; do not ask users to install a known-risky build.
3. Remove or correct affected website claims, social posts, and pinned community content with a timestamped correction.
4. Publish the approved incident messaging with the known impact, immediate safe action, and next update time; do not speculate about cause.
5. Link the incident to the canonical issue, record evidence, and decide whether a rollback, hotfix, or no-go review is required before resuming.

Rehearse this checklist before a broad launch. The rehearsal uses a harmless simulated claim correction and package/update pause; it records elapsed time, missing authority, and the improvements assigned to an owner.

## Channel playbooks

### Shared channel rules

For every channel, read the current rules immediately before posting, disclose the maker relationship, and adapt the contribution to the community's purpose. Link only to tested installation, documentation, privacy, support, known limitations, and evidence pages. Answer critical questions directly; move sensitive data and security reports to the correct escalation path. Do not copy-paste promotion.

The approved post record captures the channel, date, exact copy, disclosure wording, evidence links, rule check, landing destination, owner, and a planned response window.

### Hacker News

- Confirm Show HN and self-promotion conventions on the day of posting.
- Lead with the technical problem, shipped behaviour, evidence, and known limitations—not a slogan or a savings promise.
- State plainly that the author is the maker and answer technical, cost-model, privacy, and failure-mode questions in the thread.
- Do not vote-coordinate, ask friends to astroturf, or redirect criticism into private messages; correct public inaccuracies with evidence.

### Reddit

- Select a community whose rules and current discussion make the topic relevant; obtain moderator permission when its rules require it.
- State the maker relationship in the post, use the community's expected post format, and adapt examples to its technical interests.
- Share reproducible evidence and limitations before linking to a product page. Do not repost the same promotion across subreddits or evade removed-post decisions.
- Treat feedback as product input: acknowledge it, capture a reproduction where appropriate, and create or update the canonical issue.

### GitHub

- Use a GitHub Discussion for questions, workflow exchange, and launch conversation; use Issues only for reproducible bugs, safety problems, or scoped roadmap work.
- Link the tested install path, support guidance, privacy/security information, known limitations, and evidence bundle from the Discussion.
- Apply a clear category and moderation rule. Thank contributors without promising a roadmap item until it has a canonical issue, owner, and priority.
- Route security reports away from public Issues to the Security report path.

### LinkedIn/X

- Disclose that the account represents the maker and use a concise, channel-native explanation rather than copying a forum post.
- Link a single evidence-backed asset; include the limitation or scope necessary to understand the claim.
- Avoid engagement bait, unsupported comparisons, and automated reply campaigns. Keep a timestamped correction visible if a claim changes.
- Use replies to invite qualified technical feedback, not to inflate attention metrics.

### Direct founder outreach

- Contact only people for whom the message is relevant; personalise the reason and make declining easy.
- Disclose the maker relationship and the specific early-stage nature of the request. Do not imply a paid pilot, price, result, or availability that has not been approved elsewhere.
- Ask for a bounded, consent-based conversation or a reproducible install/first-value observation; never request prompts, secrets, private source, or private logs.
- Record only the consented channel attribution and actionable feedback needed for the cohort review.

## Support, security, and incident response

Ordinary questions, installation help, and ideas follow [SUPPORT.md](../SUPPORT.md) and the community/support workflow. Security reports follow [SECURITY.md](../SECURITY.md): do not request or publish secrets, live tokens, private logs, or private repository data in a public Issue or Discussion.

### Feedback routing

Triage every actionable report into an existing canonical issue or a genuinely new issue. The record must include a reproduction, owner, and priority; it must also identify whether the item is a product defect, support/documentation gap, evidence question, security report, or channel-learning signal. Do not create duplicates merely to preserve the launch conversation.

| Situation | First response | Escalation and evidence | Rehearsal |
| --- | --- | --- | --- |
| Security report | Acknowledge privately, provide the responsible reporting route, and avoid collecting proof in public. | Follow SECURITY.md, record the report owner and severity, and publish only an approved advisory or status update. | Run a tabletop report using synthetic metadata; verify private acknowledgement, owner assignment, and public-data boundary. |
| Secret exposure | Ask the reporter to revoke/rotate the secret; remove public exposure where authorised. | Treat it as a security report; preserve only safe metadata and never copy the secret into an issue, receipt, or chat. | Simulate a non-secret token marker; verify removal authority, safe acknowledgement, and that no secret is copied into the record. |
| Repository mutation incident | Pause automation or publication that could repeat the mutation; tell affected users how to preserve their work. | Capture safe audit evidence, classify the authority failure, and require a tested rollback or recovery path before resuming. | Simulate an unauthorised test-file mutation; verify pause, evidence capture, recovery owner, and tested rollback path. |
| Billing or cost discrepancy | Acknowledge the discrepancy without claiming a resolution; preserve the relevant receipt and price/version context. | Route to the cost/evidence owner, reconcile actual versus estimate, and correct any affected public savings claim. | Reconcile a synthetic receipt mismatch; verify estimate/fact distinction, claim pause, owner, and correction path. |
| Provider outage | State the affected provider path, workaround, and next update time without guessing at upstream cause. | Pause dependent launch claims, monitor recovery, and record the impact in the next review. | Simulate a provider-unavailable result; verify workaround wording, incident owner, next-update time, and dependent-claim pause. |

Run every rehearsal before public launch and whenever the owner, provider, package route, or escalation path changes. Record elapsed time, missing authority, remediation owner, and a canonical backlog update. For a public incident, the response owner prepares a short factual update: what is known, who is affected, the immediate safe action, what has been paused, and when the next update will arrive. The update does not contain customer data, secrets, or unverified root-cause claims.

## Privacy-safe attribution and cohort review

Channel attribution is consent-compatible and separate from product telemetry. It exists to decide where qualified users reach verified value; it is not a reason to collect prompts, source code, credentials, private paths, or silent behavioural tracking.

Use aggregate or voluntarily supplied channel attribution. Keep the smallest practical record: channel, campaign/date, consent status, qualified install status, first verified outcome status, retained verified use at Day-7 and four-week checkpoints, useful feedback, and support burden. Do not join attribution records to product telemetry without an explicit, documented consent-compatible basis.

Review channel cohorts using these questions:

1. Did a qualified install reach a first verified outcome?
2. Did the cohort show Day-7 and four-week retained verified use, with sample size and missing data shown?
3. How much useful feedback and how many design-partner-quality conversations resulted?
4. What support burden and incident rate accompanied activated use?
5. Should the channel receive a continue, change, or stop decision based on retained value rather than attention?

## Post-launch reviews

Open the review clock only after an approved launch activity begins. Each review links the evidence used, assigns an owner to every action, and creates or updates one canonical backlog update rather than scattering follow-ups across duplicate issues.

| Review | Required evidence | Required decision |
| --- | --- | --- |
| 24-hour | Launch-surface correctness, install attempts, immediate support load, incidents, corrections, and channel-rule feedback | Record owner, continue/change/stop decision, urgent correction or rollback, and canonical backlog update. |
| 7-day | Qualified install conversion, first verified outcome, Day-7 retained verified use, useful feedback, support burden, and claim reproducibility | Record owner, continue/change/stop decision for each active channel, and canonical backlog update. |
| 30-day | Four-week retained verified use where available, support and incident trend, design-partner conversations, channel economics, and unresolved objections | Record owner, continue/change/stop investment decision, roadmap implications, and canonical backlog update. |

The review must name what converted, what failed, objections, support load, incidents, limitations of the evidence, and the next owner. A channel with attention but no qualified or retained verified value is changed or stopped; it is not treated as a successful launch.

## Handoff checklist

Before any channel action, the launch owner confirms:

- The scorecard decision and any #518 waiver are current.
- Every destination link has a tested install, privacy, support, known-limitations, and rollback path.
- The claim register exists for every comparative or numeric statement.
- The channel owner has read current community rules, included the maker disclosure, and prepared a response window.
- The incident owner and backup completed the kill-switch rehearsal.
- The 24-hour, 7-day, and 30-day review owners and calendar entries exist.

If any item is missing, the correct decision is no-go until the evidence or owner exists.
