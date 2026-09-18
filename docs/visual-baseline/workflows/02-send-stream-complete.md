# Workflow: send → stream → complete

**Recording:** `recordings/02-send-stream-complete.webm`
**Screens:** `screens/execution/EXEC-STREAMING-001-streaming-in-progress.png`, `EXEC-COMPLETE-002-run-completed.png`, `EXEC-TURN-DETAILS-003-turn-summary-expanded.png`

1. The user types "Summarize my changes" and presses **Send**. The prompt
   appears as a right-aligned user bubble; the new chat is archived into the
   sidebar immediately ("Summarize my ch… · Previous chat").
2. A status bar appears at the top of the thread: "Connected to Claude ·
   claude-opus · Auto". The OPai reply opens with a **Streaming response**
   header, a running timer, a **Stop** button, and a "View work log · 2"
   disclosure. The composer's Send becomes a red **Stop** with an
   "Esc to stop" hint.
3. Tokens render live into the message body with a block cursor. The right-hand
   **SESSION** inspector shows the streaming step, model, run mode, workspace,
   budget bar ($0.42 / $2.00 today), task focus, output format, permissions
   (Read files ALLOW, Edit files ASK, Push ASK, Network/web BLOCK), privacy
   badges, and a CLI mirror of the equivalent command.
4. When the reply completes, the streaming header collapses into a one-line
   turn summary: **"Answered · $0.0021 · Details ›"** with an **Activity (2)**
   card marked **MEASURED** ("OPai · Auto mode · $0.0021 spent · Summary ›").
   Send returns.
5. Clicking the verdict on the turn summary expands the turn diagnostics in
   place (evidence, verification, cost receipt) — see
   `EXEC-TURN-DETAILS-003`.
