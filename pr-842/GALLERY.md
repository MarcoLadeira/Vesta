# PR #842 — Agents command center, screenshot evidence

Captured by booting the real web UI (`opai/assets/web/index.html`) through the repository's
own e2e mock bridge and deterministic fixtures, on both sides of the change.

| | |
| --- | --- |
| **BEFORE** | `ab4e26e` — the merge base PR #842 targets |
| **AFTER** | `74a3d49` — PR #842 head |
| Viewport | 1440x900 (1440x1000 for full-page captures) |
| Browser | Chromium (Playwright), `prefers-reduced-motion: reduce`, animations disabled |

This branch holds images and this page only. It contains no code and is not for merge.

---

## Findings

### 1. The "Inspect diff" modal is see-through

![Inspect diff modal rendered over the app](05-inspect-diff-dialog.png)

### 2. Both new mode-picker rows truncate their description

![Mode picker truncation with measurements](06-mode-picker-truncation.png)

---

## Before and after

### Agents view

![Agents view before and after](03-agents-view-before-after.png)

### Header

![Header before and after](01-header-before-after.png)

### Mode picker

![Mode picker before, after, and after with agents enabled](02-mode-picker-before-after.png)

### Mode pill

![Composer mode pill before and after](08-composer-mode-pill.png)

### Assignment and objective states

![Running, blocked, queued, and second-objective states](04-assignment-states.png)

### Live objective card in chat

![Objective card in the chat turn](07-chat-objective-card.png)
