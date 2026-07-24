# OPai QA Retest — Round 4 (session 4 of testing) — 2026-07-24

Fourth pass. Same method as all previous rounds: remote-controlled GUI only, `website/QuotePack`, branch `feature/define-pricing-architecture-112`. Build still `0.2.1a1`. This round set out to independently verify the previous round's claimed successful `git push`, and ended up surfacing a bigger, unrelated problem: **every configured model provider failed during this session, each for a different reason.**

## Headline: on this run, 0 of 5 configured providers could complete a request

I tried, in order, to get OPai to run a simple read-only git check (`git fetch` + compare against `origin/...`). Every provider failed:

| Provider | Result |
| --- | --- |
| Claude Sonnet 4.6 | Failed — real cause (see below) was "You've hit your monthly spend limit" |
| Claude Opus 4.8 | Same — same account, same spend limit, still failed |
| Gemini 3.1 Flash-Lite | First attempt: refused to run git commands at all (stayed in read-only "Explain" mode). Second attempt: "The Gemini free-tier API returned no answer... check GOOGLE_API_KEY... quota" |
| Copilot · GPT-5.2 | `Error: Model "gpt-5.2" from --model flag is not available.` — this model is listed as selectable in the picker but the underlying CLI rejects it outright |
| Codex (Account default) | Same version-mismatch error as Round 3: `The 'gpt-5.6-terra' model requires a newer version of Codex` — still not fixed |
| Kimi (Moonshot) | Not even attempted — picker itself shows it "hidden — out of credit" before you can select it |

This is a real, if partly circumstantial, finding (accumulated spend across four rounds of testing is a big part of why Claude hit its cap), but it's still worth flagging hard: **a brand-new user who burns through free-tier quota and hits a Claude spend cap in their first real session has no working fallback provider at all**, because the two "free" options (Gemini, Kimi) are both already exhausted and the two paid CLI fallbacks (Copilot GPT-5.2, Codex) are each broken by a configuration/version bug unrelated to cost. That's a bad first-session experience if it happens to land the same way for a real user.

## New Bug 12: the real failure reason is hidden by default, and the visible message is useless

For the Claude failures, the top-level UI showed only:
- `Streaming interrupted — 100 chars`
- `OPai could not complete this request.`
- `Failed — The provider failed before OPai could verify the objective.`

None of that tells you what actually went wrong. The real reason — `You've hit your monthly spend limit · raise it at claude.ai/settings/usage?from=cc_cli_limit_message` — was sitting the whole time behind a collapsed **"Show details"** disclosure that isn't expanded by default and isn't hinted at by the visible text. I only found it because I went looking. A first-time user hitting this would see three vague, alarming-sounding lines and have no idea the actual fix is "wait for reset or raise your limit at this URL."

By contrast, when Gemini failed on the free tier, the reason (missing/exhausted `GOOGLE_API_KEY` quota) appeared directly in the visible response text, no click required. So OPai's error-detail surfacing is inconsistent across providers — good for Gemini, bad for Claude — not just uniformly bad.

## New Bug 13: "Retry" silently ignores a newly-selected model

Repro:
1. Get a failed run (any reason).
2. Click "Switch model" (or the model dropdown) and pick a different provider — I picked Claude Opus 4.8 after a Claude Sonnet 4.6 failure, confirmed with a visible checkmark next to Opus in the list.
3. Click **Retry** on the failed card.
4. The activity log shows it reran with `account:claude:sonnet` again — the *original* failed model — not Opus.

I confirmed this twice. The only way I found to actually change providers was to abandon "Retry" and type a brand-new message after picking the new model from the composer's own model dropdown — that path did respect the new selection (confirmed: Gemini run showed `Model: free:gemini:gemini-3.1-flash-lite` after switching that way). So "Retry" and "Switch model" look like they're supposed to work together but the button doesn't actually pass the new model through.

Related smaller thing: clicking the **"Switch model"** button inside a failed-run card (as opposed to the separate model dropdown next to Send) doesn't visibly do anything — no dropdown opens, nothing changes on screen. If it's supposed to open the same picker, it didn't for me.

## Bug 1 (false status on refused git tasks) — still present, and now inconsistent both ways

Asked Gemini twice, back to back, to run git commands with explicit "you have Full Auto permissions" framing. Both times it gave essentially the same explanation ("I do not have the capability to execute git commands directly... my authorized capabilities are limited to inspect_git/read_files/search_code"). But the two turns got **different** status labels:
- First turn: **✓ Completed** — "Objective verified from OPai-observed evidence." (task table: "Explain — Completed — Read-only task completed")
- Second, near-identical turn: **⚠ Failed** — "Stopped without finishing" / "The provider failed before OPai could verify the objective."

So the same underlying situation (model refuses to run the mutating command, does read-only inspection instead) produced two different top-level verdicts back to back. Neither "Completed" nor "Failed" is quite right — "Completed" is the more actively misleading one, since nothing the user asked for happened — but the fact that it's not even consistent is new information: whatever classifies these turns isn't deterministic for what looks like the same case.

## Confirmed still broken from Round 3

- **Codex CLI version mismatch** — identical error string to last round, unfixed.
- **Top-bar "N uncommitted" badge is still stale** — read "2 uncommitted" throughout this entire session, including during turns where nothing in the working tree changed and where the only real question was the state of already-committed history. Never seen it update live in four rounds now.
- **"Enable pushes & PRs" Settings toggle** — did not re-check this specific item this round (ran out of working providers before getting back to a push attempt), but nothing in Settings has changed layout since Round 3's exhaustive check, so I'd expect it's still absent.

## What I could not verify this round, and why

The previous round ended with OPai (Claude Sonnet 4.6) reporting a successful push: *"Pushed successfully — feature/define-pricing-architecture-112 is now up to date on origin (df76d22..530766d)."* That claim is **still unverified**. My plan was to check it two independent ways:

1. **Via GitHub.com directly**, using the Claude-in-Chrome browser tool. This tool's safety classifier was unavailable for my entire session ("claude-sonnet-5[1m] is temporarily unavailable, so auto mode cannot determine the safety of [the tool] right now") — I retried it roughly six times over ~20 minutes with no change. This is an outage in my own tooling, not something in OPai, but it means I have no independent read of GitHub for this round.
2. **Via raw git output requested through OPai itself** (`git fetch` + compare against `origin/...`), which — as described above — failed on every single provider I tried.

So the honest state is: I don't know whether that push actually landed. It may well have — the underlying claim was specific and plausible, and Round 3 already showed OPai capable of a real, verified commit — but I have no independent confirmation, and given how much this session's error messages turned out to not mean what they said, I'm not willing to take the "Pushed successfully" line at face value without one of the two checks above. **This should be the first thing retested next session**, ideally early, before quota/spend exhaustion makes it hard again.

## Bottom line

This round didn't get to retest the push/PR/merge workflow at all — every attempt to even ask a read-only git question burned through the entire available provider list. The most useful output of this round isn't "is push fixed" (still unknown) but three fresh, concrete bugs: error messages that hide the one actionable detail behind an extra click (and only for some providers), a Retry button that doesn't respect model switching, and a status-label system that gave two different verdicts for the same kind of refusal in the same session. I'd suggest fixing the Retry/model-switch disconnect first, since it directly blocked productive retesting today — a user hitting a paid-model cap has no way to actually fall back to a free model without abandoning the conversation thread and starting fresh.
