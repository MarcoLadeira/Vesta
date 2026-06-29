# OPai Desktop — Product Vision

## Positioning

**A controllable, cost-aware AI coding workspace.** OPai's desktop app is not
"another chat box." It is the place a developer directs, inspects, constrains,
and *pays less for* AI work — with more transparency and control than a standard
AI chat. The wedge is OPai's existing engine: local-first routing, a cost
firewall, honest savings accounting, and signed proof.

## Target users

- **Solo developers / indie founders** who use Claude/Codex daily and feel the
  cost, and want local-first routing without losing capability.
- **Small teams** who need a shared, auditable story of what AI was used, what it
  cost, and what it was allowed to touch.

## What makes it stronger than a basic AI chat

1. **Switchable workspaces.** Point OPai at any project folder and everything —
   models, budget, dashboards — re-scopes to it.
2. **A real control plane.** A session inspector shows the model, run mode, task
   focus, output format, budget, and *exactly what the AI may do* — derived from
   the run mode the engine actually enforces, not a decorative toggle.
3. **Cost truth, not vibes.** The header and dashboards show real spend and
   real, signed savings (`opai receipt`), not invented numbers.
4. **Honest safety.** Read-only modes are read-only; edits ask first; only Full
   Auto edits outright; panic mode blocks paid/cloud — all visible.
5. **Calm by default.** Power lives in a palette, shortcuts, and a toggleable
   inspector, so the everyday surface stays a clean chat.

## Competitive read

| Product | Their strength | What OPai does differently |
| --- | --- | --- |
| Cursor / Claude Code | Deep in-editor agents | OPai is the **cost firewall + control plane** over the agents you already pay for, not a replacement editor |
| ChatGPT / Claude desktop | Polished chat | OPai adds **workspace scoping, spend visibility, tool-permission transparency, and savings proof** |
| Perplexity / v0 / Bolt | Focused generators | OPai is **multi-model, local-first, and private** by default |
| Raycast / Linear | Keyboard-first polish | OPai borrows the palette/shortcut model and applies it to **AI control** |

## Future premium features

- Multi-model side-by-side comparison (cost/speed/quality).
- Live workflow queue for long-running agent tasks (pending/running/approve).
- Editable context: add/remove file chips the AI may see.
- Team workspace + shared savings dashboard + signed proof export.
- Offline signed license tokens gating Pro features (see business strategy docs).

See `FEATURE_ROADMAP.md` for scope and status.
