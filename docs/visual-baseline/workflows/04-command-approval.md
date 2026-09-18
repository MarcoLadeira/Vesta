# Workflow: one-time command approval

**Recording:** `recordings/05-command-approval.webm`
**Screens:** `screens/permissions/PERMISSIONS-CMD-APPROVAL-001-command-approval-card.png`, `PERMISSIONS-CMD-APPROVED-002-command-approved.png`

1. With the run mode set to **Auto** (Safe Auto), the user sends
   "Fetch issue 219 for me". The pipeline hard-blocks the shell command.
2. A **COMMAND BLOCKED** card appears in the thread, badged
   **ONE-TIME APPROVAL**: "Approve this command once? Run any command is
   blocked in Auto." The exact command is shown verbatim in monospace:
   `gh issue view 219 --repo MarcoLadeira/OPai`. Two actions:
   **Approve once** and **Deny**.
3. Note the terminology: the blocked reason uses the *user-facing* mode name
   ("Auto"), never the internal one ("Safe Auto").
4. Clicking **Approve once** re-sends the original message with that exact
   command allowed; the card state flips to "Approved — re-running with this
   command allowed…", both buttons disable, and the normal answer lands below.
5. (The Deny path, exercised in the e2e suite, posts a cancellation and shows
   "Denied — the command was not run." — not recorded here.)

The edit-approval variant (`PERMISSIONS-EDIT-APPROVAL-003`) looks the same but
lists the exact file paths and offers **Allow edits once** / Deny.
