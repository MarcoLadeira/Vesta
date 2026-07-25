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

## Session notes

- Campaign branch was created directly from `origin/main` after PR #512 merged.
- The full baseline verification suite will be rerun after the exploratory passes and again before the final PR conclusion.
