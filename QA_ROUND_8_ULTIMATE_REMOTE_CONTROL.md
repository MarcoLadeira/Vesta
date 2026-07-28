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

### QAR8-05 — Auto silently sends repository context to a cloud provider

**Severity:** Critical — the live routing behavior contradicts the app's privacy and confirmation promises.

**Steps:**

1. Leave the default model set to `Auto`.
2. Observe the composer summary `Auto-apply · Auto · local`, the sidebar `Local only · no telemetry`, and the inspector `Local-first · cloud on confirm`.
3. From the empty chat, choose `Summarize my changes`.
4. Observe the provider timeline and receipt.

**Observed:**

- No cloud confirmation card appeared.
- Auto sent the repository task and context to Gemini's public API.
- The timeline said `Trying gemini`, and the receipt identified `free:gemini:gemini-3.1-flash-lite`.
- The call cost `$0.0000`, but data still left the device.

**Expected:**

Every cloud route, including a zero-cost/free-tier route chosen by Auto, must stop before transmission and name the provider in a confirmation card. A local or cached route may continue without confirmation.

**Root-cause evidence:**

`handle_gui_message()` computes `free_allow_cloud = allow_cloud or auto_active`. Merely selecting `Auto` therefore sets cloud authority for a free provider, bypassing the lower-level `confirmation_required` gate. This directly contradicts the public `ask()` contract, the Cost Firewall setting `require_confirmation_for_cloud`, and the inspector copy `cloud on confirm`.

### QAR8-06 — a confirmation-only turn is displayed as money spent

**Severity:** High — the cost strip claims a charge for a provider call that never started.

**Steps:**

1. Trigger an Auto route with no capable local model.
2. Stop at the new named cloud-confirmation card; do not approve it.
3. Compare `cloudStarted`, the activity log, and the top status strip.

**Observed:**

- The result correctly said `cloudStarted: false` and no provider ran.
- The status strip nevertheless displayed `$0.0002 spent`.

**Expected:**

An awaiting-confirmation result must show no spend and no savings. Estimates may be described as projections, but must never be labelled `spent`.

**Root-cause evidence:**

The Auto confirmation result attaches a normal estimated savings receipt. `stripFinalize()` renders any positive `estimated_actual_usd` from that receipt as `spent`, without considering that the terminal state is awaiting input and made zero model calls.

### QAR8-07 — confirmation cards offer a dead-end Retry action

**Severity:** Medium — the first action on a blocked card loops instead of resolving the blocker.

**Steps:**

1. Trigger the named Auto cloud-confirmation card.
2. Compare the available actions.

**Observed:**

The card offered `Retry`, `Confirm Gemini`, `Open Settings`, and `Switch model`. Retrying with the same unapproved Auto request can only repeat the same confirmation gate.

**Expected:**

Cards with a dedicated confirmation action must not offer a generic Retry. The user should confirm the named action, change configuration, or switch models.

**Root-cause evidence:**

`renderErrorCard()` enables Retry for every status except `needs_model`; it does not exclude the three statuses that already render dedicated confirm/continue buttons.

### QAR8-08 — restoring a blocked session loses its approval controls

**Severity:** High — a safely paused task becomes impossible to continue after restart.

**Steps:**

1. Stop at an Auto cloud-confirmation card without approving it.
2. Close and relaunch OPai.
3. Choose `Resume work`.

**Observed:**

- The prompt and blocked explanation were restored.
- The summary said `Next: Resolve the requested approval or input, then retry`.
- The original `Confirm Gemini` action, along with the configuration/model alternatives, was gone.

**Expected:**

Resuming must restore the exact pending approval action without granting it. The provider call must still require a fresh click on the named confirmation button.

**Root-cause evidence:**

The durable workflow keeps the exact selected provider, but `restoreSession()` renders persisted assistant messages as plain Markdown and never reconstructs a pending action from workflow safety state. The user sees the blocker but cannot act on it.

### QAR8-09 — Context Waste actions are inert and misleading

**Severity:** Medium — novice users are offered cleanup controls that neither preview nor safely start the advertised work.

**Steps:**

1. Open Insights → Context Waste.
2. Select `Preview cleanup`.
3. Observe the result.

**Observed:**

- No cleanup preview appeared.
- The only feedback was `Run it from your terminal.`
- No terminal command was shown or copied.
- The adjacent `Generate ignore files` action used the same generic fallback even though its metadata correctly marked it as a confirmed config mutation.

**Expected:**

`Preview cleanup` should render the existing read-only cleanup analysis inside OPai. `Generate ignore files` should show a scoped one-time approval before calling the existing additive ignore generator. Denying it must write nothing.

**Root-cause evidence:**

The dashboard view model exposes `cleanup_preview` and `generate_ignores`, and `app_state` already implements both operations, but `runAction()` only dispatches panic and repair. Every other action without a shell command falls through to a generic terminal toast. The classic desktop action dispatcher has the same gap.

### QAR8-10 — Benchmark, proof export, and workflow actions are also inert

**Severity:** High for proof export; Medium for benchmark/workflows — advertised primary actions do not perform their named operation.

**Steps:**

1. Open Insights → Benchmark and select `Run benchmark gate`.
2. Open Insights → Proof Bundle and select `Export JSON`.
3. Open Insights → Workflows and inspect `Copy selected command`.

**Observed:**

- The benchmark gate and proof export both fell through to `Run it from your terminal` without showing a command or result.
- Proof export never displayed its required local-file confirmation and never called the existing redacted export function.
- Workflows offered a `Copy selected command` action even though the page has no selectable workflow state; clicking it had no useful target.

**Expected:**

The benchmark gate should render the local read-only result in OPai. JSON and Markdown proof exports should name their exact local paths and require one-time approval before writing. The workflow action should copy an unambiguous valid command.

**Root-cause evidence:**

These production action IDs were not handled by either dashboard dispatcher. The backend already implements the benchmark gate and proof writer, but no GUI tool requests exposed the two export formats. The workflow action was created without a `command`, guaranteeing the generic fallback.

### QAR8-11 — sidebar Clear history destroys saved work without confirmation

**Severity:** High — a single accidental click irreversibly removes both recent chats and durable recovery state.

**Steps:**

1. Keep a blocked resumable task and several recent chats in the workspace.
2. Select `Clear history` in the sidebar.
3. Observe the chat list and recovery card.

**Observed:**

- OPai immediately called the clear bridge.
- All recent chats disappeared.
- The pending recovery session and its Resume choice were also removed.
- No confirmation or Cancel action appeared.

**Expected:**

The sidebar must disclose that both saved chats and recovery data will be deleted, state that the action cannot be undone, and require explicit confirmation. Cancel must preserve both datasets.

**Root-cause evidence:**

`renderRecents()` described the control as `deletable in one click` and called `bridge.clearRecents()` directly. Settings → Privacy already used the shared styled confirmation for the same destructive operation, so the two entry points had contradictory safety behavior.

### QAR8-12 — first-run onboarding understates the cloud confirmation boundary

**Severity:** Medium — safety copy contradicts the actual all-cloud consent rule.

**Steps:**

1. Open OPai with a fresh workspace profile.
2. Advance to onboarding steps 2 and 3.
3. Compare their cost-firewall wording with a default Auto task.

**Observed:**

- Step 2 said OPai only asks before a `paid cloud model`.
- Step 3 said `a paid model always asks first`.
- A free-tier Gemini handoff correctly required confirmation, so the tutorial taught a narrower boundary than the product enforced.

**Expected:**

Onboarding must say every cloud model requires confirmation, regardless of price, matching the live privacy card and the PR #511-priority safety behavior.

**Root-cause evidence:**

Both strings were hard-coded before the all-cloud gate was strengthened and had no regression assertion tying tutorial copy to the current consent contract.

### QAR8-13 — Open app workspace does not open a scaffold nested in a Git repo

**Severity:** High — the primary handoff from app creation reports success but leaves Build mode unavailable.

**Steps:**

1. Create a deterministic New app inside an existing Git workspace.
2. Select `Open app workspace`.
3. Inspect the header, recovery card, and composer.

**Observed:**

- OPai displayed `Workspace switched`.
- The header remained on the parent repository.
- The parent repository's blocked recovery session reappeared.
- The generated app's Build-mode composer was not activated.

**Expected:**

An OPai Build manifest identifies the generated directory as an intentional workspace boundary. Opening it must keep that exact directory selected, isolate its workspace history, and activate Build mode even when an enclosing Git worktree exists.

**Root-cause evidence:**

Every GUI launch/switch passed the selected path through `active_repo_context()`, which always collapses a nested folder to the enclosing Git top level. `_workspace()` also looked for the Build manifest at that top level instead of the selected directory.

### QAR8-14 — Build-mode cloud confirmation is a dead end and loses status truth

**Severity:** High — the generated-app workflow cannot continue safely when no local model is available.

**Steps:**

1. Open a scaffolded app in Build mode with Auto selected.
2. Ask for a file change while no capable local model is available.
3. Inspect the result, then close and resume the blocked Build.

**Observed:**

- The shared pipeline correctly stopped before Gemini, but the Build result rendered as a non-actionable `Build failed` card.
- No confirmation button was available.
- An intermediate repair produced a generic `Confirm cloud fallback` button because the Build wrapper dropped the exact model metadata.
- The top strip said `Failed` while the inspector and pipeline said `Blocked`.
- On resume, the approval could be reconstructed, but its selection had no durable Build-channel identity and could continue through ordinary Chat instead of `bridge.build`.

**Expected:**

The Build card must name the exact cloud model, remain visibly Blocked, and resend on the Build channel with a one-turn grant only after the user clicks confirmation. A closed/reopened blocked Build must preserve the same channel and exact provider without persisting cloud authority.

**Root-cause evidence:**

`run_build_request()` hard-coded `allow_cloud=False`, discarded the pipeline's safe confirmation fields and completion verdict, and returned only a generic error subset. The web Build sender had no retry selection or consent fields, and `onBuildReply()` collapsed every non-OK result to Failed. Durable thread persistence preferred the nested agent intent (`explain`) over the outer `build` execution channel, while resume reconstructed every approval as normal Chat.

### QAR8-15 — Context picker actions are labelled but do nothing

**Severity:** Medium — a primary novice workflow is a dead control.

**Steps:**

1. Open `Add context`.
2. Select `Attach files…` or `Add a folder…`.

**Observed:**

Both actions merely focused the manual path field. No picker opened and no context was added.

**Expected:**

Each ellipsis action must open the corresponding native picker and add only workspace-relative context chips. Host paths outside the active workspace must never reach the browser.

**Root-cause evidence:**

Both click handlers called only `focusDraft()`. The Qt bridge had no file/folder picker slots and the context composer had no host-picker API.

### QAR8-16 — Stop is visible but disabled during an active request

**Severity:** High — users can lose control of an in-flight task.

**Steps:**

1. Enter a prompt and select Send/Build.
2. Try to click the control after it changes to `Stop`.

**Observed:**

The button had the `Stop generation` accessible name and stop styling but retained the disabled state from the now-empty prompt.

**Expected:**

The active request's Stop control must always be enabled.

**Root-cause evidence:**

Submitting cleared the draft and disabled Send before `setBusy(true)`. The busy transition changed the label and styling but only recomputed availability when leaving busy state.

### QAR8-17 — “Use this repository” targets the enclosing repo, not the active app

**Severity:** Medium — nested Build workspaces receive a bogus context reference.

**Steps:**

1. Open a generated app nested inside a parent Git repository.
2. Open `Add context`.
3. Inspect and select `Use this repository`.

**Observed:**

The row named the parent sandbox and added `<parent-name>/`, interpreted relative to the generated app root where that path does not exist. After correcting the scope, the long exact workspace label overflowed into the action title.

**Expected:**

Repository context must mean the active workspace root (`./`), identify that workspace, and truncate a long visual label without hiding its full accessible text.

**Root-cause evidence:**

The composer preferred `workspace.name` (the enclosing Git repository) over `workspace.label`, then constructed the context path by appending `/` to that name. The metadata span had no width or overflow rule.

### QAR8-18 — the advertised global model shortcut is a no-op outside Chat

**Severity:** Medium — a documented keyboard path silently fails on primary pages.

**Steps:**

1. Open Prompt Library.
2. Press `Ctrl+M`, advertised in the command palette as `Change model`.

**Observed:**

Nothing visible happened. The hidden Chat composer opened its model popover behind Prompt Library, so the user remained on the same page with no picker.

**Expected:**

The global shortcut and command-palette action must navigate to the model picker's owning Chat view and open the picker visibly.

**Root-cause evidence:**

Both entry points called `openModelPicker()` directly. That function toggled the composer popover but never switched to Chat first.

### QAR8-19 — “Manage models” opens generic Settings Overview

**Severity:** Medium — a labelled destination drops the user at the wrong page.

**Steps:**

1. Open the model picker.
2. Scroll the populated model list to its footer.
3. Select `Manage models`.

**Observed:**

OPai opened Settings Overview. The user still had to discover and select Models & Routing manually.

**Expected:**

The action must open the existing Models & Routing page directly.

**Root-cause evidence:**

The composer called a no-argument `openSettings()` bridge helper, and that helper only switched the top-level view. It supplied no Settings deep link.

### QAR8-20 — “Run connection doctor” does not reveal the doctor

**Severity:** Medium — a command-palette action silently lands on an unrelated Settings page.

**Steps:**

1. Leave Settings on Models & Routing or another non-provider page.
2. Open the command palette and run `Run connection doctor`.

**Observed:**

OPai stayed on the previously selected Settings pane. The Connection Doctor and its provider results were not visible.

**Expected:**

The command must open Providers & Connections, whose render path refreshes and displays Connection Doctor results.

**Root-cause evidence:**

The `doctor` command only called `switchView("settings")`, so the existing Settings hash chose whichever pane was active previously.

### QAR8-21 — cancelling New app leaves a blank chat canvas

**Severity:** Medium — the first-time app-creation journey ends in an unexplained empty screen.

**Steps:**

1. From an empty chat, select `New app`.
2. Optionally submit the empty form to see its inline validation.
3. Select `Cancel`.

**Observed:**

The New app card disappeared, but OPai left the entire central chat canvas blank. The welcome explanation, starter prompts, and keyboard hint did not return.

**Expected:**

If no other chat messages remain, cancelling New app must restore the normal empty-chat welcome state. Cancelling from a non-empty conversation must preserve that conversation without adding a welcome card.

**Root-cause evidence:**

Opening New app uses the normal message renderer, which hides `#empty`. Its Cancel handler removed only the generated message element and never restored `#empty` when the thread became message-free.

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

### QAR8-05

- Auto now stops before every free-tier cloud candidate when no per-turn cloud grant exists. The confirmation names the exact provider, states that task and compact project context will leave the device, and preserves `cloudStarted: false`.
- Confirmation continues with the exact reviewed model ID instead of recomputing Auto and potentially changing providers after consent.
- Red evidence: the live default-Auto starter task contacted Gemini immediately; the new contract test then observed `opai.app_state.ask()` being called before any confirmation result could be returned.
- Green evidence: the Auto/routing safety set passed 32/32, and the browser test proved confirmation resends the exact named free model with `allowCloud: true`.
- Live retest: after a source-build restart, the same Auto prompt stopped at `Needs your confirmation`, named Gemini, explicitly said context would leave the device, and showed no provider response.

### QAR8-06

- Awaiting-confirmation results now carry an empty receipt because no provider call, spend, or saving exists yet.
- Red evidence: the live blocked turn displayed `$0.0002 spent`; the regression then received a populated estimated receipt instead of `{}` while `cloudStarted` was false.
- Green evidence: the Auto/routing safety set passed 32/32, including the no-receipt assertion, and the cloud-confirmation browser test passed.
- Live retest: after a source-build restart, the same blocked Gemini card displayed no cost in the status strip. The inspector retained the real daily total from the earlier provider call and did not add a charge for this blocked turn.

### QAR8-07

- Generic Retry is now hidden for model setup, free-cloud confirmation, Auto cloud confirmation, and usage-limit confirmation states. Their dedicated confirm/configure/switch actions remain.
- Red evidence: the cloud-confirmation browser test found one Retry button.
- Green evidence: the focused assertion passed as part of 30/30 chat, error-recovery, and provider-auth browser tests.
- Live retest: after a source-build restart, the Gemini card showed only `Confirm Gemini`, `Open Settings`, and `Switch model`. No Retry action was present.

### QAR8-08

- The workflow now persists an inert `pending_action` containing only the action kind and exact named cloud model; it persists no cloud authority.
- Resume reconstructs the named confirmation card from that safe metadata and the redacted saved prompt. `allowCloud: true` is still added only by clicking the restored confirmation button.
- The recovery summary now prefers the workflow's exact next action over a generic checkpoint fallback, so it no longer says `then retry` beside a card with no Retry.
- Red evidence: the backend result had no pending action, and the resume browser test could not find the named confirmation button. A second red assertion received the generic `then retry` copy instead of the exact workflow action.
- Green evidence: 32 Python routing/resume tests plus 3 subtests passed; 17 combined chat/resume browser tests passed; the standalone resume suite passed 7/7 after the copy correction; Ruff passed.
- Live retest: created a fresh blocked Gemini turn, closed OPai without approval, relaunched, and chose `Resume work`. The exact `Confirm Gemini · 3.1 Flash-Lite (free tier)` card returned with no Retry and no provider call.

### QAR8-09

- Connected both Context Waste actions to the existing local app-state operations on the web and classic desktop surfaces.
- `Preview cleanup` now runs the non-mutating profiler and renders potential token/byte reduction, top generated/cache sources, suggested ignore files, and an explicit no-deletion note inside chat.
- `Generate ignore files` now enters the shared one-time approval card. Its scope identifies supported AI ignore files as additive and user-rule-preserving; approval is the only path to the generator.
- Red evidence: two Python contracts failed because both tool names were unknown, and the live app only displayed `Run it from your terminal.`
- Green evidence: the desktop GUI module passed 49/49 tests. The focused browser regression passed and proved preview dispatch, in-app output, a visible approval card, and zero apply calls after Deny.
- Live retest: after a source-build restart, `Preview cleanup` rendered the full local analysis in chat with an explicit no-deletion note. `Generate ignore files` opened a scoped `Config change` approval card; choosing Deny reported `nothing was changed` and did not invoke the generator.

### QAR8-10

- Connected `Run benchmark gate` to the local benchmark tool so its result renders in chat without starting a model request.
- Added separate JSON and Markdown proof-export tools. Each names its exact `.opaihub/proof-bundle.*` target, excludes raw prompts/secrets, and enters the shared one-time approval flow before any file write.
- Replaced the impossible `Copy selected command` workflow action with `Copy workflow list command` and the valid `opai guard list` command.
- Applied the same action dispatch to the web and classic desktop surfaces.
- Red evidence: the live benchmark and proof buttons only produced the generic terminal toast; two focused browser cases could not find a tool result or approval card; the backend export names were unknown; the workflow view-model assertion received a commandless action.
- Green evidence: desktop GUI tests passed 50/50; the combined Benchmark, Proof Bundle, and Agents/Workflows browser sweep passed 13 existing cases plus both new production-action cases after correcting a strict test locator; Ruff and diff checks passed.
- Live retest: after a source-build restart, the benchmark gate rendered the real local `Gate: PASS` result in chat without a model request. JSON proof export named `.opaihub/proof-bundle.json`, disclosed redaction/signing, and stopped at one-time approval; Deny wrote nothing. The workflow action copied the valid `opai guard list` command.

### QAR8-11

- Routed the sidebar clear through the same styled inline confirmation primitive used by Settings.
- The confirmation now says it deletes all saved chats and recovery data for the current workspace and cannot be undone.
- The clear bridge is not called until the user selects `Clear history` inside that confirmation; Cancel leaves recents and recovery untouched.
- Red evidence: three browser regressions could not find a confirmation because the first click immediately cleared state; the live app erased the complete sidebar history and blocked-session recovery in one click.
- Green evidence: focused confirmation/cancel/failure coverage passed 3/3; the full Folder, Session Resume, and Permissions & Privacy browser sweep passed 21/21.
- Live retest: after a source-build restart, created `Disposable history safety test` and stopped at the named Gemini confirmation without approving cloud use. Sidebar Clear history displayed the styled warning and exact recovery-data consequence. Cancel kept the recent chat; after closing and relaunching OPai, both the recent entry and `Resume work` recovery card were still present.

### QAR8-12

- Updated both first-run statements to describe the real rule: OPai only uses a cloud model after confirmation, and a cloud model always asks first.
- Red evidence: the new browser contract still found `paid cloud model` and `a paid model always asks first`.
- Green evidence: the focused onboarding Playwright regression passed and Ruff passed.
- Live retest: relaunched a fresh profile from the patched source. Steps 2 and 3 displayed the all-cloud wording; no task or provider call was started.

### QAR8-13

- Added a GUI workspace resolver that preserves a directory carrying an OPai Build manifest while retaining the existing Git-top-level behavior for ordinary nested folders.
- Web and classic desktop launch/switch paths now share that resolver.
- Workspace payloads use the exact selected directory for identity and Build-mode detection while separately reporting the enclosing `repo_root` for Git context.
- Red evidence: the new Python test could not import the resolver, and a nested-scaffold boot contract resolved its workspace root to the parent Git repository.
- Green evidence: 51 focused workspace/web-GUI tests passed, including nested-scaffold selection and Build detection; Ruff passed.
- Live retest: launched the generated quote app directly from patched source. The window title and header named the nested app, onboarding identified that exact directory, its recents were empty instead of restoring the parent's blocked task, and the composer changed to the green `Build` action after finishing the tour.

### QAR8-14

- Threaded explicit `allowCloud` and `allowLimit` values from the Build confirmation resend through the web bridge and `run_build_request()` into the shared pipeline.
- Build retries now retain `build: true`, use `bridge.build`, and preserve the exact reviewed fallback model instead of recomputing Auto or switching to Chat.
- Build confirmation cards reuse the shared actionable error UI under an `OPai Build` header. Persisted free-model consent is also honored by Build.
- Whitelisted safe gate metadata now survives the Build wrapper: exact model ID/label, cloud-started flag, usage gate, receipt, completion verdict, and user-facing recovery fields. Raw provider/tool internals remain excluded.
- Blocked Build threads persist `mode: build` even when the nested agent policy says Explain, so resume reconstructs a Build approval without storing authority.
- Red evidence: the backend rejected an `allow_cloud` argument, the live Build produced a dead-end failure, and the focused browser test could not find the named confirmation. The first live repair then exposed the dropped label and Failed/Blocked disagreement.
- Green evidence: 60 backend Build/resume tests plus 3 subtests passed; all 11 Build-mode browser tests passed; dedicated blocked-Build persistence and resumed-Build browser regressions passed; Ruff passed.
- Live retest: a fresh Build stopped before Gemini with an amber `Blocked` strip, `OPai Build` header, exact `Confirm Gemini · 3.1 Flash-Lite (free tier)` action, no Retry, and no provider call. Closing and relaunching restored the same named action under `OPai Build`; confirmation was deliberately not clicked.

### QAR8-15

- Added native Qt file and folder picker slots and exposed them through the browser bridge.
- Added a Qt-free confinement helper that converts existing selections inside the active workspace to portable relative paths, retains a trailing slash for folders, rejects the workspace root and outside/nonexistent paths, and reports rejection without exposing absolute paths.
- The composer now adds every safe picker result as a context chip and explains when a selection was outside the workspace.
- Red evidence: the Python contract could not import the missing helper and the browser action test received no context chips.
- Green evidence: the confinement helper passed 2/2 focused tests; the native-action browser regression passed; the complete web-GUI Python module passed 46/46; the composer suite passed 15/15; Ruff passed.
- Live retest: relaunched the patched source, opened the real Windows `Attach files` dialog at the exact generated-app directory, selected `index.html`, and observed `@index.html`. Created a disposable `qa-assets` subfolder, selected it through the real `Add a folder` dialog, and observed `@qa-assets/`. No absolute host path appeared in either chip.

### QAR8-16

- Busy transitions now recompute composer availability in both directions, making the relabelled Stop button enabled even though the sent draft was cleared.
- Red evidence: the existing cancellation browser regression failed 3/3 because `#send` was visibly Stop but disabled.
- Green evidence: the focused cancellation regression passed 3/3 after the fix and the complete composer suite passed 15/15.
- Live retest boundary: a real local-first Build reached the safe Gemini confirmation block in roughly two seconds, too quickly for a stable manual Stop click; no cloud confirmation was approved. The deterministic browser bridge holds the request open and verifies both enabled clicking and cancellation dispatch.

### QAR8-17

- `Use this repository` now adds `./`, the active workspace root, instead of synthesizing a child path from the enclosing Git repository's name.
- The row identifies the selected workspace label. Its metadata now has a bounded ellipsis treatment with the complete value retained in DOM text and the title attribute.
- Red evidence: the nested-workspace regression received parent label `sandbox`, parent path semantics, and CSS `text-overflow: clip`.
- Green evidence: the focused nested-workspace test passed after receiving `sandbox/generated-app`, `@./`, and `text-overflow: ellipsis`; the complete composer suite passed 15/15.
- Live retest: the restarted source build's row named `OPai-QA-Sandbox-513/a-tiny-offline-quote-pack-web-app-with-add-favor`, kept the long metadata inside the menu with an ellipsis, and inserted the exact `@./` chip for the active generated app.

### QAR8-18

- The shared model-picker entry point now switches to Chat before deferring the popover open, so both `Ctrl+M` and the command palette work from every view.
- Red evidence: the new Prompt Library regression timed out because `#view-chat` remained hidden.
- Green evidence: the focused browser regression passed after Chat became visible, the model popover opened, and its trigger reported `aria-expanded=true`; the complete composer suite passed 16/16 and the surrounding shell/Prompt Library suites passed 12/12.
- Live retest: relaunched the source against the nested generated app, opened Prompt Library, pressed `Ctrl+M`, and observed an immediate switch to Chat with the complete Auto/model picker visible. No model was selected and no provider call was made.

### QAR8-19

- Added one shared Settings deep-link helper and made the model footer request the `models` page explicitly.
- Red evidence: the extended model-picker regression opened Settings but found `.settings-pane[data-pane="models"]` inactive.
- Green evidence: the focused regression passed with Models & Routing active and its rail item marked `aria-current=page`; the complete composer suite passed 16/16 and the Settings page/routing/search suites passed 20/20.
- Live retest: relaunched the nested generated app, opened and scrolled the populated model picker, selected `Manage models`, and landed directly on Models & Routing with `Default model`, run mode, task focus, output format, and local-first routing visible. No preference was changed and no provider call was made.

### QAR8-20

- Routed the doctor palette command through the shared Settings deep-link helper to `providers`.
- Red evidence: the extended shell regression opened Settings but left the Providers & Connections pane inactive.
- Green evidence: the focused palette regression passed with Providers & Connections active and the `Connection Doctor` region visible; the complete shell suite passed 7/7 and the combined Connection Doctor/settings-connections run passed 21/21. That broader run also exposed and corrected one stale test assertion for the intentionally renamed `Ask before edits` mode label.
- Live retest: relaunched the nested app, ran `Run connection doctor` from the palette, and landed directly on Providers & Connections with the eight-provider doctor, attention count, detected/not-configured states, safe credential-source labels, and local test/sign-in controls visible. No sign-in, provider test, disconnect, or model call was started.

### QAR8-21

- New app cancellation now checks whether any real messages remain. If the thread is empty, it restores the welcome state and rebuilds its starter actions; existing conversations remain untouched.
- Red evidence: the focused New app regression removed the card but found `#empty` hidden until timeout.
- Green evidence: the complete New app suite passed 9/9, including empty validation, deterministic scaffolding, Enter submission, workspace handoff, preview command, failure recovery, cancellation, and command-palette launch.
- Live retest: restarted the nested source app, opened New app, selected Cancel, and confirmed the app card disappeared while the welcome headline and all three starter actions returned. No model call or file scaffold was started.

### QAR8-22

- Benchmark Proof no longer presents hard-coded `50x`, `16`, and `0` values as measured evidence while its hero simultaneously says `Not run yet`.
- Before a local benchmark has completed, all three benchmark KPI cards now use an unavailable placeholder and neutral styling. Completed benchmark data still renders its real measured values.
- Red evidence: the new zero-state contract received `50x`, `16`, and `0` from an unrun repository because the view model used those figures as fallback values.
- Green evidence: the zero-state and premium-view contracts plus the complete benchmark backend suite passed 12/12.
- Live retest: after a source-build restart, the same unrun Benchmark Proof page kept its `Not run yet` hero and rendered an unavailable dash for Context reduction, Paid calls avoided, and Risk blocks. No benchmark or provider call was started.

### QAR8-23

- An unrun workspace no longer dead-ends behind a `Run benchmark gate` button that only says to use the terminal.
- Benchmark Proof now offers `Run local benchmark` and `Check latest gate` as two honest, separate actions. The local run enters the shared one-time approval card before writing privacy-safe evidence under `.opaihub`; it stores no raw prompts and contacts no model provider.
- Money Saved chooses `Run local benchmark` for an unrun workspace and `Check latest benchmark gate` once proof exists.
- Red evidence: the live `Run benchmark gate` action rendered `No run yet. opai benchmark run --suite max --mode both` and performed no run; the new backend contract could not find `benchmark_run`, the premium action contract lacked it, and the browser regression could not find an approval card.
- Green evidence: all 62 desktop GUI and benchmark backend tests passed; all 6 Benchmark browser tests passed, including exact approval scope, no pre-approval write, one-time application, and the existing read-only gate action; Ruff passed.
- Live retest: after a source-build restart, Benchmark Proof showed separate `Run local benchmark` and `Check latest gate` actions. The run disclosed its exact `.opaihub` evidence scope and no-cloud/no-raw-prompt guarantees, then stopped at one-time approval. Approving produced a real offline max-suite result (`100` effectiveness, `50x` reduction, `16` paid calls avoided), refreshed the dashboard to measured proof with `6` risk blocks, and made the independent gate action return PASS. No provider call or spend occurred.

### QAR8-24

- Shared local-action approval cards now transition from `Approved — applying…` to `Approved — applied.` when the asynchronous result arrives.
- Red evidence: the live completed benchmark result appeared below an approval card that remained indefinitely in the applying state; the shared tool-confirmation regression timed out on the same stale copy.
- Green evidence: all 11 combined tool-confirmation and Benchmark browser tests passed, including exact-once application, denial, keyboard operation, local benchmark approval, and final applied-state copy.
- Live retest: after a source-build restart, approved a second offline benchmark run. The card showed `Approved — applying…` only while the worker was active, then changed to `Approved — applied.` at the same moment the completed result appeared.

### QAR8-25

- Agent Readiness now renders Gemini CLI alongside Claude Code, Codex CLI, GitHub Copilot, Cursor, and Cline, matching the declared six-client support and readiness denominator.
- Red evidence: the live page subtitle named all six clients but its complete accessibility tree and scrolled card list contained only five; the updated client-set contract received no `gemini` card.
- Green evidence: 37 focused desktop/client activation tests plus 14 subtests passed; the Agent Readiness capture browser test passed; Ruff passed.
- Live retest: after a full desktop restart on `codex/ultimate-opai-qa`, Agent Readiness rendered all six client cards in order — Claude Code, Codex CLI, GitHub Copilot, Gemini CLI, Cursor, and Cline — with Gemini CLI showing `ACTIVE` alongside the other ready clients. No repair or account action was run during verification.

### QAR8-26 — provider consistency: deterministic refusals, blips, and dead ends

The core complaint on this PR ("sometimes OPai works, other times it doesn't")
was traced to four *deterministic* refusals stacked behind one another, not to
randomness. This item fixes the routing behavior that made them look random.

**1. Deterministic refusals are now remembered (`opaihub/provider_blocks.py`).**
Codex answering `The 'gpt-5.6-terra' model requires a newer version of Codex`
and Copilot refusing repository write access are not transient — they refuse
identically on every turn until the user changes something. OPai now records
them once, with a closed reason vocabulary (`cli_outdated`, `config_invalid`,
`no_scoped_edits`), a scope (`all` vs `edit`), and a TTL:

- Auto no longer spends a fallback step on a provider it has already watched
  refuse; the whole provider is pruned from the remaining chain in one move
  instead of one wasted call per model.
- An `edit`-scoped block only applies to editing turns, so a write-incapable
  provider stays a legitimate Ask/Plan choice.
- Blocks expire, and any successful call clears one immediately, so a CLI
  upgraded outside OPai is rediscovered without user action.
- The store holds only provider ids, closed-vocabulary slugs, and timestamps —
  no prompts, no raw provider text, no secrets.

**2. A transport blip no longer counts as a provider failure.** Gemini's free
tier intermittently returns `provider temporarily unavailable` and the identical
prompt succeeds a second later. Transient codes (`PROVIDER_UNAVAILABLE`,
`PROVIDER_TIMEOUT`, `NETWORK_ERROR`, `STREAM_ABORTED`, `NO_RESPONSE`) now earn
exactly one silent re-attempt on the same provider before the chain advances,
and the blip does not stain the provider's reliability record. This applies
outside Auto too — "I picked Gemini and it randomly failed" is the same bug as
"Auto picked Gemini and it randomly failed".

**3. A failure is never a dead end.** When a turn ends because a model could not
serve it, the engine names one model that still can (`fallback_offer`, cheapest
usable first: on-device → free → paid) and the failure card offers it as the
primary action: **Continue with `<model>`**. Previously the user got a generic
`Switch model` and had to diagnose a routing problem OPai had already solved.

**4. The picker stops offering guaranteed dead ends.** Blocked providers are
grayed with the exact remedy (including the literal update command, e.g.
`npm install -g @openai/codex`) instead of looking selectable and then failing.

Safety boundaries preserved: the offer never carries the previous route's
`allowCloud`/`allowLimit`, so the new model passes its own PR #511 gates; the
offer is suppressed on every awaiting-input card so it can never read as a way
around a safety gate; and it never changes the user's saved default model.

- Green evidence: new `tests/test_provider_blocks.py` 19/19; new
  `fallback-offer.spec.js` 6/6; combined errors-recovery + model-mode +
  chat-core browser suites 31/31; focused Python sweep (pipeline/routing/auto/
  provider/model) 800 passed, 1 skipped, 125 subtests; Ruff clean;
  `git diff --check` clean.

### QAR8-27 — the picker lied on every cold start (root cause of "sometimes")

Investigating QAR8-26 found the mechanism behind the reported Codex and Copilot
symptoms, and it was worse than a routing problem: **the model picker's
availability was wrong on every fresh launch.**

What a provider CLI can do (its version, and whether it can expose a bounded
edit-tool set) was cached only in process memory, and model enumeration
deliberately avoids provider probes. An unknown CLI was therefore resolved
*optimistically* — `_codex_cli_supports_current_default("")` returns `True`. So
whether Codex appeared usable depended entirely on whether some earlier code
path in that same process had happened to probe it. Launch OPai and pick Codex
straight away: it looked available and the run hard-failed with
`The 'gpt-5.6-terra' model requires a newer version of Codex`. Open Settings
first (which probes), and the same model was correctly grayed out. That is
literally "sometimes I can do the tasks, other times I can't".

Fix: CLI capability is a property of the machine, so the verdict is now
persisted next to OPai's other machine-scoped state (`~/.opai/cli_capability.json`),
keyed by the executable's identity (path + size + mtime):

- A cold start reads the last known verdict — honest, and still probe-free.
- Upgrading the CLI changes its identity and invalidates the entry immediately;
  otherwise entries expire after 6h.
- The one-shot probe now runs only when *nothing at all* is known (first launch,
  or right after the binary changed) — exactly when guessing is most wrong.
- Copilot's write-incapability is likewise known before the first run, so the
  picker says so instead of letting the user pick it for an editing task and
  hit the refusal. It stays fully selectable for Ask and Plan.

- Red evidence (live, this machine, cold process): `account:codex` enumerated
  `available=True, repo_editing=True` with Codex CLI 0.128.0 installed against a
  0.143.0 minimum, and all three `account:copilot:*` models enumerated
  `repo_editing=True`.
- Green evidence (same command, same machine): `account:codex` →
  `available=False`, reason `Update Codex CLI to use the current account-default
  model (npm install -g @openai/codex)`; every `account:copilot:*` →
  `repo_editing=False` with the Ask/Plan remedy.
- Regression evidence: new `tests/test_cli_capability_cache.py` 8/8; combined
  account/connection/capability/app_state/desktop-GUI/settings sweep 238 passed
  with 12 subtests; Ruff clean; `git diff --check` clean.

### QAR8-28 — transient failures were classified as unknown dead ends

With the retry machinery from QAR8-26 in place, an audit of
`classify_error_code` found that almost no real-world transient transport
failure actually reached it. Verified by classifying the exact strings these
paths produce:

| provider diagnostic | before | after |
| --- | --- | --- |
| `The read operation timed out` | `UNKNOWN` | `PROVIDER_TIMEOUT` |
| `_ssl.c:1112: The handshake operation timed out` | `UNKNOWN` | `PROVIDER_TIMEOUT` |
| `Remote end closed connection without response` | `UNKNOWN` | `NETWORK_ERROR` |
| `[WinError 10053] An established connection was aborted` | `UNKNOWN` | `NETWORK_ERROR` |
| `[Errno 111] Connection refused` | `UNKNOWN` | `NETWORK_ERROR` |
| `EOF occurred in violation of protocol` | `UNKNOWN` | `NETWORK_ERROR` |
| `The model is overloaded. Please try again later.` | `UNKNOWN` | `PROVIDER_UNAVAILABLE` |
| `HTTP 529: overloaded_error` | `UNKNOWN` | `PROVIDER_UNAVAILABLE` |
| `HTTP 500: internal error` | `UNKNOWN` | `PROVIDER_UNAVAILABLE` |
| `the provider was temporarily unavailable` | `UNKNOWN` | `PROVIDER_UNAVAILABLE` |

`UNKNOWN` renders as *"OPai could not complete this request. Retry, or open
technical details if the problem continues."* — an alarming dead end for what
was a momentary blip. This is the single biggest contributor to "sometimes my
messages work, sometimes they don't": the failure was transient, but nothing in
OPai could tell, so nothing retried and the user saw a hard error. Every code in
the table is in `TRANSIENT_ERROR_CODES`, so with QAR8-26 these are now absorbed
by one silent re-attempt.

Two related misclassifications fixed in the same pass:

- `context deadline exceeded` (how Go/gRPC backends report a plain timeout) was
  classified `CONTEXT_TOO_LARGE`, telling the user to shrink a prompt that was
  never the problem. It is now `PROVIDER_TIMEOUT`.
- HTTP 5xx was matched by the bare substrings `"502"`/`"503"`, so `used 1503
  tokens` read as an outage. Status codes are now matched as whole tokens.

Every deterministic classification is unchanged and covered by an explicit
regression: rate limit, 401, insufficient balance, stale Codex CLI, invalid
config, unknown model, and signed-out all keep their own code and stay
non-transient.

### QAR8-29 — Auto's dead end now names every blocker and its exact fix

Auto exhausting its chain used to say *"Auto has no available model. Choose a
configured model, or connect a free API, account, or local model in Settings."*
— true but useless when OPai already knows precisely which provider is capped,
which CLI is stale, and which cannot take write access. It now lists them:

```
Auto could not use any connected model for this request:
- Codex (OpenAI): Update Codex CLI to use the current account-default model (npm install -g @openai/codex).
- GitHub Copilot cannot be given safe repository write access from this CLI. Use it for Ask or Plan, or update its CLI for scoped tools.
- Groq: Set GROQ_API_KEY env var. Create a free-plan key at console.groq.com
- Mistral: Set MISTRAL_API_KEY env var. Create a free-mode key at console.mistral.ai

Fix any one of these, or pick a different model — OPai only needs one working route.
```

(That block is real output from this machine, not an illustration.) Each
provider reports its own most specific cause, in precedence order: out of
credit → recorded block → not connected → not available (its own
`disabled_reason`) → cannot take write access. Codex therefore reports the CLI
update rather than the write-access sentence, even though the stale CLI sets
both flags.

The same pass also made the Copilot exclusion **proactive**. Previously Auto
learned Copilot could not edit by routing an editing task to it and being
refused; now `repo_editing: False` removes it from editing chains before it is
picked, while leaving it fully available for Ask and Plan. Verified live: an
editing task's chain contains no `account:copilot:*` entry, a read-only task's
chain contains all three.

The model popover carries the same truth: a `repo_editing: false` model now
renders a persistent `Ask & Plan only — can't edit files` caption and keeps the
full reason in its tooltip. It stays enabled, because read-only work through it
is perfectly valid — the row is honest, not restrictive.

- Green evidence: `tests/test_provider_contract.py` 15 tests + new transient
  suite, `tests/test_provider_blocks.py` 30/30, `tests/test_auto_router.py`
  11/11, `tests/test_cli_capability_cache.py` 8/8 — 64 passed with 46 subtests;
  `fallback-offer.spec.js` 8/8; combined composer-setup + model-usage +
  model-mode browser suites 35/35; focused Python sweep 851 passed, 1 skipped,
  129 subtests; Ruff clean; `git diff --check` clean.

### QAR8-30 — a blip mid-run discarded the entire run

The worst version of the transient-failure problem, and the one that matches
"sometimes I can do the tasks, other times I can't" most exactly.

`ToolLoopController.run` is documented as raising `ToolLoopProviderError` "for a
**retryable** transport failure" — but nothing retried it. One failed provider
round-trip returned `RETRYABLE_PROVIDER_ERROR` and ended the run, throwing away
every tool call, every milestone, and every edit already made. On a long coding
task that is twenty minutes of real work lost to a momentary 503, with no way
to resume.

A failed round-trip changes nothing about the loop's state — the request
messages, tool trace, and milestones are exactly as they were — so the turn is
now simply re-issued from where it was. Bounds and boundaries:

- `max_provider_retries: 2` per run, so a genuinely down provider still stops
  honestly instead of spinning.
- The failed round-trip is **not** counted as a model call, because none
  happened — the token/spend accounting stays true.
- `cancel` is re-checked before and after the backoff, so Stop always wins over
  a retry.
- The backoff is injected (`sleep=`), so the suite exercises the policy without
  waiting for it — the tool-loop suite went from 7.4s to 0.5s as a side effect.

- Red evidence: with `max_provider_retries` forced to `0`, the resume
  regression fails with `RETRYABLE_PROVIDER_ERROR is not COMPLETED`.
- Green evidence: `tests/test_tool_loop_controller.py` 37/37 including the new
  resume, bounded-budget, and Stop-wins cases; combined tool-loop / completion /
  agent / capture / activity sweep 464 passed with 96 subtests; Ruff clean;
  `git diff --check` clean.

### QAR8-31 — message laneing: one routing decision, made before anything runs

Implements the central recommendation of the Cursor-consistency research
report: **define a message contract before any model is invoked**, so runtime
behaviour is governed state rather than a side effect of how a request happened
to be worded. The report names the defect precisely — *message-shape variance*:
"semantically similar user messages succeed or fail depending on wording,
length, attached context, tool path, or the model selected".

OPai already classified intent (`agent_policy`), task type
(`model_intelligence.classify_task`), and provider order (`auto_router`) — but
nothing bound them into one decision, and nothing said which runtime *policies*
followed from it. Two phrasings of the same request could take different tool
budgets, different fallback behaviour, and different amounts of context.

`opaihub/message_contract.py` assigns every message exactly one lane, and the
lane fixes the policy:

| lane | when | fallback | blip retries | tool calls | wall clock | context |
| --- | --- | --- | --- | --- | --- | --- |
| `stable` | routine, bounded work | yes | 1 | 12 | 600s | shared |
| `explore` | discovery ("find me an issue") | yes | 1 | 20 | 600s | **isolated** |
| `long_horizon` | multi-file features, architecture | yes | 2 | 40 | 1800s | shared |
| `governed` | destructive, release, credentials | **no** | **0** | 12 | 600s | shared |

Two budgets were deliberately **not** made lane-specific, after review:

- The **governed lane keeps the standard execution allowance**. Its safety comes
  from refusing fallback and requiring confirmation; a tighter budget would only
  strand a legitimate release half-finished, adding a failure mode rather than
  removing one.
- The **compaction threshold stays shared and conservative**. Raising it for
  long tasks is tempting, but the tool loop only clamps it against the
  provider's real context window when `provider_context_chars` is known, and
  OPai does not know that for every local model — a raised threshold would
  silently overflow a small-context model. It stays until OPai can read the
  true window per provider.

The governed lane is the one that matters most for safety. Moving a publish or
a delete to a different provider after a failure is not a recovery — it is a
second attempt at an irreversible action the user approved once, for one route.
That lane refuses provider fallback, refuses the silent transient retry, and
suppresses the QAR8-26 "Continue with…" offer. When a message qualifies for
more than one lane, the **most constrained** one wins: an
"implement the deploy feature and publish it to production" request is
governed, because a wider budget is worthless if it routes around the gate.

What is actually enforced, not merely declared:

- `allow_provider_fallback` gates `_advance_auto` and the dead-end offer.
- `max_transient_retries` replaces the former global constant.
- `max_tool_calls` / `max_active_seconds` are threaded to `ToolLoopPolicy`
  through `app_state.ask` → `run_explicit_model` → the runner, using the same
  additive signature-inspection pattern as the existing one-shot command grant,
  so older and fake runners are unaffected. Previously a multi-file refactor and
  a one-line fix shared one allowance, so the long task quietly stopped at a
  budget sized for the short one.

**Transparency** (the report's separate point that route quality and route
*trust* are different problems): every result carries `message_contract`, and
a governed-lane failure states in the card that OPai will not move the request
to another model on its own, and why. Without that, a deliberately withheld
fallback would look exactly like the dead end the rest of this release removed.

**Flapping evals** (the report's own recommendation — "replay identical cases
multiple times and flag routes whose outcomes oscillate") are implemented in
`tests/test_message_lanes.py`: the same message replayed 10× must yield one
contract, and paraphrase groups must not split across lanes. Since the defect
*is* message-shape variance, a paraphrase that changes lane is itself the bug.

Deliberately **not** implemented from the report, and why: custom embeddings and
the retrieval overhaul (large, and OPai's `semantic_index` is a separate track);
release channels and online A/B telemetry (needs product infrastructure OPai
does not have); real-time RL behind Auto (the report itself flags this as the
part to adopt last, after stable lanes exist — which is what this item builds).

- Red evidence: granting the governed lane the stable lane's permissions
  (`allow_provider_fallback: True`, `max_transient_retries: 1`) fails 5
  regressions across both suites — the pipeline's no-silent-retry and
  no-automatic-reroute cases, and the lane, precedence, and serialization
  cases. Flipping only `allow_provider_fallback` fails 1, which is how the
  two rules were confirmed to be independently enforced rather than one
  check wearing two names.
- Green evidence: `tests/test_message_lanes.py` 17 tests + 20 subtests;
  `tests/test_pipeline_consistency.py` 10/10 including the governed-lane
  contrast; `fallback-offer.spec.js` 10/10; existing
  `tests/test_message_contract.py` (status contract) unchanged and passing;
  Ruff clean; `git diff --check` clean.

### QAR8-32 — a test-only global `time.sleep` mock could exhaust process memory

Found by the full-suite run, not by any individual suite. The QAR8-26 pipeline
tests patched `opaihub.gui_pipeline.time.sleep`, which resolves to the **stdlib
`time` module** and therefore replaced `time.sleep` process-wide. A pre-existing
leaked daemon reader thread in `tests/test_cancellation.py` calls
`time.sleep(0.03)` in an unbounded keep-alive loop; against the mock, every one
of those calls appended to `call_args_list` until the run died of `MemoryError`,
taking down both `test_pipeline_consistency` and the unrelated
`test_gui_overview_cache` timing test.

Fixed by patching `auto_router.TRANSIENT_RETRY_DELAY_SECONDS` to `0.0` instead —
exact, local, and it cannot leak into another test's threads. Verified by
running the three affected suites together (28 passed).

### QAR8-33 — Auto actively routed away from whatever just worked

The research report warns that request-level model switching "risks visible
inconsistency if conversation state, tool permissions, response style, or safety
policy differ sharply between models", and recommends **no silent mid-thread
switching**. Checking OPai against that found the opposite of stickiness — an
active anti-affinity.

`_rank_bucket` ordered equally-healthy providers by least-recently-used, so
"Auto rotates instead of hammering the first entry". But `last_used` is stamped
on **success** as well as failure. So the provider that had just answered
successfully sorted *last*, and the very next turn preferred anything else. Ask
a question and get Gemini; ask a follow-up and get Kimi — different style,
different context, no cause the user could see. That is precisely the
"sometimes my messages work" complaint, produced by the router itself.

Fixed with a bounded stickiness window rather than by deleting rotation, because
both goals are real:

- A provider that answered successfully within `STICKY_SECONDS` (30 min) leads
  the next turn, so a working conversation stays on one provider.
- Outside that window LRU rotation resumes exactly as before, so load still
  spreads across free tiers over a day.
- Stickiness is a tiebreak among *healthy* providers only — it sorts after
  cooldown and reliability penalty, so a provider that succeeds and then fails
  loses preference immediately. It is not a lock.

The pre-existing rotation test asserted the old behaviour directly, so it was
**scoped rather than removed**: `test_rotation_resumes_once_the_conversation_
goes_cold` keeps the original guarantee, alongside a new test for the follow-up
turn and one proving stickiness never outranks a failure.

- Green evidence: `tests/test_auto_router.py` 13/13; full Python suite 2652
  passed, 3 skipped, 602 subtests.

### QAR8-34 — project rules reached account models only

The research report's prompt-contract item, and the largest remaining source of
"the same request behaves differently depending on the model".

OPai's **account** models run through their vendor CLIs (`claude`, `codex`,
`copilot`), and those CLIs read the repository's `AGENTS.md` / `CLAUDE.md`
themselves. OPai's **local and free-tier** models go through `opaihub.ask`,
whose prompt is built from languages, markers, test commands, and git status —
and `context_pack` explicitly excludes the instruction files as "not useful code
context". Confirmed by grep: before this change, `opaihub/ask.py` contained no
reference to either filename.

So a rule the user wrote in `AGENTS.md` — "always run the tests before claiming
done" — was obeyed by Claude and silently ignored by Gemini, with nothing in the
request to explain the difference. Rephrasing cannot fix it, which is exactly
the profile of the reported complaint.

`opaihub/project_instructions.py` loads the project's standing instructions as a
prompt layer and both `ask` paths (the plain completion and the tool loop, which
is where free-tier coding actually runs) now prepend it. Boundaries:

- **Project files only** — `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
  `.opai/rules.md`. A user's global `~/.claude/CLAUDE.md` is personal
  configuration that can describe unrelated work and is never read.
- **Bounded** at 4000 chars, clipped on a line boundary, with no single file
  allowed to starve the others; truncation is labelled `(truncated)` rather
  than hidden, because a silently half-applied rule set is worse than a
  visibly clipped one.
- **Deterministic** — fixed filename order, no globbing, no clock.
- **OPai's rules stay first.** The project directs the work; it does not
  override OPai's safety and honesty rules.
- Unreadable files, directories in place of files, undecodable bytes, and a
  missing root all degrade to "no instructions" rather than failing the turn.

Verified against this repository: 2560 chars of real `AGENTS.md` content now
reach the system prompt (3120 chars total, up from 560).

- Red evidence: reverting the `ask.py` wiring fails
  `test_a_local_run_receives_the_projects_rules`.
- Green evidence: `tests/test_project_instructions.py` 15 tests + 8 subtests.

### QAR8-35 — stale `Safe Auto` assertion in the inspector browser test

The full browser sweep (422 tests) surfaced one failure unrelated to this
session's changes: `inspector.spec.js` still asserted the raw engine name
`Safe Auto`, which QAR8-01 retired from every user-facing surface in favour of
`Ask before edits`. That commit's own notes record updating "one stale test
assertion" — the inspector one was missed. Updated to the canonical label.

- Green evidence: `inspector.spec.js` 6/6; full browser suite 422/422.

### Consistency campaign — final verification (QAR8-26 … QAR8-35)

Run against the complete branch with every change in place:

- **Full Python suite: 2674 passed, 3 skipped, 610 subtests, 0 failures**
  (13:01). For contrast, the same suite took 27:13 and failed twice before
  QAR8-32 — the leaked `time.sleep` mock was both breaking tests and slowing
  the whole run.
- **Full browser suite: 422/422** across every spec.
- Ruff clean; `git diff --check` clean.

New coverage added by this campaign: `test_provider_blocks.py` (30),
`test_cli_capability_cache.py` (8), `test_pipeline_consistency.py` (10),
`test_message_lanes.py` (17 + 20 subtests), `test_project_instructions.py`
(15 + 8 subtests), `fallback-offer.spec.js` (10), plus new cases in
`test_provider_contract.py`, `test_auto_router.py`, and
`test_tool_loop_controller.py`.

Each behavioural fix was red-checked by reverting its mechanism and confirming
the regression fails: the transient retry (`MAX_TRANSIENT_RETRIES = 0`), the
dead-end offer (`_DEAD_END_STATUSES` disabled), the tool-loop resume
(`max_provider_retries = 0`), the governed lane (granted the stable lane's
permissions — 5 failures), and the project-instruction wiring (reverted to the
bare system prompt).

### QAR8-36 — skill catalogue validation (from the repository audit report)

An external audit report was supplied for implementation. Its premises were
checked against the repository before acting, and **most of them are wrong** —
the audit could not fetch the repo or PR and inferred the product from two
cached third-party pages:

| Audit claim | Verified reality |
| --- | --- |
| Repo/PR 404 ⇒ "governance anti-pattern", "restore public access" | The repo is **private** (`gh api … .private = true`). A 404 to anonymous fetch is correct behaviour, not an outage. |
| README / LICENCE / CONTRIBUTING / SECURITY / CODEOWNERS "unverified ⇒ operationally missing" | All present. `README.md` is 25 KB; `LICENSE` is MIT; `.github/CODEOWNERS` exists. |
| "CI/CD unverified ⇒ not trustworthy as a merge gate" | Four workflows exist. Hosted CI is manual **by design** (documented in `ci.yml`: Actions minutes are capped on a private repo); the day-to-day gate is `ci-selfhosted.yml` running `scripts/ci_local.py`. |
| "OPai is a skills repository / workflow library, not an AI IDE" | `hub/skills/` is 36 files totalling 10 KB inside a 10.8 MB product: desktop GUI, 109-module routing engine, CLI, provider adapters, 2674 Python tests, 422 browser tests. |
| "Reposition away from competing with Cursor" | Founded on the above misidentification. Not actioned. |

One recommendation survived scrutiny, and it was correct: **skills had no
validation.** `opaihub validate` gated five registries (tools, agents,
workflows, mcp_servers, models) and skipped skills entirely, and `opaihub skills
doctor` checked only that each file exists. Two failure shapes were unguarded:

- **Silent deactivation.** A host selects a skill from its frontmatter `name`
  and `description`. If `name` drifts from the registry `id`, the skill still
  exists, still passes `skills doctor`, and simply never activates again.
- **Undeclared mutation.** A skill in a state-changing category needs to say
  where it stops. `gitops-pr` did this well ("Never push, merge, delete
  branches, or deploy without explicit confirmation") and `tool-installer` did
  too. Nothing required the next one to.

`opaihub/skill_validation.py` enforces registry↔disk consistency (dangling
entries and unregistered directories), unique kebab-case ids, required registry
fields including OPai's `cost_policy`, frontmatter presence, `name`/`id` match,
description length bounds, body substance, and a stated boundary for the eight
state-changing categories. It joins the existing `opaihub validate` gate rather
than adding a CI step, so it costs nothing against the repo's Actions budget.

Run against the shipped catalogue it found **four real gaps** — skills in
mutating categories with no stated limit:

- `ci-fixer` — now: *"Never push, merge, re-run a deploy job, or edit workflow
  permissions and secrets without explicit confirmation."* Independent
  convergence worth noting: this is the one skill the external ClaudSkills
  catalogue graded **B** and flagged for filesystem/persistence risk, reached
  here from a completely different direction.
- `browser-automation` — now treats live sites as read-only: never sign in,
  submit a form, or complete a purchase without explicit confirmation.
- `refactor-codemod` — now requires a dry-run first and a revertible diff.
- `tool-discovery` — now states it is read-only and installs nothing
  (`tool-installer` owns that), which was already true but unstated.

- Red evidence: stripping the four boundary sentences fails
  `test_the_real_skill_catalogue_is_valid` with all four named.
- Green evidence: `tests/test_skill_validation.py` 21 tests + 13 subtests;
  `opaihub validate` reports `skills count=36 ok=True`; validator/registry sweep
  146 passed with 50 subtests; Ruff clean.

### QAR8-37 — the real CI gate was red on `ruff format`

Running the actual gate (`scripts/ci_local.py`, what `ci-selfhosted.yml`
executes) rather than `ruff check` alone found it failing at step 2 of 4:
`ruff format --check` reported **59 files** needing reformatting, so the gate
never reached unittest, registry validate, or bandit.

Attribution, measured rather than assumed — the reformat list was diffed against
the files this branch touches, and each touched file was re-checked at the
branch point (`abc5246`):

- **43 files** are untouched by this branch — pre-existing formatting debt.
- **8 files** were already unformatted at `abc5246` before this branch edited
  them (`app_state.py`, `accounts.py`, `ask.py`, `auto_router.py`,
  `gui_pipeline.py`, `provider_reliability.py`, and two test modules).
- **8 files** are this branch's own debt: the six modules and tests it created,
  plus `local_runner.py` and `tool_loop.py`, which were clean at the branch
  point and became unformatted through its edits.

Only that last group was reformatted at first, leaving the 43 unrelated files
alone so the pre-existing debt would stay visible as its own piece of work.

**That call was then reversed, on new evidence.** Checking `origin/main`
directly showed **main is itself red on this gate — 50 files**. So the debt is
not something this branch would expose; it is a condition that has been making
`ci-selfhosted.yml` fail on main already. A gate that is permanently red is a
gate everyone learns to ignore, which is worse than the churn of fixing it. The
remaining files were therefore formatted too:

- `ruff format` is deterministic and semantics-preserving, so the change carries
  no behavioural risk — 50 files, +543/−255, entirely whitespace and wrapping.
- It is a single isolated commit, so a reviewer can skip it as "formatting only"
  rather than having it interleaved with logic.
- The gate now goes green for the first time, which makes it usable as a merge
  criterion for every future PR instead of noise.

- Green evidence: `ruff format --check` reports **381 tracked files already
  formatted**, `ruff check` on the tracked tree passes, and the full Python
  suite passes after reformatting.

**One unresolved observation, recorded rather than glossed.** The first
post-format full run hung: output stopped at 9% and the process's CPU time was
byte-identical across a 20-second sample (`UserModeTime` 66562500 twice) with a
flat 110 MB working set, so it was genuinely blocked rather than slow. It was
killed and the suite re-run verbosely; the second run progressed past the same
region and completed. The hang therefore **did not reproduce** and cannot be
attributed to a specific test on this evidence. `ruff format` is AST-preserving,
so the reformatting is not a plausible cause. Noted here because "it passed the
second time" is not the same as "it was fine" — if it recurs, the verbose-run
technique (`PYTHONUNBUFFERED=1 pytest -v` redirected straight to a file, no
pipe) names the blocking test, and `wmic process … get UserModeTime` sampled
twice distinguishes a hang from slowness.

### Issue #295 — waiting is not failing, and typing is never discarded

Work against the P0 consistency epic, guided by its 2026-07-28 product
amendment ("strict runtime, unrestricted conversation").

**1. A run waiting for the user was recorded as terminal.** Every turn that
handed control back — command approval, edit approval, cloud consent, limit
waiver — derived its lifecycle state from the completion verdict and reported
`blocked`. `blocked` is terminal and immutable, yet the user's next click
resumes that same work. Measured before the fix: a free-tier consent turn
returned `status=needs_auto_confirmation, run_state=blocked`. Consequences:
history, receipts and the ledger recorded an ordinary "shall I run this?" as a
run that could not proceed; `is_terminal()` answered True for a run about to
continue; and the GUI store's `canApply()` refused further updates to the very
message the answer would resume.

Added `AWAITING_INPUT` and `CANCEL_REQUESTED` as canonical **non-terminal**
states — the epic's `waiting_user`/`awaiting_approval` and `cancel_requested`.
Membership is deliberately narrow: a status qualifies only if the user's answer
resumes the run by adding one authority. `needs_model` fails that test (Auto
found nothing to call; the remedy is to configure a provider and start again),
so it stays terminal rather than being dressed up as a question. This was found
by a test written before the decision, which failed and forced the distinction.

Transition edges stay conservative. Verification judges evidence that already
exists, so it cannot stop to ask; its one new outbound edge is the
acknowledgement of a Stop pressed while it was running. A requested cancel may
still end `completed`, because pressing Stop as the last step finishes is a
race and calling that cancelled would misstate what happened.

Two existing tests encoded the old spec and were updated rather than deleted:
the non-terminal partition genuinely grew, and `verifying`'s
"only leads to terminal" guarantee was preserved with the cancel edge carved out
explicitly.

**2. The amendment's two layers are enforced.** Layer 2 keeps the state names;
Layer 1 is first-person English — *"I need your OK to run this command."*,
never `awaiting_approval` — with a guard test asserting no rendered string
carries internal vocabulary and every ask reads as OPai speaking. The GUI store
mirrors the same status list under a parity test, so the two cannot drift and
leave one turn "waiting" on one surface and "blocked" on the other.

**3. A message typed during a run was silently discarded.** `app.js` read:
*"Enter sends; while a request is active it is ignored (no duplicate/queue)"* —
the keystroke vanished with no trace, worst exactly when it matters most, for
someone correcting or redirecting work in flight. The epic says plainly: "OPai
must not silently ignore a new instruction because an older run is active."

The text is now held and sent when the run ends. Deliberately a **queue, not a
second concurrent run**: single-flight is what keeps cost and side effects
controllable, and the existing "Enter during generation does not create a
duplicate request" invariant still passes unchanged. The queued message is
visible, editable and removable. It is **not** flushed into an awaiting-input
turn, which would start a fresh run over an approval card the user has not
answered and lose both the question and the work behind it. OPai also never
infers a cancel from the queued prose — guessing at a destructive action next
to a real Stop button is not a feature.

- Red evidence: before the state fix, a consent turn measured
  `run_state=blocked`; before the queue, `Enter` mid-run left `sendCount` at 1
  with the text gone from the DOM entirely.
- Green evidence: `tests/test_awaiting_input_state.py` 24 tests + 47 subtests;
  `test_run_state.py` + `test_run_state_parity.py` updated and passing;
  new `queued-message.spec.js` 7/7; 324 passed across
  state/completion/session/capture/activity; 74 JS unit; design-token lint
  clean; Ruff clean.

**Not attempted, and why:** true concurrent conversational turns during a run
(amendment #14's full form) need a second execution lane and task/run
separation — Workstream A plus #517. The queue removes the silent data loss
without pretending to deliver that. CLI parity for these states (Workstream H,
#525) is untouched: the CLI does not consume `run_state` at all today.

### Issue #380 — Stop claimed the work had stopped before it had

`#380` (a direct child of the #295 epic) opens with: *"Immediate visible
`cancelling` feedback followed by canonical `cancelled` or typed
`failed(cancel_timeout)` within a bounded interval."* Nothing produced that
intermediate state — `CANCEL_REQUESTED` had just been added to the canonical
machine with no producer.

**The defect.** `stop()` did four things in a row:

```js
bridge.cancel(state.currentRequest);
state.currentRequest = null;                 // drop id → late signals ignored
state.message = transition(..., "cancelled"); // terminal: "proven stopped"
finalize("cancelled", ...);
```

But `bridge.cancel` only sets a `threading.Event`; the provider CLI dies
whenever it next notices, and the desktop `Worker` then **swallows** the result
(`if not self._cancelled: self.done.emit(result)`) so the GUI is never told
teardown finished. The UI therefore declared a terminal "cancelled" — which
means *proven stopped* — at the instant the request was made, and dropped the
request id so the truth became unobservable. A paid provider call could still
be running with nothing on screen to say so. That is #295 invariant 9
(acknowledgement, propagation, teardown and final state are one contract) and
invariant 15 (no hidden work).

**The fix, in two halves.**

*Backend:* a new `cancelReady` signal. `cancel()` records the request as
in-teardown and returns; `_confirm_teardown` is wired to `QThread.finished`,
which fires after `run()` returns — the first moment "cancelled" is a fact
rather than a hope. A cancel for a request that is not running answers
`not_running` immediately, so the UI is never left waiting for a confirmation
that will never come. All three cancellable paths (login, send, build) are
hooked.

*Frontend:* `stop()` transitions to `cancel_requested`, shows "Stopping…", and
keeps the request id so the confirmation can be matched. On confirmation the run
becomes `cancelled`. A 10s bound guarantees resolution either way, and an
unconfirmed teardown is reported as such rather than dressed up as a clean stop.

**The safety property that had to survive.** Dropping the request id was also
what blocked stale replies from overwriting the UI. Keeping the id would have
reopened that hole, so the guard moved into `canApply()`, which now refuses
everything while a run is stopping. The regression test was strengthened
accordingly: it now proves a late reply is ignored *both* during teardown and
after it.

**A defect the full suite caught in the first attempt.** Holding the busy state
until teardown was confirmed failed `REGRESSION: Stop never leaves the interface
busy` and `Send changes to Stop immediately` — and those tests were right. Making
the user wait on a teardown they play no part in is exactly the kind of
restriction this work is supposed to remove. `setBusy(false)` now happens the
moment Stop is pressed, so control returns instantly; the honesty lives in the
run state and the status strip, not in a frozen interface.

That change created a second problem worth naming: since the user can start a
new run while the old teardown is pending, a late confirmation could clobber it.
`finishCancel` therefore carries the cancelled request id explicitly and touches
nothing unless the id still matches.

Twelve existing tests encoded the old instant-cancelled contract and were
updated to drive the confirmation — the same treatment the run-state tests got,
and for the same reason: the epic says that contract was wrong. Three of them
(`calm-scenarios`) are stale-guard tests, so they now also prove the guard
survives the two-phase change.

One incidental fix: `test_run_state_parity.py` scraped state names with a naive
`(\w+):` regex that also matched ordinary prose in comments ("evidence that
already exists:"), so a comment could fail the guard. It now strips `//`
comments first.

- Green evidence: new `cancel-teardown.spec.js` 7/7; `activity.spec.js` 15/15;
  `activity-truth` + `status-strip` + cancel suites 29/29; state suites 41 tests
  + 47 subtests; 74 JS unit; Ruff and format clean.

**Not attempted:** process-tree reaping fixtures on Windows/POSIX, and the
`timeout` terminal state with its own budget — both are #380 scope but need the
supervisor/lease work from Workstream A. This slice makes the acknowledgement
honest; it does not yet prove zero orphan processes.

### Issue #295 Workstream A — the engine was running two lifecycles

Phase 0 of the epic is "instrument and freeze semantic drift", and its
transition rule says there must be one machine-enforced lifecycle: *"No adapter,
UI component or provider may invent a parallel vocabulary."*

**Measured drift.** OPai had two engine-side lifecycles:

- `RunState` (`run_state.py`) — canonical per #379, twelve states. Its
  `transition()` had **no production caller at all**; only `run_state_for_verdict`
  was imported. The canonical machine described a lifecycle but enforced nothing
  live.
- `RuntimePhase` (`agent_runtime.py`) — eighteen phases, actually driven by the
  pipeline. And `AgentRuntime._move` already does what Workstream A asks for:
  it validates every move, **raises** on an illegal one, and records a sequenced
  `RuntimeEvent` history. The guard and the history existed — in the parallel
  vocabulary.

**Fourteen of the eighteen phases had no canonical mapping.** Both vocabularies
even contain `blocked`/`failed`/`completed`, so the same word was defined twice
with nothing binding the definitions together.

**The fix** binds them: `_RUNTIME_PHASE_TO_CANONICAL` declares, for every phase,
which canonical state it refines, and `canonical_for_runtime_phase()` raises on
anything unmapped. A refinement is legitimate; an *unmapped* phase is a second
lifecycle in disguise. `tests/test_runtime_phase_parity.py` asserts the mapping
is total, so a new phase cannot be added without deciding what it means.

Deliberate mapping calls: `repairing` refines `RUNNING` (a second attempt at the
work, not a phase of judging evidence); the four delivery phases
(`preparing_pr` … `merged`) refine `RUNNING` because #295 reserves a
`delivering` state that nothing emits yet, and a state with no producer is
decoration.

**The strongest test found a real contradiction — and I was the one who was
wrong.** `test_no_legal_phase_move_implies_an_illegal_canonical_transition`
projects every edge of the engine's graph onto the canonical graph. It failed:
the engine allows `reviewing_diff → implementing/testing` (the repair loop), but
the canonical machine forbade `verifying → running` — a restriction I had
deliberately preserved in the earlier slice because no producer existed for the
reverse edge.

The epic's own lifecycle settles it: `verifying ↔ repairing?`, bidirectional.
The canonical machine was describing a lifecycle the product does not have, so
`VERIFYING → RUNNING` is now legal, and the two earlier assertions that encoded
the stricter rule were updated to state the repair loop explicitly.

That is the whole point of the projection test: without it the "one lifecycle"
claim would have been words, and the engine would have kept legally walking a
path the canonical model forbade.

- Green evidence: `tests/test_runtime_phase_parity.py` 8/8; state suites
  (`run_state`, `run_state_parity`, `awaiting_input_state`) 49 tests + 47
  subtests; state/runtime/pipeline sweep 167 passed with 47 subtests; Ruff and
  format clean.

**Still open in Workstream A:** supervisor leases and duplicate-active-run
prevention; separate task/run/attempt/step identities. Both need the persistence
contract from #517 to be meaningful, and neither is faked here.

### Issue #295 Workstream A — supervisor leases

The gap was already documented, in the resume path's own comment:

> Boot is deliberately read-only. A pending checkpoint can still belong to
> another live OPai window or CLI run; **without an owner lease, process death
> cannot be inferred safely**. Preserve it verbatim and let the user make the
> explicit resume/start-fresh choice.

That is the right call while the information is absent — and it means OPai
could not tell "a sibling window is working on this" from "this died three days
ago", so it had to present both identically. A crashed run stays
`state: "running"` forever, pointing at a dead request id. `"interrupted"` was
already a valid thread state and the resume path already accepted it, but
nothing ever wrote it.

`opaihub/owner_lease.py` supplies the missing evidence. #295 invariant 4: one
active owner, each run holding exactly one supervisor lease.

**Liveness is decided by heartbeat, not by pid.** A bare process id is not
evidence — operating systems reuse them, so a dead owner's id can belong to an
unrelated process minutes later and would read as alive. The owning process
restamps its lease while it works, and a lease silent for longer than
`STALE_AFTER_SECONDS` is stale whatever its pid says; a recycled pid cannot
refresh a lease it does not know about. The pid and a per-process boot id are
still recorded for the two narrower jobs they *can* do honestly: recognising
OPai's own lease so a process never treats its own work as abandoned, and giving
a human something to identify in a diagnostic.

The stale window is deliberately several heartbeats wide (90s against a 10s
beat), asserted by a test: declaring a live run abandoned is far worse than
waiting longer to declare a dead one.

Wiring: `begin_thread_turn` claims the lease at the same moment it records the
turn as running, so there is never a window where a run looks active with nobody
accountable. The heartbeat rides the existing per-request activity-flush tick,
so a long task keeps proving itself alive without a new timer. Only a *running*
turn carries a lease — a finished one has no owner to prove alive. The lease is
whitelisted on both write and read, since this file is parsed on every boot.

**Boot stays read-only.** `_resume_payload` now reports what the lease says and
mutates nothing; the resume/start-fresh decision remains the user's. It simply
stops OPai having to present an abandoned run and an actively-owned one as the
same indistinguishable thing.

**A bug my own test caught.** `describe()` checked ownership before staleness,
so a lease this process had stopped refreshing reported as healthy — a run
nobody was tending, described as fine — because the pid still matched. The
heartbeat is the evidence; ownership only refines the wording. Staleness is now
decided first, with a regression naming the ordering.

- Green evidence: `tests/test_owner_lease.py` 20 tests + 8 subtests;
  recents/resume/thread/session/gui_web/desktop sweep 233 passed with 22
  subtests; Ruff and format clean.

**Still open:** nothing yet *acts* on a stale lease — no automatic transition to
`interrupted`, because choosing what to do with abandoned work (resume, discard,
reconcile external side effects) is the recovery-decision half of Workstream A
and needs #517's replay guarantees to be safe. This slice supplies the evidence
that decision will require; it deliberately does not pre-empt it.

## Session notes

- Campaign branch was created directly from `origin/main` after PR #512 merged.
- The full baseline verification suite will be rerun after the exploratory passes and again before the final PR conclusion.
