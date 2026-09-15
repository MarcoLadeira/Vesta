# QA Round 7 — live remote-control adversarial retest

**Date:** 2026-07-25  
**Target:** PR #511 (`fix/qa-round2-push-consent-and-status-honesty`)  
**Session:** Vesta GUI, Full Auto, Claude Sonnet 4.6; live QuotePack throwaway-repository context.

## Reproduced remaining defects

### QAR7-01 — `gh pr comment` is incorrectly treated as destructive

**Severity:** High — it prevents the requested GitHub update from being completed through Vesta.

**Steps:**

1. Ask Vesta to post the supplied QA Round 6 retest comment using `gh pr comment 511 --repo MarcoLadeira/Vesta --body-file ...`.
2. Repeat with the comment body first saved to a local markdown file, so the command itself contains no quoted `git push` text.
3. Explicitly authorize the command in a follow-up request.

**Observed:**

- The first form was classified as a force/delete/mirror push because the *quoted comment text* mentioned `git push origin ...`.
- The file-backed form was then classified as "destructive or confirmation-only" and blocked in the autonomous run.
- The explicit authorization produced a red terminal “Blocked as risky” result rather than a one-time approval card for the exact `gh pr comment` command.

**Expected:**

Posting a pull-request comment must not be parsed as a git push merely because its body contains those words. It should be either allowed by the relevant policy or presented as a precise one-time approval; it must not be terminally blocked as destructive.

### QAR7-02 — an unperformed remote objective can be marked completed from unrelated local-file evidence

**Severity:** High — the UI gives a false assurance that an externally visible action happened.

**Steps:**

1. Ask Vesta to post a comment to PR #511.
2. Observe the safety gate block every `gh pr comment` invocation.
3. The agent writes `.pr511-comment.md` locally after the block.

**Observed:**

The workflow subsequently displayed **Completed — Objective verified from Vesta-observed evidence**, even though no GitHub comment command succeeded and the only new artifact was a local markdown file. The workflow history also showed an `Implement` / `Reviewing Diff` completion state for this unfulfilled GitHub-comment objective.

**Expected:**

For an objective whose success condition is an external GitHub mutation, local file creation is not completion evidence. The terminal verdict, status pill, and bottom summary must remain blocked/partial and explain that the comment was not posted until Vesta receives a successful command result or independently verifies the remote comment.

## Controls observed

- The previous confirmed push workflow still shows the intended one-time approval pattern for an actual `git push`.
- The failure above is therefore a classification/evidence-attribution regression, not a request to bypass push safety.

## Notes on scope

This report records actions observed in the live remote-control session. I did not launch additional paid model turns solely to generate more git mutations; the existing session already provided deterministic reproductions of both failures above.
