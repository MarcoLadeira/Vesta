# QA Round 8 — ultimate novice remote-control campaign

**Date:** 2026-07-25
**Branch:** `codex/ultimate-opai-qa`
**Baseline:** merged PR #512 (`e2c37c8`), including the PR #511 safety and status fixes
**Method:** real Windows desktop interaction, reproducible evidence, test-first fixes, and repeated live retesting

## Mission

Exercise OPai as a curious first-time user who wants to:

- understand an unfamiliar repository;
- plan and implement coding work;
- review file changes and command output;
- use run modes, models, agents, context, prompts, and inspector controls;
- create commits, push branches, and work with GitHub pull requests;
- recover safely from errors, cancellation, stale state, and interrupted sessions.

Every reproducible product defect belongs in this file before it is fixed. Each fix must include automated regression coverage where practical and a live desktop retest. PR #511's push-consent and truthful-status behavior is a release-blocking compatibility requirement throughout this campaign.

## Guardrails

- Do not weaken one-time approval for pushes, deploys, destructive commands, or external mutations.
- Do not accept prose, pills, workflow summaries, or evidence cards that disagree about the outcome.
- Do not claim a GitHub action succeeded without observed command evidence or independent remote verification.
- Do not spend money on cloud model calls without explicit approval.
- Preserve unrelated local files and user work.

## Coverage ledger

| Area | Novice journey | Status | Evidence / finding |
|---|---|---:|---|
| Launch and workspace | Open OPai, identify current project and branch, switch projects | Pending | |
| Navigation | Chat, Prompt Library, Insights, Settings, Inspector, sidebar controls | Prompt Library fix verified; deeper coverage pending | QAR8-02 |
| Composer | Empty prompt, multiline input, context, attachments, send/stop controls | Draft-loading fix verified; deeper coverage pending | QAR8-02 |
| Run modes | Ask, Plan only, Ask before edits, Approve edits, Auto-apply, Full Auto | Fix verified; deeper coverage pending | QAR8-01 |
| Models and routing | Account, API, free/local choices, unavailable-provider recovery | Pending | |
| Coding workflow | Explain, plan, edit files, review diffs, accept/reject changes | Pending | |
| Commands and safety | Safe commands, blocked commands, one-time approvals, cancellation | Pending | |
| Git workflow | Status, diff, commit, branch, push, remote verification | Pending | |
| GitHub workflow | Inspect PR, comment, create/update PR, verify external results | Pending | |
| Agents and tools | Agent/tool visibility, progress, errors, handoff, completion evidence | Pending | |
| History and recovery | New chat, recent chats, resume, stale request isolation | Pending | |
| Cost and privacy | Spend/savings display, budget controls, local-only messaging | Pending | |
| Keyboard/accessibility | Tab order, focus, shortcuts, menus, dialogs, readable states | Pending | |
| Responsive/visual QA | Window resize, clipping, spacing, contrast, loading/error states | Pending | |
| PR #511 regression | Push confirmation and status/prose/summary agreement | Pending | |
| PR #512 regression | Per-file diff review plus composer/status fixes | Pending | |

## Reproducible defects

### QAR8-01 — the same run mode has different names across primary controls

**Severity:** High — this regresses the PR #511 requirement that every visible status signal agree.

**Steps:**

1. Launch OPai in a workspace whose saved mode is Full Auto.
2. Compare the top-bar status, composer mode control, composer summary, and CLI mirror.
3. Repeat with Safe Auto / Ask before edits.

**Observed:**

- The top bar displayed `Full Auto`.
- The composer control and summary displayed `Auto-apply`.
- The CLI mirror used the internal `--mode full-auto` identifier.
- The same split exists for the engine label `Safe Auto` versus the novice-facing label `Ask before edits`.

**Expected:**

Primary GUI surfaces must use one novice-facing name for the active mode. Internal CLI identifiers may remain technical, but the top bar, composer control, composer summary, dialogs, and inspector must not look like different modes are active.

**Root-cause evidence:**

`app.js` paints the header from the engine-provided `state.mode.label`, while `composer.js` owns a separate plain-language `MODE_LABEL` map. There is no shared presentation-label function, so the two surfaces drift by construction.

### QAR8-02 — Prompt Library templates leave Send disabled

**Severity:** High — the primary curated onboarding path becomes a dead end.

**Steps:**

1. Open Prompt Library.
2. Choose `Use prompt` on any template.
3. Observe the populated composer, Send button, and footer reason.

**Observed:**

- The template text was loaded and the prompt field received focus.
- Send remained disabled.
- The footer continued to say `Write a prompt before sending.`

**Expected:**

Loading a non-empty template must immediately enable Send and clear the empty-prompt warning without requiring the user to type an extra character.

**Root-cause evidence:**

The normal input event calls `updateComposerAvailability()`, but `usePrompt()` assigns `#input.value` programmatically and only resizes/focuses the field. Other programmatic draft-loading paths repeat the same incomplete sequence, so availability state can become stale whenever text is inserted without a keyboard event.

## Fix and retest log

### QAR8-01

- Added one shared novice-facing mode-label function and used it in the header, composer context, composer menu, composer summary, and inspector run-mode row.
- Red evidence: the focused Playwright case received `Auto · Safe Auto · ...` while the composer read `Ask before edits`.
- Green evidence: the new case and the existing Full Auto-to-Ask synchronization regression both passed.
- Live retest: relaunched the desktop app in the original Full Auto workspace. The top bar, composer control, and composer summary all displayed `Auto-apply`; the CLI mirror correctly retained the technical `--mode full-auto` argument.

### QAR8-02

- Added one `setComposerDraft()` path that updates the text, height, availability, warning, and optional focus together.
- Routed templates, recent chats, starter chips, initial tasks, stopped-request editing, slash-command clearing, send clearing, build prompts, and plan handoff through it.
- Red evidence: the populated template case timed out because `#send` remained disabled.
- Green evidence: all five Prompt Library Playwright cases passed, including immediate Send enablement and warning removal.
- Live retest: relaunched OPai, selected `Explain this repo`, and observed a focused populated prompt, enabled Send control, and no empty-prompt warning.

## Session notes

- Campaign branch was created directly from `origin/main` after PR #512 merged.
- The full baseline verification suite will be rerun after the exploratory passes and again before the final PR conclusion.
