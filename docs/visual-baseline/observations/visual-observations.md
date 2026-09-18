# Visual observations (evidence only)

Everything below is directly visible in the captured artifacts at commit
`8192bf0`. No redesign proposals — just what the screenshots/recordings show.

## Terminology / labeling

1. **Two vocabularies for run modes.** The run-mode popover
   (`SHELL-MODE-POP-003`) shows user-facing names: **Auto / Manual / Accept
   edits / Plan / Bypass permissions**. The mock boot payload (mirroring
   `gui_preferences.MODES`) uses internal names: Ask / Plan / Approve Edits /
   Safe Auto / Auto-Accept Edits / Full Auto. Approval-card copy deliberately
   uses the user-facing name ("Run any command is blocked in **Auto**" even
   though the active mode is Safe Auto — see
   `PERMISSIONS-CMD-APPROVAL-001`).
2. **"Auto · Auto" duplication.** When model = Auto and mode = Auto, the
   header status line reads "Auto · Auto · $0.42 today · $12.34 saved"
   (`SHELL-LAUNCH-001`), the composer shows two adjacent "Auto" selectors
   (model with a green dot, mode without), and the session inspector repeats
   "Model: Auto / Run mode: Auto". Model and mode are indistinguishable in
   all three places.
3. Sidebar history is labelled **RECENT CHATS** and the active/last
   conversation carries a **"Previous chat"** tag next to its title.

## Hierarchy / layout

4. **Persistent right-hand SESSION inspector** (`EXEC-STREAMING-001`): during
   a run it shows streaming step, Model, Run mode, Workspace, a BUDGET
   progress bar, an ADVANCED divider, TASK FOCUS and OUTPUT FORMAT dropdowns,
   a PERMISSIONS list with color-coded ALLOW/ASK/BLOCK, PRIVACY badges, and a
   CLI MIRROR line. Dense but consistently ordered; model/mode info is the
   third surface repeating it (header, composer, inspector).
5. **Settings is a two-pane layout** with a grouped rail (CONNECT / SPEND &
   SAFETY / SYSTEM), a search box, and scope pills (App-wide / This project /
   Local only) with the workspace path (`/demo`) under them
   (`SETTINGS-OVERVIEW-001`). On the Providers page only "App-wide" and
   "Local only" pills are shown — "This project" is absent there
   (`PROVIDERS-DISCONNECTED-001`).
6. **Scroll position is retained when switching rail pages**: in
   `PROVIDERS-DISCONNECTED-001` the page's intro line ("…never read or
   displayed") is clipped behind the search-field row — the pane did not
   scroll back to top on navigation.
7. **Empty state hierarchy** is clear: mascot → "Better. Faster. Cheaper." →
   three starter chips → composer (`SHELL-LAUNCH-001`). The brand emptyTitle
   differs from the tagline shown elsewhere ("Every step visible. Every
   dollar accounted." is in the boot payload but not visible on screen).

## States / controls

8. **Streaming state** adds a thread-top status bar ("Connected to Claude ·
   claude-opus · Auto" + timer), a message header with **Stop** and a
   "View work log · 2" disclosure, and flips the composer Send into a red
   **Stop** with an "Esc to stop" hint (`EXEC-STREAMING-001`).
9. **Completed turns collapse to a one-line summary** ("Answered · $0.0021 ·
   Details ›") plus an Activity card badged **MEASURED**; all diagnostics live
   behind the disclosure (`EXEC-COMPLETE-002`, `EXEC-TURN-DETAILS-003`).
10. **Stop is two-phase**: Escape alone does not show a terminal card
    (`EXEC-STOP-PENDING-004`); the **Stopped** card ("Generation stopped by
    you." + partial text + Retry / Edit prompt) appears only after the backend
    confirms teardown (`EXEC-STOPPED-005`).
11. **Approval cards are explicit**: COMMAND BLOCKED / ONE-TIME APPROVAL
    badges, reason sentence, exact command in monospace, Approve once
    (accent) / Deny; the turn status chip reads "Failed · Auto" while the
    decision is pending (`PERMISSIONS-CMD-APPROVAL-001`). The edit-approval
    variant lists exact file paths (`PERMISSIONS-EDIT-APPROVAL-003`).
12. **Changeset review** shows a Proposed badge, a "1 risky" warning, an
    aggregate line ("2 files changed +2 −1 · 2 of 2 pending review"), Reject
    all / Approve all, and per-file red/green diff hunks with per-file
    Reject/Approve; the risky file carries a "permissions" badge
    (`CHANGES-CHANGESET-001`, `CHANGES-DIFF-FILE-002`).
13. **Connection Doctor** per-account rows show credential type, "CLI
    installed", "Last checked: Never checked", status text, and Test /
    Sign-in buttons; with all accounts disconnected the page header warns
    "3 of 3 connections need attention" and the sidebar footer switches from
    per-account dots + "Copilot connected" to "No account connected"
    (`PROVIDERS-CONNECTED-001` vs `PROVIDERS-DISCONNECTED-001`).
14. **Model Usage page intentionally shows one provider per state**: Claude
    "unavailable" (rolling 5-hour session window), Gemini live daily quota,
    Kimi prepaid balance, Groq not configured (`SETTINGS-USAGE-001`).
15. **Onboarding** is a 3-step modal over a blurred shell; step 1 ("Connect a
    provider") embeds a live status chip ("3 providers connected") and offers
    Open Providers / Skip tour / Next (`EMPTY-ONBOARDING-001`).

## Capture-environment caveats

16. All captures are the web UI in Chromium with the mock bridge; Qt window
    chrome (min/max/close buttons are actually rendered in-app — visible top
    right in every shot) is part of the web UI, but native OS dialogs are
    not exercised.
17. The starfield background renders as sparse dots behind the thread; motion
    is only visible in the recordings.
