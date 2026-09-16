# Vesta QA Retest — 2026-07-24

Retest of [`QA_BUG_REPORT_2026-07-23.md`](./QA_BUG_REPORT_2026-07-23.md) after Claude's fixes. Same method: remote-controlled GUI only, same repo (`website/QuotePack`, branch `feature/define-pricing-architecture-112`), fresh chat sessions. Build still reports `0.2.1a1` in Settings → About (version number wasn't bumped, so I verified behavior directly rather than trusting the version string).

## Fixed

- **Bug 4 — stale "Last checked" timestamp.** Now updates instantly when you click Test. Confirmed on Claude and GitHub.
- **Bug 5 — GitHub test failure with no diagnostics.** GitHub now shows **Verified** with an actual reason: *"Token valid — signed in as MarcoLadeira (source: keychain)."* This was "Failed" with zero explanation before.
- **Bug 6 — "Connect accounts" navigation/messaging bug.** The button is now labeled **"Connect CLI accounts..."** with explicit scoping text: *"Opens a guided sign-in in Chat for CLI accounts (Claude, Codex, Copilot). API-key providers are managed above."* No longer conflates CLI accounts with the API-key providers, and the contradiction with the credential-storage messaging is gone.
- **Bug 7 — duplicate provider UI.** The redundant second "Free model connections" section (with its own paste-a-key boxes for Kimi/Gemini) is gone. Each provider now appears once.
- **Bug 8 — no retry button on failures.** Failed run cards now have an actual **Retry** button, not just text telling you to retype your prompt.
- **Bug 9 — delete-files vs. run-any-command gap.** "Run any command" now explicitly says *"Runs without asking; commands that delete files still follow Delete files."* The gap is closed and, importantly, now documented in the UI itself.
- **Cosmetic — dead space in Settings.** The empty gap between the sidebar and Settings content is gone; layout fills properly now.
- **Cosmetic — "Test GitHub" lowercasing itself after click.** Stayed correctly capitalized this time.

## Improved but not fully fixed

- **Bug 1 — false "Completed" status on refused/incomplete tasks.** The model's own written responses are now honest and specific — e.g. *"The file was created successfully, but the git add/git commit command was blocked by Vesta's safety gate... I won't retry or work around it,"* followed by an explicit status list (✅ file created / 🚫 commit not done). That's a real, meaningful improvement over the old silent "here are the read-only-mode reasons" refusals. There's also a new **"N file(s) changed" diff panel** with `Open folder` and per-file +/− line counts, which gives you actual evidence instead of a vague "no diff evidence" warning.

  However: the colored status pill at the top of the same message can still say **"✓ Completed"** on a turn where the commit was explicitly blocked and never happened. The pill needs to reflect "your request partially succeeded / the mutating step was blocked," not a flat green Completed, since a user scanning just the pill (not the paragraph) will still be misled.

- **Bug 2 — git push/commit blocked with no way to approve.** This is the big one, and it's half-fixed:
  - The block message is now honest: *"Vesta safety gate: this command is classified as destructive or confirmation-only... It is blocked in autonomous runs and Vesta will not run it for you."* This is a big improvement over the old message, which claimed a confirmation dialog existed "in the Vesta UI" when it didn't.
  - A real **"Review changes" panel with Approve / Reject buttons** now appears under file-changing turns, showing the pending file, a diff, and a status line ("1 files · 1 pending · Tests: not verified"). This is clearly the intended fix for the missing-confirmation problem, and it's a good design.
  - **But clicking Approve does not actually run the commit.** I clicked Approve, then asked Vesta to run `git status` / `git log -1` directly to check — it confirmed the file was still **untracked**, i.e. nothing was committed. The button doesn't error, doesn't say "not implemented yet," it just silently does nothing.
  - Net result: the git commit → push → PR → merge workflow is still not completable end-to-end through the GUI. I could not test PR creation or merging again this session for the same reason as before — there's still no way to get a real commit onto the branch through the app itself.
  - Settings → Permissions & Safety still has no dedicated toggle for "allow git mutations autonomously" — I searched Settings for "git" and found only the pre-existing "Run safe commands (git status/diff/log)" allowance, nothing new for add/commit/push.

## Not retested this session

- **Bug 3 (false "Partial" on a fully successful edit)** didn't reproduce in its original form — I couldn't get a clean repeat of "edit succeeds, banner says Partial" because the new diff-evidence panel changes the whole verification path. Given the new evidence panel exists, I'd consider the root cause addressed, but it's worth a dedicated retest once push is fixed and full multi-step turns (create + commit + push) can be exercised end-to-end.
- **Bug 10 (stray misnamed file from a Gemini-routed run)** did not recur — both retest files (`vesta-retest-notes.md`, `vesta-retest-verify.md`) were created with exactly the right names. Encouraging, but one clean run isn't proof it's fixed.
- **Bug 11 (onboarding tour can run a real task against your real, non-sandboxed project)** — didn't re-open the tour this session to avoid repeating the same risk against real project data. Worth a deliberate, careful retest rather than an incidental one.
- **"X of 8 connections need attention" counter not live-updating** — didn't specifically re-check this one; lower priority.

## Bottom line

Good progress, and the direction on Bug 2 (the Approve/Reject panel) is the right one — it just isn't wired up to actually execute yet. Once Approve actually performs the git operation, that should also resolve the "false Completed" pill issue for these turns (since the turn would only complete after a real, verified commit). I'd treat Bug 2 as still the top blocker: everything downstream (PR creation, merging) is still untestable until Approve does something.
