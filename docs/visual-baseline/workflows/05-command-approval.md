# Workflow: command approval

**Recording:** `recordings/05-command-approval.webm`
**Screens:** `screens/permissions/PERMISSIONS-CMD-APPROVAL-001-command-approval-card.png`, `PERMISSIONS-CMD-APPROVED-002-command-approved.png`

1. With the run mode in Safe Auto, the user sends "Fetch issue 219 for me".
   The mock backend answers `needs_command_approval`.
2. A **COMMAND BLOCKED** approval card renders: "Approve this command once?",
   the policy reason, and the exact command verbatim
   (`gh issue view 219 --repo MarcoLadeira/OPai`), with **Approve once** and
   **Deny** buttons. The top status strip shows "Failed · Auto" while the
   card awaits the decision (see observations #7).
3. Clicking **Approve once** re-runs the request; the card flips to its
   approved state and the answer message lands in the thread.

The edit-approval variant (`PERMISSIONS-EDIT-APPROVAL-003`) lists the exact
file paths to be modified with Allow-edits-once / Deny.
