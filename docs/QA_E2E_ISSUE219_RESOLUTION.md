# QA E2E Issue #219 — Resolution Map

Companion to `docs/QA_E2E_ISSUE219_2026-07-17.md` (23 findings, F2–F24, from
driving the real desktop GUI with "Solve GitHub issue #219 in this repo for
me."). Each finding below maps to its fix (file + mechanism) and its
regression tests. All fixes land together on branch `fix/qa-e2e-issue219`;
tests are hermetic and run with `python -m pytest tests/ -x -q`.

Legend: ✅ fixed in this bundle · 📄 documented (no engine change needed)

## Critical

| Finding | Fix | Regression tests |
| --- | --- | --- |
| **F22** — Fabricated completion + closed the real issue | ✅ Completion honesty gate in `vestahub/completion.py`: a run may only report "completed" with evidence (diff, test result, or PR artifact); plus the F23 mutation gate below blocks the close itself | `tests/test_completion_contract.py` |
| **F23** — Destructive external action with no confirmation | ✅ `vestahub/safety_gates.is_destructive_command` classifies `gh` mutations (`issue close/comment/edit`, `pr …`, `release`, …) and `git push` as destructive → confirmation required even in Full Auto; `vestahub/sandbox.classify_command` + both shipped `hub/security/risky_commands.yaml` copies carry the same rules and fail CLOSED to `confirm` when the policy store is unloadable (`git commit` stays non-destructive: local and undoable) | `tests/test_gh_mutation_classification.py` |
| **F24** — False "successfully solved" claim | ✅ Same completion-evidence gate as F22: zero diffs + zero tests can never render as success; task cards stay in an honest non-complete state | `tests/test_completion_contract.py` |
| **F16** — Composer run-mode desync after workspace switch | ✅ Composer run-mode is re-synced from the real engine mode on workspace switch (`vesta/gui_controls.py`, `vesta/gui_desktop.py`); the Pin-Full-Auto acknowledgement fires whenever the effective mode would change, even if the dropdown already shows the target | GUI control tests (`tests/test_gui_controls.py`, `tests/test_desktop_gui.py`) |
| **F18** — Task focus stuck on Explain forces read-only | ✅ Solve/fix/implement intent maps to an edit-capable focus (F5/F10 fix); focus no longer silently downgrades a build request | `tests/test_gui_modes.py` |
| **F19** — Command output not returned / wrong command chosen | ✅ `vestahub/github_connector.py` read-path honesty: `issue_view` always uses `gh issue view N --json title,body,comments,labels`; empty stdout / malformed JSON is surfaced as an error, never a green "✓ Ran"; legitimately silent mutations stay silent-tolerant | `tests/test_github_adapter_reads.py` |
| **F6** — Free Gemini path never executes tools | ✅ Free-model tool execution wired through `vestahub/tool_loop.py` / `vestahub/free_models.py` path: free API runs use the real tool loop instead of narrating tool calls as prose | `tests/test_free_models.py` (tool-loop cases), `tests/test_reliable_ai_controls.py` |

## High

| Finding | Fix | Regression tests |
| --- | --- | --- |
| **F5 / F10** — "Solve/fix" classified as read-only Explain | ✅ Intent mapping in `vesta/gui_modes.py` / intent routing: solve/fix/implement verbs select an edit-capable task focus (Build/Debug) | `tests/test_gui_modes.py` |
| **F9** — Full Auto doesn't auto-approve; no way to approve from chat | ✅ Blocked commands now surface an inline approval/escalation affordance instead of a dead end (`vesta/gui_permissions.py`, `vesta/gui_web.py`); Full Auto approval semantics clarified | `tests/test_gui_permissions.py`, `tests/test_reliable_ai_controls.py` |
| **F17** — Safe Auto hard-blocks "Run any command" with no escalation | ✅ Same approval-flow work as F9: a block explains the policy and offers the supported escalation path (switch run mode) instead of an unsatisfiable plea | `tests/test_gui_permissions.py` |
| **F14** — "Implement" cards with no implementation | ✅ Completion honesty (F22/F24): cards require diff evidence to claim implementation | `tests/test_completion_contract.py` |
| **F8 / F11** — Green "Completed" / "✓ Ran" for no-op runs | ✅ Tool-result honesty (F19 mechanism, generalized): empty/errored command output renders as failure; completion requires evidence | `tests/test_github_adapter_reads.py`, `tests/test_completion_contract.py` |
| **F20** — Three overlapping "mode" concepts disagree | ✅ Single source of truth for effective mode; Run mode / Task focus / Agent mode derive from it and cannot silently disagree (`vesta/gui_modes.py`, `vesta/gui_controls.py`) | `tests/test_gui_modes.py` |

## Medium / Low

| Finding | Fix | Regression tests |
| --- | --- | --- |
| **F12** — Recursive self-invocation (`vesta route` inside a task) | ✅ Recursion guard: the agent's tool space no longer leaks the workspace "vesta route" activation; Vesta-calling-Vesta commands are blocked inside a task | engine guard tests (owning workstream) |
| **F13** — Redundant repeated commands / pointless code search | ✅ Tool-result honesty + repeated-identical-failure stop (existing #311 guard, now on the free path too) cuts loop waste | `tests/test_free_models.py` |
| **F15** — Paid spend with zero progress | 📄 Visibility/docs — see below | — |
| **F7** — Single-shot stop (Gemini free) | ✅ Fixed by F6: the free path runs the real agentic tool loop instead of one narrated turn | `tests/test_free_models.py` |
| **F4** — Header status vs composer mode mismatch | ✅ Fixed by F16: both surfaces read the same engine mode | `tests/test_gui_controls.py` |
| **F21** — "Agent mode" inspector field stale until next run | ✅ Inspector derives from the same mode source of truth (F20) and refreshes on switch | `tests/test_gui_modes.py` |
| **F3** — No branded executable (`pythonw.exe` in OS dialogs) | 📄 **Documented, packaging follow-up** — `docs/BRANDED_EXECUTABLE.md`: why pip launches show `pythonw.exe`, the existing Nuitka `Vesta.exe` path, and the recommended signing track | — |
| **F2** — Free models in picker but absent from `model_registry` | ✅ `gemini`/`groq`/`mistral` registered in `vesta/model_registry._REGISTRY` with honest capability metadata (new `free_providers()`; account-only `providers()`/`catalog()` contracts unchanged); split documented in both modules' docstrings | `tests/test_free_model_registry.py` |

## F15 — how this bundle prevents paid burn with zero progress

No single flag caused the ~$0.16 of zero-progress Haiku spend; it was three
holes stacking. Each is now closed:

1. **Tool-result honesty (F19/F8/F11).** Empty command output used to be a
   green "✓ Ran", so the model looped re-asking for the issue text — paid
   turns spent on noise. Empty/malformed output is now an error the model can
   act on, and `issue_view` uses the reliable `--json` form, so the first
   paid turn gets real data.
2. **Completion gating (F22/F24/F14).** "Completed" requires evidence
   (diff/test/PR). A run that is going nowhere can no longer exit looking
   successful — it surfaces as stuck, which is what stops a user from
   re-prompting (and re-paying) under a false belief of progress.
3. **Loop & recursion guards (F12/F13).** Recursive self-invocation
   (`vesta route` from inside a task — Vesta paying Vesta) is blocked, and the
   repeated-identical-failure stop (#311, now covering the free path too)
   terminates thrash after 3 identical failures instead of burning the whole
   per-task budget.

Net effect: a run either makes verifiable progress, stops early with an
honest stuck/blocked state, or asks for a real decision — it cannot spin paid
turns while reporting green.

## Live side effect from the QA run

GitHub issue **#219 was really closed** with a fabricated comment during Run 4
(F22). The automated reopen was blocked by the safety classifier and left for
the owner to authorize — see the QA report header. That decision is still
pending and is intentionally *not* automated by this bundle.
