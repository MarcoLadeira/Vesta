# Vesta QA Bug Report — Cowork remote-control session

**Date:** 2026-07-23
**Build tested:** `0.2.1a1` (alpha.1) — via Settings → About
**Tester persona:** Brand-new user, using Vesta's desktop GUI only (no `vesta` CLI), remote-controlled by Claude in Cowork mode
**Repos touched:** `website/MarcoLadeiraWebsite` (browsed only, not modified), `website/QuotePack` (https://github.com/MarcoLadeira/QuotePack — used for all file/git/PR testing, on branch `feature/define-pricing-architecture-112`)
**Areas covered:** first-run tour, all 9 Settings pages, file creation, git commit, git push, PR creation, delete request, model/provider switching, run-mode switching, adversarial input testing

**Cost impact of this session:** header read **$0.01** at the start and **$0.48** by the end — roughly **$0.47 spent**, the majority of it on repeated retries of a single git-push request that never succeeded (see Bug 1). Worth knowing before re-running this kind of test.

---

## Read this first — the one bug that matters most

**Bug 1 — Tasks the agent refuses or fails are shown to the user as green "✓ Completed."**

This is not a one-off. It happened on 5 separate turns, across 3 different providers (Gemini, Kimi, Claude Sonnet 4.6), for 3 different requested actions (git commit, git push, delete file).

Repro:
1. Open a project with git history, be in **Full Auto** mode (Settings confirms "Run any command: Allow", "Run safe commands: Allow" for this mode).
2. Ask Vesta to do something that mutates the repo, e.g. *"Run git add and git commit for file X."*
3. The model internally decides it is in an "Explain / read-only" capability scope and refuses — it prints the git commands for you to run yourself instead of running them.
4. The status card at the bottom of the same message still shows **"✓ Completed — Objective verified from Vesta-observed evidence."**

Verbatim example from the transcript (Claude Sonnet 4.6, after I explicitly told it "you have full tool permissions... Run any command is set to Allow"):

> "I have inspected the repository and verified that 'vesta-test-notes.md' exists. However, I am restricted to a read-only mode and cannot execute git commands such as 'add' or 'commit' as requested."

Task card directly under that text: **Explain — Completed — Read-only task completed.**

Same pattern for the delete request:

> "This session is in **Explain mode (read-only)** — I'm not authorized to modify files or run mutating commands, so I can't delete it for you."

Task card: **Completed.**

**Why this matters:** a new user (or anyone glancing at the header/status pill instead of reading the paragraph) will believe the action happened. It didn't. The visible run-mode badge (top bar said "Full Auto" the whole time) and the Settings → Permissions & Safety page (which says Full Auto = "Run any command: Allow, runs without asking") both promise something the actual per-turn "capability contract" given to the model does not deliver. The model itself calls out this contradiction unprompted ("even though push is listed as an authorized capability in the task packet").

**Suggested fix:** a turn that refuses the user's actual request should never render a green "Completed" pill. At minimum, "Explain (read-only)" results should get their own neutral/blue status distinct from "Completed," and the top-level run summary shouldn't say "Done" in green when the underlying git state is unchanged.

---

## Bug 2 — `git push` is permanently blocked with no discoverable way to approve it

Repro:
1. On the QuotePack repo, ask Vesta (Claude Sonnet 4.6, Full Auto) to push the current branch.
2. Activity log shows: `Ran command: git push -u origin feature/define-pricing-architecture-112 — failed. Vesta safety gate: this command is classified as destructive or confirmation-only (Requires explicit user confirmation before execution.) It needs explicit user confirmation in the Vesta UI. Do not retry.`
3. **No confirmation dialog, banner, badge, or notification ever appeared anywhere in the app.** I checked: the branch/project selector, the hamburger sidebar toggle, the run-mode dropdown (Ask / Plan only / Ask before edits / Approve edits / Auto-apply), and the top-right icon row. Nothing.
4. I tried typing "I approve the git push, please proceed" directly in chat. Same block, verbatim same error, on a fresh $0.15 model call.
5. Even Full Auto — the single most permissive run mode, which the Permissions & Safety page explicitly lists as allowing "Run any command" without asking — cannot get past this gate.

**Net effect:** the "commit → push → open PR → merge PR" workflow the app advertises (see the onboarding tour and the Settings → Overview quick actions) cannot currently be completed end-to-end through the GUI. I was not able to test PR creation or PR merge at all as a downstream consequence — there was never a pushed branch for GitHub to open a PR from.

This exact wall appears to be pre-existing: the QuotePack chat history already had entries titled *"no i waant you to trigger a pr di..."* and *"try to make a test pr called test..."* from an earlier session, suggesting this is a known, previously-hit, still-unresolved blocker.

**Suggested fix:** either (a) implement the missing confirmation UI (a toast/modal with Approve/Deny for gated commands), or (b) if push is meant to be entirely manual for now, say so plainly in the chat response instead of "needs explicit user confirmation in the Vesta UI" — that phrasing promises a control that doesn't exist yet.

---

## Bug 3 — Verification system gives false "Partial" results on edits that actually succeeded

First request of the session: *"Create vesta-test-notes.md with the text 'Hello from Vesta QA test'."* (routed to `free:gemini:gemini-3.1-flash-lite`)

- Result banner: **⚠ Partial — "Vesta received a response but no changed-file or diff evidence verifies the requested edit."**
- Body text directly below it: **"Created vesta-test-notes.md at the repository root with the required content and committed the change."**
- Ground truth (checked via File Explorer): the file *was* created, at the correct path, with exactly the correct 23-byte content. It was *not* committed (that took several more turns — see Bug 1/2 above).

So the "Partial" banner was itself wrong (the file write fully succeeded), while the model's own summary sentence was *also* wrong in the other direction (claiming a commit that hadn't happened). Two different, contradictory, both-incorrect status signals shown on the same message. A user has no reliable signal to trust here.

---

## Bug 4 — Provider "Last checked" timestamp doesn't update in place

Settings → Providers & Connections → click "Test Claude": the pill correctly flips from "Detected" to "Verified," but the "Last checked" field on the same card keeps reading **"Never checked"** until you navigate away to another settings page and back. Purely a stale re-render; the underlying check clearly ran (the pill updated, the ignored-overrides list appeared).

## Bug 5 — GitHub connection test fails with zero diagnostic information

Providers & Connections → GitHub → "Test connection" → pill goes straight to **"Failed"** (button label also inexplicably lowercases itself to "Test github" after the click — cosmetic but odd). No reason is given: not "token invalid," not "network error," not "rate limited," nothing. There's no logs link, no retry-with-verbose option. Given this connection is required for the entire PR workflow, a silent failure here is a bad first impression and (combined with Bug 2) leaves a new user with no path forward and no idea why.

## Bug 6 — "Connect accounts" button in Settings silently exits Settings

Settings → Providers & Connections → "Connect accounts" button does not open an account-connection flow. It closes Settings entirely, switches the left nav to "Chat," and injects a static informational block into the chat pane (a message about Claude/Codex/Copilot CLI connections that has nothing to do with the GitHub/Kimi/Gemini API-key connections you were just looking at). This is disorienting — clicking a settings action shouldn't silently navigate you out of Settings into an unrelated part of the app.

That same injected block also states **"No API keys, no credentials stored,"** while the Providers & Connections page you just came from explicitly shows, per provider, `Credential: OS credential store` / `Variable: GITHUB_TOKEN` etc. — i.e. credentials clearly *are* stored (in the OS keychain). The two screens contradict each other on a security-relevant claim.

## Bug 7 — Duplicate, redundant provider UI on the same page

Settings → Providers & Connections lists Kimi and Gemini once under "Connection Doctor" (with Test/Disconnect buttons) and then again, lower on the same page, under a separate "Free model connections" section (with a paste-API-key box, Replace/Test/Remove buttons). Same providers, two different control sets, no visible link between them. Confusing for a first-time user trying to figure out where to actually manage a key.

## Bug 8 — No retry affordance on failed runs

When a run shows the red **"Failed"** card, the only guidance is text: *"Next: Retry the run, or switch to another provider."* There's no button — you have to manually retype (or copy/paste) your previous prompt. Small thing, but it adds friction exactly when a new user is already frustrated.

## Bug 9 — Permissions logic gap: file delete requires confirmation, arbitrary commands don't

Settings → Permissions & Safety, Full Auto mode: **"Delete files"** is set to **Ask**, but **"Run any command"** is set to **Allow (runs without asking)**. Since an arbitrary shell command can trivially delete files (`rm`, `del`, etc.), the "Ask before deleting" protection is easy to bypass through the "Allow" command channel sitting right next to it. Not something I triggered a loss from, but worth flagging as a design gap for anyone relying on the delete-confirmation as a safety net.

## Bug 10 — Stray/incorrectly-named file left over from a previous QA session

While checking the current test's output in File Explorer, I found `Documents\website\...\geminii-vesta-test.md` (note the misspelling — "geminii," not "gemini"), 75 bytes, dated over a week before this session. This strongly suggests the file-naming/hallucination issue in Bug 3 is a repeat occurrence with the Gemini route specifically, and that these stray artifacts aren't being cleaned up or surfaced anywhere for the user to notice.

## Bug 11 — Onboarding tour's step 3 runs a real task against whatever project happens to be open

Settings → About → "Replay tour," step 3 ("Earn your first receipt") defaults to the prompt *"Summarize my uncommitted changes"* and a **"Send my first task"** button — and it runs this against whatever project is currently active, not a sandboxed demo repo. When I opened the tour, the active project was the real `website/MarcoLadeiraWebsite` site with 5 real uncommitted changes. A genuinely new user clicking through the tour on their real work folder would kick off a real (paid-model-eligible) task on real uncommitted work before they've had a chance to understand what Vesta does. I skipped this step rather than risk it.

## Minor / cosmetic

- Big empty dead space (roughly a third of the window width) between the left chat sidebar and the Settings content pane whenever Settings is open — looks unfinished.
- The Settings → Providers & Connections "X of 8 connections need attention" counter does not visibly change immediately after you fix (or break) a connection on the same page; you have to trust it's counting something other than what you just did.
- Run-mode dropdown (Ask / Plan only / Ask before edits / Approve edits / Auto-apply) does not close on `Escape`; only clicking outside it dismisses it.
- Cost-limit input fields are well-validated (rejects negative/non-numeric values with a clear inline message) — noted as a **positive**, not a bug, since it's easy to assume the worst going in.
- No light theme yet ("Dark is the only complete theme; a light theme is not shipped yet") — honestly disclosed in-product, but worth listing as a known gap.

---

## What I did *not* get to test

Because Bug 2 blocks `git push` unconditionally, I could not test:
- Opening a real PR on GitHub
- Merging a PR (including conflict handling, squash/merge/rebase options, branch deletion after merge)

Both were explicit parts of the original ask and remain untested. Once Bug 2 is fixed, these should be retested end-to-end on a disposable repo/branch (I used `MarcoLadeira/QuotePack`, branch `feature/define-pricing-architecture-112`, so as not to touch the real website project).

## Suggested fix priority

1. Bug 1 (false "Completed" status) — actively misleading, affects trust in every other status the app shows.
2. Bug 2 (push confirmation UI missing) — blocks the core git workflow entirely.
3. Bug 5 / Bug 6 (GitHub test failure with no diagnosis; Connect accounts navigation+messaging bug) — both sit directly in front of Bug 2 and make it harder to even diagnose.
4. Bug 3 (false Partial) and Bug 4 (stale Last-checked) — trust/polish issues.
5. Everything else — polish, in roughly the order listed above.
