# Workflow: stop / cancel a run

**Recording:** `recordings/03-stop-cancel.webm`
**Screens:** `screens/execution/EXEC-STOP-PENDING-004-stop-requested-unconfirmed.png`, `EXEC-STOPPED-005-stopped-card.png`

1. Mid-stream, the user presses **Esc** (or clicks the red **Stop** button in
   the composer).
2. The UI enters an intermediate state: generation halts visually but the
   terminal card is not shown yet — the front-end waits for the backend to
   confirm teardown (cancel is only *confirmed* via the `cancelReady` signal).
3. Once teardown is confirmed, a **Stopped** card replaces the in-flight
   message: "Generation stopped by you." followed by the partial text that had
   streamed, the note "You can edit the prompt, retry, or switch model.", and
   two actions: **Retry** and **Edit prompt**.
4. The header status chip for the turn reads **Stopped**. Choosing
   **Edit prompt** restores the original text into the composer without
   re-sending (verified by the e2e suite; the stopped card itself is what the
   recording shows).
