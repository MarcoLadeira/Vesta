# Vesta Desktop — Feature Roadmap

Status legend: **Done** (shipped this redesign) · **Partial** (usable, narrower
than full scope) · **Roadmap** (designed, not built).

| Priority | Feature | Why it matters | MVP scope | Full scope | Status |
| --- | --- | --- | --- | --- | --- |
| P1 | Workspace switcher | The folder was uneditable | Folder picker + recents, persisted | Multi-window, per-workspace settings & history | **Done** |
| P1 | Grouped sidebar nav | 2-button list felt unfinished | Workspace/Dashboard/System groups + active state | Collapsible groups, drag-reorder, pinned | **Done** |
| P1 | Data-backed dashboard pages | Rich engine data was hidden | 7 sections rendered from `build_view_model` | Inline editing of caps/policy from the page | **Done** |
| P1 | Session inspector / control panel | No visibility/control of AI state | Model, mode, focus, format, budget, permissions, privacy | Per-message inspection, history scrubber | **Done** |
| P1 | Task focus + output format | Shape how the AI works | Preface + format instruction on the real prompt | Custom personas, saved presets | **Done** |
| P1 | Real settings page | Was a text dump | Defaults, firewall, permissions, accounts, privacy | Inline editable caps, profile switcher | **Done** |
| P1 | Command palette + shortcuts | Keyboard-first power | Ctrl+K + Ctrl+P/I/O/B and more | Fuzzy actions over all commands incl. prompts | **Done** |
| P1 | Premium typography/quality | Looked low-quality | Inter + high-DPI + antialias | Light theme parity | **Done** |
| P2 | Prompt library | Faster starts | 12 curated prompts, search, categories | User-saved/favourite prompts, folders | **Done** |
| P2 | Tool-permission view | Trust | Mode-derived allow/ask/block | Per-tool overrides within a mode | **Done** |
| P2 | Dashboard actions | Act from the page | panic/repair execute; others copy command | All actions execute with confirm | **Partial** |
| P2 | Workflows page | Repeatable guarded tasks | Surfaces guarded-workflow tiles + copy command | One-click start with approval gates | **Partial** |
| P3 | Multi-model compare | Pick the best answer | — | Split cards, vote/merge, cost/speed compare | **Roadmap** |
| P3 | Live workflow queue | Long-running agent tasks | — | pending/running/needs-approval/done states | **Roadmap** |
| P3 | Editable context chips | Control what AI sees | Inspector shows indexed-file count | Add/remove file & message chips, budget meter | **Roadmap** |
| P3 | Light theme | Preference | Dark only (centralised tokens) | Full light token map + toggle | **Roadmap** |
| P3 | Nav/icon set | Visual polish | Text-only nav | Bundled icon font / SVG set | **Roadmap** |
| P3 | Team + billing surfaces | Monetisation | — | Workspace sharing, signed-license Pro gates | **Roadmap** (see business strategy docs) |

## Engineering notes

- New display logic is **Qt-free and unit-tested** (`gui_nav`, `gui_modes`,
  `gui_permissions`, `gui_prompts`, `gui_workspace`, extended `gui_controls`).
- Dashboard pages render directly from `gui_view_model` sections, so most P3
  data work is "add a section," not "build a page."
- The headless render smoke covers every view, so adding a page keeps CI honest.
