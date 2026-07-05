# OPai Coding-Agent Autonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a centralized autonomy policy, durable coding-agent runtime, structured ACI/task packets, persistent repository context, safe GitHub workflow services, and truthful GUI workflow status.

**Architecture:** Pure policy and scoring modules make decisions; a persisted event-driven runtime owns workflow truth; injected ACI and GitHub adapters perform bounded operations; the GUI pipeline composes task packets and capability contracts. Existing provider transports, authentication, redaction, and receipts remain unchanged.

**Tech Stack:** Python 3.10+, unittest, PySide/web GUI payloads, Git, GitHub CLI.

---

### Task 1: Intent and authorization policy

**Files:**
- Create: `opaihub/agent_policy.py`
- Create: `tests/test_agent_autonomy.py`

- [ ] Write failing tests proving Explain, Review, Implement, Ship, Dangerous, latest-instruction precedence, PR authorization, merge gates, and secret-safe contracts.
- [ ] Run `python -m unittest tests.test_agent_autonomy.AgentPolicyTests -v` and confirm failures are missing APIs.
- [ ] Implement `AgentMode`, `AgentPolicy`, `resolve_agent_policy`, and `build_capability_contract` with explicit capability sets.
- [ ] Run the policy tests and confirm they pass.

### Task 2: Persistent repository context and dirty classification

**Files:**
- Create: `opaihub/repo_context.py`
- Modify: `tests/test_agent_autonomy.py`

- [ ] Write failing tests for Git-root resolution, active-context persistence, remote/branch capture, unrelated dirty paths, conflicting dirty paths, and safe worktree command construction.
- [ ] Run the repository-context test class and confirm it fails for missing APIs.
- [ ] Implement `RepoContext`, `resolve_repo_context`, `save_active_repo`, `load_active_repo`, `classify_dirty_paths`, and `prepare_isolated_worktree` using injected subprocess execution.
- [ ] Run the repository-context tests and confirm they pass.

### Task 3: Issue ranking and GitHub orchestration

**Files:**
- Create: `opaihub/github_workflow.py`
- Modify: `tests/test_agent_autonomy.py`

- [ ] Write failing tests for deterministic issue ranking, manageable high-value selection, mockable list/create/merge calls, and every merge safety gate.
- [ ] Run the GitHub-workflow tests and confirm they fail for missing APIs.
- [ ] Implement `IssueCandidate`, `rank_issues`, `select_small_important_issue`, `GitHubAdapter`, `ShipChecks`, and `CodingWorkflow`.
- [ ] Run the GitHub-workflow tests and confirm they pass.

### Task 4: Pipeline capability contract and workflow state

**Files:**
- Create: `opaihub/workflow_state.py`
- Modify: `opaihub/gui_pipeline.py`
- Modify: `opai/gui_modes.py`
- Modify: `opai/gui_desktop.py`
- Modify: `opai/gui_web.py`
- Modify: `tests/test_agent_autonomy.py`
- Modify: `tests/test_gui_modes.py`

- [ ] Write failing integration tests proving a fix/PR request reaches the provider as Implement mode despite stale read-only boilerplate, Explain remains read-only, and response payloads include policy/workflow state.
- [ ] Run those focused tests and confirm the expected failures.
- [ ] Add persistent `WorkflowState` helpers and route raw GUI messages through policy resolution before prompt composition.
- [ ] Make task focus advisory, keep output formatting presentational, and attach repo/policy/workflow metadata to every terminal pipeline response.
- [ ] Run the focused pipeline and GUI-mode tests and confirm they pass.

### Task 5: UI/status surface

**Files:**
- Modify: `opai/gui_web.py`
- Modify: `opai/assets/web/app.js`
- Modify: `opai/gui_desktop.py`
- Modify: `tests/test_agent_autonomy.py`
- Modify: `opai/assets/web/__tests__/e2e/inspector.spec.js`

- [ ] Write failing payload/DOM tests for active path, branch, remote, dirty state, effective mode, workflow phase, tests, PR, merge, and blocker display.
- [ ] Run focused Python and web tests and confirm they fail for the missing fields.
- [ ] Extend boot, inspector, and result rendering using existing header/activity components without new modal dialogs.
- [ ] Run focused Python and web tests and confirm they pass.

### Task 6: Managed autonomy instructions and regression coverage

**Files:**
- Modify: `opai/integrations.py`
- Modify: `configs/permissions.yaml`
- Modify: `opaihub/data/hub/prompts/gitops.md`
- Modify: `tests/test_opai_integrations.py`
- Modify: `tests/test_agent_autonomy.py`

- [ ] Write failing tests for action-oriented managed instructions, current-task precedence, authorized push/PR behavior, dangerous confirmation, secret redaction, and avoidance of unnecessary clarification loops.
- [ ] Run the focused tests and confirm the intended failures.
- [ ] Replace generic write hesitation with the centralized autonomy contract while retaining paid/cloud/destructive gates.
- [ ] Run all focused autonomy, integration, pipeline, workspace, permission, and prompt tests.

### Task 7: Full verification and publication

**Files:**
- Modify: `.github/PULL_REQUEST_TEMPLATE.md` only if required by repository conventions.

- [ ] Run `python -m unittest discover -s tests` and require zero failures.
- [ ] Run `python -m ruff check opai opaihub tests` and require zero errors.
- [ ] Run `npm ci` if dependencies are absent, then `npm run test:unit` and relevant Playwright tests.
- [ ] Inspect `git diff --check`, `git status -sb`, and the complete diff for unrelated or secret-bearing content.
- [ ] Commit the focused change, push `codex/improve-agent-autonomy`, and open a ready PR titled `Upgrade OPai agent runtime, workflow state, and coding autonomy` with diagnosis, behavior, tests, before/after examples, and follow-ups.
- [ ] Inspect PR checks. Merge only if every `ShipChecks` condition and repository check succeeds; otherwise report the exact blocker.

### Task 8: Durable runtime, ACI, and task packets

- [ ] Add failing tests for all runtime states, valid/invalid transitions, resume, event history, structured observations, bounded reads, redaction, task packets, and workflow templates.
- [ ] Implement `agent_runtime`, `aci`, and `task_packet` with injected side-effect boundaries.
- [ ] Preserve existing provider transports and normalize their events into runtime observations.

### Task 9: Test/repair loop, workflow ledger, and deterministic safety

- [ ] Add failing tests for test discovery, focused/full ordering, failure parsing, bounded retries, secret/risky-file/destructive/production/unrelated-diff gates, and fail-closed merge.
- [ ] Implement the test loop, workflow ledger, and safety gate report.
- [ ] Add deterministic OPaiBench scenarios under `tests/agent_evals`.

### Task 10: GitHub workbench and cockpit

- [ ] Extend the mockable GitHub adapter with linked PR lookup, PR update, check inspection, comments, and merge status.
- [ ] Expose runtime history, issue, blocker, next actions, changed files, tests, provider/cost, PR, and merge gates in shared GUI payloads.
- [ ] Add the eleven local workflow templates and remove remaining prompt-level workflow contradictions.
