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
- Settings repeated `Full Auto` in the Overview card and Permissions copy.
- Approval cards and plan handoff repeated `Full Auto` / `Safe Auto` while the surrounding controls used `Auto-apply` / `Ask before edits`.
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

### QAR8-03 — Models & Routing lies about a pinned Auto-apply default

**Severity:** High — a Settings control reports a safer default than the one the app will actually use.

**Steps:**

1. Pin Auto-apply in a workspace.
2. Confirm the header, composer, and Settings Overview display `Auto-apply`.
3. Open Settings → Models & Routing.
4. Compare the `Default run mode` select with the other active-mode signals.

**Observed:**

- Header, composer, and Overview displayed `Auto-apply`.
- The `Default run mode` select displayed `Ask`.
- The helper copy below it simultaneously said Auto-apply can only be pinned from the composer.

**Expected:**

The control must truthfully display the current `Auto-apply` default without allowing Settings to select or silently persist that privileged mode. Users must still pin Auto-apply only through the composer acknowledgement.

**Root-cause evidence:**

The Settings select deliberately omits `full-auto` from its options. When the persisted value is `full-auto`, HTML falls back to the first available option (`ask`), so the displayed selection becomes a lie even though the safety restriction is working.

### QAR8-04 — Privacy summary says prompts are stored while the policy says they are not

**Severity:** Medium — contradictory privacy copy makes the local-data guarantee difficult to trust.

**Steps:**

1. Open Settings → Privacy & Data.
2. Read the `Data stays on this device` summary.
3. Compare it with the first detailed privacy statements below.

**Observed:**

- The summary said `Prompts, the ledger, and audit history are kept locally`.
- The detail said `Raw prompts are never stored; the local ledger keeps one-way task hashes and counts only`.
- A separate statement clarified that saved chat is redacted and kept locally.

**Expected:**

The summary must distinguish redacted saved chat from raw prompts and agree with the detailed policy.

**Root-cause evidence:**

The summary is a separate static string from the payload-backed privacy statements. It used the broad word `Prompts` even though the implementation and detailed copy describe redacted chat plus one-way ledger hashes.

## Fix and retest log

### QAR8-01

- Added shared novice-facing mode-label and mode-copy functions. Header, composer, inspector, Settings, confirmation cards, command/edit approvals, and plan handoff now use them.
- Red evidence: the focused header case received `Auto · Safe Auto · ...` while the composer read `Ask before edits`; five additional approval/plan cases then proved the remaining raw labels were still visible.
- Green evidence: the model-mode suite passed 10/10, the Settings regression set passed 17/17, and the six focused approval/plan/workspace cases passed 6/6.
- Broader regression evidence: 40 unaffected cases passed in the combined approval/plan/Settings/workspace run; the one stale old-label assertion was corrected and its full workspace suite then passed 4/4. JavaScript unit tests passed 74/74, design-token checks passed 2/2, and the Python UI honesty sweep passed 10 tests plus 19 subtests.
- Live retest: relaunched the desktop app repeatedly in the original Full Auto workspace. The top bar, composer control, composer summary, Settings Overview, Permissions heading, current-mode card, and push-approval note all displayed `Auto-apply`; no raw `Full Auto` remained on those live screens. The CLI mirror correctly retained the technical `--mode full-auto` argument.

### QAR8-02

- Added one `setComposerDraft()` path that updates the text, height, availability, warning, and optional focus together.
- Routed templates, recent chats, starter chips, initial tasks, stopped-request editing, slash-command clearing, send clearing, build prompts, and plan handoff through it.
- Red evidence: the populated template case timed out because `#send` remained disabled.
- Green evidence: all five Prompt Library Playwright cases passed, including immediate Send enablement and warning removal.
- Live retest: relaunched OPai, selected `Explain this repo`, and observed a focused populated prompt, enabled Send control, and no empty-prompt warning.

### QAR8-03

- When `full-auto` is the persisted default, Models & Routing now adds a selected, disabled `Auto-apply` option solely to report the current truth. Unpinned workspaces still do not offer Auto-apply in this Settings picker.
- Red evidence: the new pinned-default case received select value `ask` instead of `full-auto`.
- Green evidence: all seven Models & Routing / budget cases passed. The regression asserts `Auto-apply` is selected, its option is disabled, and opening the page saves nothing.
- Live retest: after a full desktop restart, header, composer, Overview, and the Models & Routing `Default run mode` control all displayed `Auto-apply`. No setting was changed during verification.

### QAR8-04

- Changed the Privacy summary to say `Redacted saved chat, the ledger, and audit history are kept locally`, matching the detailed no-raw-prompt policy.
- Red evidence: the factual-privacy case received the old `Prompts ... are kept locally` summary.
- Green evidence: the full Permissions & Privacy suite passed 4/4, including both cancel and confirm paths for the styled saved-chat clear.
- Live retest: after a full desktop restart, the summary explicitly said `Redacted saved chat` while the detailed statement continued to say `Raw prompts are never stored`.

## Session notes

- Campaign branch was created directly from `origin/main` after PR #512 merged.
- The full baseline verification suite will be rerun after the exploratory passes and again before the final PR conclusion.
