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
| Navigation | Chat, Prompt Library, Insights, Settings, Inspector, sidebar controls | Pending | |
| Composer | Empty prompt, multiline input, context, attachments, send/stop controls | Pending | |
| Run modes | Ask, Plan only, Ask before edits, Approve edits, Auto-apply, Full Auto | Pending | |
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

No Round 8 defects recorded yet. Findings will be added here as they are reproduced.

## Fix and retest log

No Round 8 fixes recorded yet.

## Session notes

- Campaign branch was created directly from `origin/main` after PR #512 merged.
- The full baseline verification suite will be rerun after the exploratory passes and again before the final PR conclusion.
