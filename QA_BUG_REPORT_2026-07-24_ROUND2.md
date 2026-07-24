# OPai QA Retest — Round 2 — 2026-07-24

Third pass, after another round of fixes. Same method and repo as before (`website/QuotePack`, branch `feature/define-pricing-architecture-112`), fresh chat, GUI only. Build still reports `0.2.1a1` in Settings → About.

## The headline: commits now actually work

This is real progress. I asked OPai to stage and commit the two leftover test files from the last session. The run showed the usual **"Partial — no changed-file or diff evidence"** warning, which by now I don't trust at face value — so I asked a follow-up turn to print the *raw, verbatim* output of `git status --porcelain` and `git log -1 --oneline`. It came back:

```
?? .opaihub/
530766d chore: add opai qa retest notes
```

That's a real commit, with the right message, actually in the repo's history. So despite the scary "Partial" banner, **the commit genuinely happened.** This is the first time in three rounds of testing that a git-mutating action has gone all the way through successfully.

## But: push is still completely blocked, and now for a new, more specific wrong reason

I asked OPai to push the branch. It refused, and gave very confident, specific instructions:

> "The push is blocked by OPai's safety gate — it explicitly refuses raw `git push` from an autonomous run, even with GitHub verified. It wants one of these instead: **1. Enable OPai push/PR support: In Settings → Providers & Connections, click "Enable pushes & PRs" (separate from just verifying the GitHub connection).** ... 2. You push manually in a terminal yourself."

I went and looked. **There is no "Enable pushes & PRs" button or toggle anywhere in Settings.** I checked Providers & Connections (both "App-wide" and "This project" scope), Cost Firewall, Permissions & Safety, and searched Settings for "push" and "git" — nothing. I even verified the GitHub connection first (it now shows "Verified — Token valid, signed in as MarcoLadeira") and asked OPai to retry the push specifically because I'd done that — it ran the push again, failed the same way, and repeated the exact same instruction to click a button that doesn't exist.

This is the same underlying issue as the last two rounds (git push has no way to be approved), but the specific claim about *where* to fix it has gotten more detailed and more wrong at the same time. A real user following these directions would spend real time hunting through Settings for a button that was never built. I'd treat this as higher priority to fix than before, precisely because the instructions read as trustworthy.

**Net result:** still could not test PR creation or merging, for the same downstream reason as rounds 1 and 2 — no way to get a pushed branch onto GitHub through the app.

## Bug 1 (false/misleading status) — still not resolved, in a new form

The "Explain mode" refusal pattern (I asked to verify git state, it ran the check but replied "noted that git log execution is outside of tool capabilities") still gets stamped **"Completed."** Separately, an actually-successful commit got a **"Partial"** banner. So the status labels are still not reliable in either direction — sometimes too positive, sometimes too negative — you have to read the paragraph and, ideally, ask for raw command output to know what really happened. I don't think a first-time user would think to do that.

## New: a real "needs confirmation" flow exists — just not wired to push yet

Good sign — I hit a **"Blocked" / "Auto needs your confirmation"** card when OPai wanted to fall through to a paid model (Codex) after the free options failed. It had real, working buttons: **Retry**, **Confirm Codex · Account default**, **Open Settings**, **Switch model**. I clicked Confirm and it proceeded correctly (aside from Codex itself erroring — see below). So the "ask for real confirmation" UI pattern clearly exists and works for at least one case (cost/paid-model escalation). It just isn't hooked up to the git-push safety gate yet, which is the one place it's actually needed most.

## New, unrelated finding: Codex CLI version mismatch

When OPai routed a request to Codex, it failed with: `Codex reported: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'gpt-5.6-terra' model requires a newer version of Codex. Please upgrade to the latest one or CLI and try again."}}`. Not part of the original report, but worth flagging — Codex is essentially unusable as a fallback provider right now on this machine.

## Confirmed still working from Round 1 fixes

Spot-checked and still holding: GitHub connection test gives a real "Verified / Token valid, signed in as MarcoLadeira" result; Last-checked timestamps update instantly; the Retry button on failed runs works (used it twice this session, both times it correctly re-ran); "Free model API keys" section is now clearly differentiated from Connection Doctor rather than a confusing duplicate.

## New minor finding: top-bar "N uncommitted" badge is stale

The header next to the branch name read **"2 uncommitted"** the entire session — including after the commit `530766d` landed, when actual `git status --porcelain` showed only `.opaihub/` untracked (an internal config dir, not my files). The badge did not update to reflect the real count. Given how much I (and presumably you) rely on that badge to sanity-check state at a glance, this is worth fixing — right now it can't be trusted.

## Bottom line

The core capability gap has narrowed: **commit works now**, which is genuine, verified progress. **Push is still the hard blocker**, and the fix path OPai now describes to get around it doesn't exist in the UI, which is a regression in a different way — it went from "there's no way to approve this" to "here's a specific place to approve this that isn't real." I'd prioritize: (1) either build the "Enable pushes & PRs" toggle the model keeps describing, or make it stop describing it, and (2) reuse the confirmation-card pattern that already works for the paid-model case and wire it to the git push gate.
