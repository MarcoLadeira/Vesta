# Workflow: stop / cancel

**Recording:** `recordings/03-stop-cancel.webm`
**Screens:** `screens/execution/EXEC-STOP-PENDING-004-stop-requested-unconfirmed.png`, `EXEC-STOPPED-005-stopped-card.png`

1. During streaming, the user presses **Escape** (or clicks either Stop
   control). The run enters a stop-requested state; the partial text stays
   visible while backend confirmation is pending.
2. When the backend confirms the cancel, a terminal **stopped card** renders
   ("Generation stopped by you.") with edit/retry affordances, and the
   composer returns to Send.
