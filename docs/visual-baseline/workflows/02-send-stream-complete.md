# Workflow: send → stream → complete

**Recording:** `recordings/02-send-stream-complete.webm`
**Screens:** `screens/execution/EXEC-STREAMING-001-streaming-in-progress.png`, `EXEC-COMPLETE-002-run-completed.png`, `EXEC-TURN-DETAILS-003-turn-summary-expanded.png`

1. The user types "Summarize my changes" and presses **Send**. The prompt
   becomes a right-aligned user bubble; the chat is archived into the sidebar
   immediately.
2. A status strip appears atop the thread: "Connected to Claude ·
   claude-opus · Auto". The Vesta reply card opens with a **Streaming
   response** header, running timer, its own **Stop** button, and a work-log
   disclosure ("Hide work log" with a live "Streaming response · 240 chars ·
   00:01" line). The composer's Send flips to a red **Stop** with an
   "Esc to stop" hint, and the composer header reads "Working".
3. Tokens render live as markdown with a block cursor.
4. On reply, the card settles: the answer text stays, the turn collapses to a
   one-line summary row, and the composer restores **Send**.
5. Clicking the verdict on the turn summary expands the diagnostics
   disclosure (evidence, receipt with the estimated actual cost).
