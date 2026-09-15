# Vesta Alpha Reliability Programme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make productive Vesta runs continue without an internal tool ceiling, make usage resettable and provably accurate, rebuild Settings around lazy honest controls, and meet measurable Windows responsiveness/release gates.

**Architecture:** Introduce focused completion, tool-loop, usage-report, atomic-I/O, workspace-snapshot, and Settings-service modules while retaining thin compatibility adapters at the existing runner/GUI boundaries. Provider turns produce one canonical usage contract; GUI surfaces consume versioned asynchronous envelopes; every non-success state remains durable and resumable. Delivery stays one integrated branch because execution, accounting, and Settings share the same completion/usage/snapshot contracts, but tasks are independently reviewable and committed.

**Tech Stack:** Python 3.10+, PySide6/QWebChannel, vanilla JavaScript/CSS, pytest/unittest, Vitest, Playwright, append-only JSONL, Git/GitHub REST, Windows `msvcrt` plus POSIX `fcntl`, Ruff, Bandit, build/twine.

## Global Constraints

- `checkpoint_interval` is 12 maintenance calls, never a terminal default.
- Context compacts at the earlier of 48,000 serialized characters or 35% of the declared provider context; retain at most two complete protocol atoms.
- Per-observation content is capped at 8,000 characters; compact summaries at 16,000; evidence fingerprints at 256.
- Goal exploration pauses recoverably after 12 calls per unsatisfied subgoal, three stagnant checkpoints, or ten controller-active minutes without a milestone.
- Zero, blank, and `None` mean `Unlimited`; positive token/request thresholds are advisory and never block dispatch.
- Paid/cloud consent, panic, provider auth/quota/billing, and task/daily/monthly dollar caps are checked before every provider call.
- Raw prompts, provider messages, tool output, credentials, and unredacted source content never enter durable checkpoints or usage/reset events.
- Ledger events remain append-only and use one interprocess sequence; reset never deletes lifetime truth.
- Settings desktop mode collapses the global sidebar at 1040 px; the product minimum is 720 px; actions have at least 44 px targets.
- Cold snapshots use at most two Git processes and one ledger parse; valid warm snapshots use zero of each.
- Shell paint is at most 250 ms cold/100 ms warm, Settings shell 100 ms, Settings data 300 ms cold/100 ms warm, and synchronous UI work 50 ms on the reference Windows environment.
- No paid model calls are used to implement or verify this plan.
- Preserve unrelated work and never use force push, hard reset, clean, destructive checkout, or branch deletion.
- Every behavioral change follows red/green TDD and gets a focused review before the next task.

---

### Task 1: Make state writes multiprocess-safe and restore canonical pytest discovery

**Files:**
- Create: `vestahub/atomic_io.py`
- Create: `tests/test_atomic_io.py`
- Create: `tests/test_pytest_discovery.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `interprocess_transaction(target: Path, *, timeout_seconds: float = 60.0) -> ContextManager[None]`.
- Produces: `atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8") -> None`.
- Produces: typed `InterprocessLockTimeout`.
- Provides the only new lock/write primitive used by Tasks 4, 7, and 8.

- [ ] **Step 1: Write failing atomic-I/O and discovery tests**

  Add subprocess-barrier tests modelled on `tests/test_gui_recents_multiprocess.py`:

  ```python
  def test_eight_processes_serialize_transactions(tmp_path):
      target = tmp_path / "state.json"
      results = run_writers(8, target)
      assert sorted(results) == list(range(8))
      assert json.loads(target.read_text())["writes"] == 8

  def test_default_collection_excludes_embedded_benchmark_fixtures():
      collected = collect_pytest()
      assert "tests/test_gui_web.py" in collected
      assert "vestahub/data/hub/benchmarks/parity" not in collected
  ```

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_atomic_io.py tests/test_pytest_discovery.py -q
  ```

  Expected: `vestahub.atomic_io` is missing and default discovery still enters embedded fixture repositories.

- [ ] **Step 3: Implement atomic primitives and test scope**

  Use a process-local per-path `RLock`, sibling `.lock` file, `msvcrt.locking` on Windows/`fcntl.flock` elsewhere, bounded sharing-violation retry, unique same-directory temp file, flush, `fsync`, and `os.replace`. Add:

  ```toml
  [tool.pytest.ini_options]
  filterwarnings = ["error::pytest.PytestCollectionWarning"]
  testpaths = ["tests"]
  ```

- [ ] **Step 4: Verify focused and explicit fixture collection**

  ```powershell
  python -m pytest tests/test_atomic_io.py tests/test_pytest_discovery.py -q
  python -m pytest --collect-only -q vestahub/data/hub/benchmarks/parity/bugfix/repo/test_calculator.py
  ```

  Expected: focused tests pass; direct fixture collection remains possible.

- [ ] **Step 5: Commit**

  ```powershell
  git add pyproject.toml vestahub/atomic_io.py tests/test_atomic_io.py tests/test_pytest_discovery.py
  git commit -m "test: make state IO and discovery deterministic"
  ```

### Task 2: Close autonomous-command bypasses and add safe GitHub discovery

**Files:**
- Modify: `vestahub/safety_gates.py`
- Modify: `vestahub/provider_tools.py`
- Modify: `vestahub/github_connector.py`
- Modify: `vestahub/agent_policy.py`
- Modify: `tests/test_run_command_tool.py`
- Modify: `tests/test_provider_tools.py`
- Modify: `tests/test_github_connector.py`
- Modify: `tests/test_agent_autonomy.py`

**Interfaces:**
- Produces: `NormalizedCommand` and `normalize_autonomous_command(raw, argv) -> NormalizedCommand | None`.
- Produces: `search_issues(project_root, *, query="", state="open", labels=(), limit=20, http=_default_http, allow_public=False) -> dict`.
- Adds: `github_search_issues` to `GITHUB_READ_TOOLS`.
- Allows only Git `status`, `diff`, `log`, `show`, `rev-parse`, and exact `branch --show-current`, optionally preceded by `--no-pager`.

- [ ] **Step 1: Extend the command bypass matrix and GitHub connector tests**

  ```python
  @pytest.mark.parametrize("command", [
      "git.exe push", "GH.EXE pr create", "git -c alias.x=push x",
      "git -C .. fetch", "git ls-remote origin", "cmd /c git push",
      "powershell -Command git push", "python -m malicious_push_module",
  ])
  def test_autonomous_command_bypasses_are_blocked(command, executor):
      result = executor.invoke("run_command", {"command": command})
      assert result["error_code"] == "COMMAND_BLOCKED"
      assert executor.aci.calls == []

  def test_issue_search_excludes_pull_requests_and_bounds_untrusted_text():
      result = search_issues(ROOT, query="good first issue", limit=2, http=fake_http)
      assert [item["number"] for item in result["issues"]] == [7, 9]
      assert all("pull_request" not in item for item in result["issues"])
  ```

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_run_command_tool.py tests/test_provider_tools.py tests/test_github_connector.py tests/test_agent_autonomy.py -q
  ```

- [ ] **Step 3: Implement the allowlist and bounded issue search**

  Normalize path/case/`.exe`, reject absolute executable paths, Git `-c`/`-C`, aliases, wrappers, shells, interpreters, all `gh`, network utilities, remote/submodule/LFS operations before ACI execution. Search only the active GitHub origin, URL-encode filters, cap at 50/two pages, exclude PR entries, return bounded quoted fields, and return typed `auth`, `consent_required`, `rate_limit`, or `not_github` reasons.

- [ ] **Step 4: Verify security and connector green**

  ```powershell
  python -m pytest tests/test_run_command_tool.py tests/test_provider_tools.py tests/test_github_connector.py tests/test_agent_autonomy.py -q
  ```

- [ ] **Step 5: Commit**

  ```powershell
  git add vestahub/safety_gates.py vestahub/provider_tools.py vestahub/github_connector.py vestahub/agent_policy.py tests/test_run_command_tool.py tests/test_provider_tools.py tests/test_github_connector.py tests/test_agent_autonomy.py
  git commit -m "fix(security): gate commands and GitHub discovery"
  ```

### Task 3: Define canonical completion and usage contracts

**Files:**
- Create: `vestahub/completion.py`
- Create: `vestahub/model_identity.py`
- Create: `vestahub/usage_report.py`
- Create: `tests/test_completion_contract.py`
- Create: `tests/test_model_identity.py`
- Create: `tests/test_usage_report.py`
- Modify: `vestahub/cost_telemetry.py`
- Modify: `tests/test_cost_telemetry.py`

**Interfaces:**
- Produces: `CompletionState`, `ProviderBlockedReason`, `CompletionResult`, `completion_state_from_legacy()`, and `legacy_status_for_completion()`.
- Produces: `canonical_usage_model_id()`, `model_provider()`, and `historical_model_descriptor()`.
- Produces: immutable `UsageValue`, `ProviderTurnUsage`, and `UsageReport` with `aggregate()`, `to_dict()`, and `to_ledger_fields()`.

- [ ] **Step 1: Write strict contract tests**

  ```python
  def test_legacy_stop_text_can_never_map_to_completed():
      state = completion_state_from_legacy({
          "status": "answered_by_free_api",
          "stopped_reason": "tool_budget_exhausted",
      })
      assert state is CompletionState.STUCK_NO_PROGRESS

  def test_provider_total_is_not_replaced_by_components():
      usage = ProviderTurnUsage.from_provider(
          turn_index=1, total=120, input_tokens=80, output_tokens=20
      )
      assert usage.total_tokens.value == 120
      assert usage.total_tokens.provenance == "provider"
  ```

  Cover every completion value, unknown legacy statuses, alias/no-alias identity, total-only/components-only/mixed/unknown/cache/reasoning values, independent cost precision, and input amplification only on complete provider coverage.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_completion_contract.py tests/test_model_identity.py tests/test_usage_report.py tests/test_cost_telemetry.py -q
  ```

- [ ] **Step 3: Implement the immutable contracts and telemetry adapter**

  Keep missing values as `None`; derive total only when both components exist; preserve raw ledger model IDs and canonicalize at the read boundary; convert to workflow telemetry through one `usage_report_to_cost_telemetry()` adapter.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_completion_contract.py tests/test_model_identity.py tests/test_usage_report.py tests/test_cost_telemetry.py -q
  git add vestahub/completion.py vestahub/model_identity.py vestahub/usage_report.py vestahub/cost_telemetry.py tests/test_completion_contract.py tests/test_model_identity.py tests/test_usage_report.py tests/test_cost_telemetry.py
  git commit -m "feat(runtime): define completion and usage truth"
  ```

### Task 4: Add the sequenced per-turn ledger and reset epochs

**Files:**
- Modify: `vestahub/ledger.py`
- Create: `tests/test_usage_ledger_v2.py`
- Create: `tests/test_usage_ledger_multiprocess.py`
- Modify: `tests/test_cost_ledger.py`

**Interfaces:**
- Adds: `EVENT_MODEL_CALL_STARTED`, `EVENT_USAGE_BASELINE_RESET`, `EVENT_USAGE_ADVISORY_NOTICE`, and `MODEL_CALL_SCHEMA_VERSION = 2`.
- Produces: `record_model_call_started(..., call_id, run_id, turn_index, model_id, provider_id, model_tier, provider_type, confirmed) -> dict`.
- Produces: `record_model_call_finalized(..., call_id, usage: ProviderTurnUsage) -> dict`.
- Produces: `reset_usage_baseline(project_root, model_id) -> dict`.
- Keeps: `record_model_call()` as a legacy writer until Task 7 removes aggregate call sites.

- [ ] **Step 1: Write sequence, epoch, idempotency, and race tests**

  ```python
  def test_reset_during_inflight_call_does_not_readd_old_usage(root):
      started = record_model_call_started(root, "task", call_id="r:1", run_id="r", turn_index=1, model_id=MODEL, provider_id="gemini", model_tier="L2", provider_type="free_api", confirmed=True)
      reset = reset_usage_baseline(root, MODEL)
      finalized = record_model_call_finalized(root, "task", call_id="r:1", usage=TURN)
      assert started["usage_epoch"] < reset["usage_epoch"]
      assert finalized["usage_epoch"] == started["usage_epoch"]
  ```

  Add stale/corrupt head recovery, same-second ordering, unresolved starts, duplicate start/final, unrelated model epochs, eight-process race, and injected append/head failures.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_usage_ledger_v2.py tests/test_usage_ledger_multiprocess.py tests/test_cost_ledger.py -q
  ```

- [ ] **Step 3: Implement locked sequence/head recovery**

  Under `_LEDGER_LOCK` plus `interprocess_transaction(usage.jsonl)`, recover `ledger.head.json` from valid tail events, allocate one monotonic sequence, append+`fsync`, update per-model epoch/active-call/advisory state, and atomically replace the head. Stable call IDs are `run_id:turn_index`; completion never emits a duplicate aggregate.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_usage_ledger_v2.py tests/test_usage_ledger_multiprocess.py tests/test_cost_ledger.py -q
  git add vestahub/ledger.py tests/test_usage_ledger_v2.py tests/test_usage_ledger_multiprocess.py tests/test_cost_ledger.py
  git commit -m "feat(usage): record sequenced provider turns"
  ```

### Task 5: Build the continuous controller and adaptive compaction

**Files:**
- Create: `vestahub/tool_loop.py`
- Create: `tests/test_tool_loop_controller.py`
- Create: `tests/test_tool_context_compaction.py`
- Modify: `vestahub/local_runner.py`
- Modify: `tests/test_free_models.py`

**Interfaces:**
- Produces: `ToolLoopPolicy`, strict `CompletionDecision`, `ToolProtocolAtom`, `ToolLoopState`, `CompactionReport`, `parse_completion_decision()`, `compact_context()`, `verify_completion()`, and `ToolLoopController.run()`.
- Refactors: `FreeAPIRunner.complete_with_tools(..., tool_calling_enabled=True, allow_mutations=None, policy=None, guard=None, checkpoint=None)`.
- Preserves: explicit `max_tool_calls` as a deprecated recoverable external ceiling; GUI supplies no value.

- [x] **Step 1: Write controller/compaction red tests**

  ```python
  def test_exactly_twelve_tools_can_still_complete(controller):
      result = controller.run(chat=scripted_chat(12, final_decision()), executor=EXECUTOR)
      assert result.completion_state is CompletionState.COMPLETED

  def test_unique_reads_do_not_fake_goal_progress(controller):
      result = controller.run(chat=endless_unique_reads(), executor=EXECUTOR)
      assert result.completion_state is CompletionState.STUCK_NO_PROGRESS

  def test_twelve_turn_replay_is_half_the_legacy_input():
      bounded = replay_with_compaction(FIXTURE_TURNS)
      assert bounded.cumulative_serialized_chars <= legacy_size(FIXTURE_TURNS) * 0.5
  ```

  Cover valid/invalid decision JSON, false evidence, task-class verification, batch crossing without slicing, cancellation between calls, partial effects, malformed/duplicate call IDs, protocol-atom integrity, Unicode JSON, bounded rings, and productive work across multiple checkpoints.

- [x] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_tool_loop_controller.py tests/test_tool_context_compaction.py tests/test_free_models.py -q
  ```

- [x] **Step 3: Implement controller and make the runner a transport adapter**

  Parse every no-tool response through the version-1 decision schema, compact before request threshold/checkpoint calls, retain whole protocol atoms, tie milestones to controller evidence, and return typed recoverable states. Remove the internal `tool_budget_exhausted` terminal.

- [x] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_tool_loop_controller.py tests/test_tool_context_compaction.py tests/test_free_models.py -q
  git add vestahub/tool_loop.py vestahub/local_runner.py tests/test_tool_loop_controller.py tests/test_tool_context_compaction.py tests/test_free_models.py
  git commit -m "feat(agent): continue productive tool runs"
  ```

### Task 6: Enforce per-turn financial/permission guards and read-only tools

**Files:**
- Create: `vestahub/execution_guard.py`
- Create: `tests/test_execution_guard.py`
- Modify: `vestahub/ask.py`
- Modify: `vesta/app_state.py`
- Modify: `vestahub/gui_pipeline.py`
- Modify: `tests/test_pipeline_routing_and_safety.py`
- Modify: `tests/test_agent_autonomy.py`
- Modify: `tests/test_model_call_accounting.py`

**Interfaces:**
- Produces: `ExecutionGuardContext`, `GuardDecision`, and `ExecutionGuard.check()`.
- Refactors: `run_explicit_model(..., tool_calling_enabled=False, allow_mutations=False, checkpoint=None, guard=None)`.
- Integrates: per-turn `record_model_call_started/finalized`; runner result carries canonical `usage_report`.

- [x] **Step 1: Write guard and read-only routing tests**

  ```python
  def test_guard_runs_before_every_provider_turn(fake_guard, runner):
      runner.complete_with_tools("task", project_root=ROOT, allow_edits=False, tool_calling_enabled=True, guard=fake_guard)
      assert fake_guard.checked_turns == [1, 2, 3]

  def test_discovery_gets_read_tools_without_mutations(pipeline):
      result = pipeline("find me a git issue that we can solve", mode="full-auto", focus="build")
      assert "github_search_issues" in result.tool_names
      assert not {"write_file", "apply_patch", "run_command", "git_commit"} & set(result.tool_names)
  ```

  Cover cancel/panic/cloud consent/auth/rate/quota/billing/task/daily/monthly caps, free $0, and advisory threshold ignored by guard.

- [x] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_execution_guard.py tests/test_pipeline_routing_and_safety.py tests/test_agent_autonomy.py tests/test_model_call_accounting.py -q
  ```

- [ ] **Step 3: Implement guard, authority split, and per-turn usage wiring**

  Separate `tool_calling_enabled` from `allow_mutations`; use `ExecutionGuard` immediately before each `_chat`; append start/final events around every response; remove `_ask_free_model`/account aggregate writers; record incurred usage for every completion state.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_execution_guard.py tests/test_pipeline_routing_and_safety.py tests/test_agent_autonomy.py tests/test_model_call_accounting.py tests/test_free_models.py -q
  git add vestahub/execution_guard.py vestahub/ask.py vesta/app_state.py vestahub/gui_pipeline.py vestahub/local_runner.py tests/test_execution_guard.py tests/test_pipeline_routing_and_safety.py tests/test_agent_autonomy.py tests/test_model_call_accounting.py tests/test_free_models.py
  git commit -m "fix(runtime): guard every provider continuation"
  ```

### Task 7: Persist controller checkpoints and propagate honest completion

**Files:**
- Modify: `vestahub/checkpoints.py`
- Modify: `vestahub/gui_pipeline.py`
- Modify: `vesta/gui_recents.py`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/assets/web/message-state.js`
- Modify: `vesta/assets/web/app.js`
- Modify: `tests/test_run_checkpoints.py`
- Modify: `tests/test_session_resume.py`
- Modify: `tests/test_task_outcomes.py`
- Modify: `tests/test_message_contract.py`
- Modify: `vesta/assets/web/__tests__/message-state.test.js`
- Modify: `vesta/assets/web/__tests__/e2e/activity-truth.spec.js`

**Interfaces:**
- Advances: `RunCheckpoint` to schema 2 with `run_id`, controller state/revision, side-effect fingerprints, next turn, usage IDs, recovery state, and lease.
- Produces: `update_run_checkpoint()`, `acquire_checkpoint_lease()`, `renew_checkpoint_lease()`, `release_checkpoint_lease()`, and `CheckpointLeaseConflict`.
- Produces: `resume_tool_loop(checkpoint, *, saved_instruction) -> ResumeRequest | CompletionResult`.

- [ ] **Step 1: Write persistence and cross-surface red tests**

  Assert v1 remains readable/non-resumable; schema 2 round-trips without prompt/tool/secret content; competing leases fail/expire; stuck -> restart -> Resume -> completed does not repeat a mutating fingerprint or usage event. Parameterize every non-complete state through runner, app state, pipeline, checkpoint, task outcome, registry, saved chat, CLI, and frontend; none may display `Vesta completed`.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_run_checkpoints.py tests/test_session_resume.py tests/test_task_outcomes.py tests/test_message_contract.py tests/test_pipeline_routing_and_safety.py -q
  npm run test:unit -- vesta/assets/web/__tests__/message-state.test.js
  ```

- [ ] **Step 3: Implement schema-2 persistence, leases, resume, and typed mapping**

  Use atomic I/O, bounded recursive redaction, saved redacted chat plus compact controller summary, one-click Resume, injected retry/backoff, and an explicit completion mapping table. Only `CompletionState.COMPLETED` may emit answered/DONE/completed outcome/activity.

- [ ] **Step 4: Verify Python, unit, and activity E2E**

  ```powershell
  python -m pytest tests/test_run_checkpoints.py tests/test_session_resume.py tests/test_task_outcomes.py tests/test_message_contract.py tests/test_pipeline_routing_and_safety.py -q
  npm run test:unit -- vesta/assets/web/__tests__/message-state.test.js
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/activity-truth.spec.js --project=chromium
  ```

- [ ] **Step 5: Commit**

  ```powershell
  git add vestahub/checkpoints.py vestahub/gui_pipeline.py vesta/gui_recents.py vesta/gui_web.py vesta/assets/web/message-state.js vesta/assets/web/app.js tests/test_run_checkpoints.py tests/test_session_resume.py tests/test_task_outcomes.py tests/test_message_contract.py vesta/assets/web/__tests__/message-state.test.js vesta/assets/web/__tests__/e2e/activity-truth.spec.js
  git commit -m "fix(runtime): preserve resumable completion truth"
  ```

### Task 8: Build honest snapshots, Unlimited semantics, and non-blocking advisories

**Files:**
- Modify: `vestahub/usage.py`
- Modify: `vestahub/gui_preferences.py`
- Modify: `vestahub/gui_pipeline.py`
- Create: `tests/test_usage_snapshots_v2.py`
- Create: `tests/test_gui_preferences_atomic.py`
- Create: `tests/test_usage_pipeline.py`
- Modify: `tests/test_reliable_ai_controls.py`
- Modify: `tests/test_model_call_accounting.py`

**Interfaces:**
- Refactors: `build_usage_snapshots(..., events=None, now=None, local_tz=None) -> list[dict]` as a one-pass canonical aggregator.
- Produces: `usage_advisory_status()` and `record_advisory_notice_once()`.
- Refactors: `save_usage_limit(..., limit: int | str | None)` and adds `remove_usage_limit()`.
- Keeps: temporary v1 `used/limit/remaining` snapshot keys until Task 11 migrates the UI.

- [ ] **Step 1: Write snapshot/preference/pipeline red tests**

  Include the screenshot fixture (905,445 input + 8,926 output = 914,371; 16 tasks; 38+ turns), reset epoch/window intersection, local timezone/DST, stale separate quota, historical model aliases, unresolved calls, per-field provenance, blank/zero/None removal, concurrent preference mutations, and reached advisory still invoking the provider once.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_usage_snapshots_v2.py tests/test_gui_preferences_atomic.py tests/test_usage_pipeline.py tests/test_reliable_ai_controls.py tests/test_model_call_accounting.py -q
  ```

- [ ] **Step 3: Implement one-pass snapshots and atomic preferences**

  Group one event pass by canonical model, return catalog plus historical rows, intersect epoch/local-calendar windows, keep quota separate/staleness-labelled, show lower-bound legacy turns, read-merge-write preferences under the transaction lock, and replace `needs_limit_confirmation` with one deduplicated advisory notice plus uninterrupted dispatch.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_usage_snapshots_v2.py tests/test_gui_preferences_atomic.py tests/test_usage_pipeline.py tests/test_reliable_ai_controls.py tests/test_model_call_accounting.py -q
  git add vestahub/usage.py vestahub/gui_preferences.py vestahub/gui_pipeline.py tests/test_usage_snapshots_v2.py tests/test_gui_preferences_atomic.py tests/test_usage_pipeline.py tests/test_reliable_ai_controls.py tests/test_model_call_accounting.py
  git commit -m "fix(usage): make limits advisory and resettable"
  ```

### Task 9: Introduce one immutable workspace snapshot and staged boot

**Files:**
- Create: `vestahub/workspace_snapshot.py`
- Create: `tests/test_workspace_snapshot.py`
- Create: `tests/test_gui_boot_hydration.py`
- Modify: `vestahub/budget.py`
- Modify: `vesta/app_state.py`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/assets/web/app.js`
- Modify: `vesta/assets/web/__tests__/e2e/mock-bridge.js`
- Modify: `vesta/assets/web/__tests__/e2e/async-data.spec.js`

**Interfaces:**
- Produces: immutable `GitReadSnapshot`, `WorkspaceReadSnapshot`, and `WorkspaceSnapshotService.get/invalidate/revision/cancel_root/clear` with two-second TTL.
- Produces: `boot_shell_payload()` and `boot_hydration_payload()`; keeps `boot_payload()` as compatibility composition.
- Adds bridge: `requestBootHydration(revision, requestId)` / `bootHydrationReady(envelope)`.

- [ ] **Step 1: Write snapshot and hydration red tests**

  Prove cold build uses no more than `git status --porcelain=v2 --branch -z --untracked-files=all` and `git ls-files -z`, one ledger/preferences/policy/registry read, immutable consumers, identical-request coalescing, cancellation/failure release, root isolation, explicit invalidation matrix, valid warm zero-call hit, and stale hydration rejected after workspace switch.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_workspace_snapshot.py tests/test_gui_boot_hydration.py tests/test_gui_web.py -q
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/async-data.spec.js --project=chromium
  ```

- [ ] **Step 3: Implement snapshot service and staged boot**

  Derive workspace/status/inspector/budget/usage from one event tuple, cache by resolved root+Git common dir+revision, use immutable mappings, paint a no-Git/no-ledger shell, then hydrate in the background with root/revision/request validation.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_workspace_snapshot.py tests/test_gui_boot_hydration.py tests/test_gui_web.py -q
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/async-data.spec.js --project=chromium
  git add vestahub/workspace_snapshot.py vestahub/budget.py vesta/app_state.py vesta/gui_web.py vesta/assets/web/app.js vesta/assets/web/__tests__/e2e/mock-bridge.js vesta/assets/web/__tests__/e2e/async-data.spec.js tests/test_workspace_snapshot.py tests/test_gui_boot_hydration.py
  git commit -m "perf(gui): hydrate from one workspace snapshot"
  ```

### Task 10: Add the versioned lazy Settings service and bounded worker pool

**Files:**
- Create: `vesta/gui_settings.py`
- Create: `tests/test_gui_settings.py`
- Modify: `vesta/gui_web.py`
- Modify: `tests/test_gui_web.py`

**Interfaces:**
- Produces: `settings_shell_payload()`, `settings_section_payload()`, and `settings_mutation_payload()`.
- Supports section IDs: providers, models, firewall, permissions, privacy, appearance, about.
- Supports typed usage save/remove/reset plus existing provider/GitHub/preference/privacy actions.
- Adds versioned `requestSettingsSection/settingsSectionReady` and `requestSettingsMutation/settingsMutationReady` envelopes.

- [ ] **Step 1: Write service/bridge red tests**

  Assert unknown section/action performs no work; only Providers can request diagnostics; non-provider reads use one snapshot; usage mutation returns one row patch/new revision; response contains no submitted secret; concurrent requests coalesce; stale root/revision is labelled; executor shutdown cancels pending work.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_gui_settings.py tests/test_gui_web.py -q
  ```

- [ ] **Step 3: Implement service and shared `ThreadPoolExecutor(max_workers=4)`**

  Route compatibility `settings_payload()` and synchronous slots through the same service; invalidate only affected caches; emit typed secret-free envelopes; never start provider diagnostics for hidden sections.

- [ ] **Step 4: Verify and commit**

  ```powershell
  python -m pytest tests/test_gui_settings.py tests/test_gui_web.py -q
  git add vesta/gui_settings.py vesta/gui_web.py tests/test_gui_settings.py tests/test_gui_web.py
  git commit -m "perf(settings): load sections and mutations lazily"
  ```

### Task 11: Rebuild Settings UX on the stable service contracts

**Files:**
- Modify: `vesta/assets/web/settings.js`
- Modify: `vesta/assets/web/app.js`
- Modify: `vesta/assets/web/styles.css`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/assets/web/__tests__/settings.test.js`
- Modify: `vesta/assets/web/__tests__/e2e/settings-pages.spec.js`
- Modify: `vesta/assets/web/__tests__/e2e/settings-search.spec.js`
- Modify: `vesta/assets/web/__tests__/e2e/settings-routing.spec.js`
- Create: `vesta/assets/web/__tests__/e2e/settings-responsive.spec.js`

**Interfaces:**
- Produces: `VestaSettings.mount(page, ctx) -> SettingsController` with `navigate`, `acceptSection`, `acceptMutation`, `search`, and `destroy`.
- Stable DOM: `#settingsSearch`, `.settings-rail`, `#settingsSectionHost`, and `#settingsStatus[role=status][aria-live=polite]`.
- Produces: `enterSettingsMode()`, `leaveSettingsMode()`, and `syncSettingsShellMode()`.

- [ ] **Step 1: Write lazy DOM, usage-action, accessibility, and responsive red tests**

  Unit tests prove inactive renderers never run, metadata search renders no hidden page, stale envelopes drop, a row mutation preserves node identity/focus, and live status announces success/error. E2E verifies Unlimited, Reset usage to zero with audit disclosure, Remove limit, measured/estimated/mixed badges, input/output and 38+ turns, separate quota/blocking badges, sidebar restoration, keyboard navigation, 44 px targets, reduced motion, and no overflow at 720/768/1040/1280.

- [ ] **Step 2: Run red tests**

  ```powershell
  npm run test:unit -- vesta/assets/web/__tests__/settings.test.js
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/settings-pages.spec.js vesta/assets/web/__tests__/e2e/settings-search.spec.js vesta/assets/web/__tests__/e2e/settings-routing.spec.js vesta/assets/web/__tests__/e2e/settings-responsive.spec.js --project=chromium
  ```

- [ ] **Step 3: Implement stable shell, Cost Firewall hierarchy, and responsive mode**

  Mount once, render only active section, delegate events once, search immutable registry metadata, patch affected rows, restore focus, collapse global sidebar to 64 px at >=1040, drawer it below, switch Settings navigation below 760, and lower Qt minimum to 720x700. Remove duplicate Settings CSS and unconditional doctor refresh.

- [ ] **Step 4: Verify and commit**

  ```powershell
  npm run test:unit
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/settings-pages.spec.js vesta/assets/web/__tests__/e2e/settings-search.spec.js vesta/assets/web/__tests__/e2e/settings-routing.spec.js vesta/assets/web/__tests__/e2e/settings-responsive.spec.js --project=chromium
  git add vesta/assets/web/settings.js vesta/assets/web/app.js vesta/assets/web/styles.css vesta/gui_web.py vesta/assets/web/__tests__/settings.test.js vesta/assets/web/__tests__/e2e/settings-pages.spec.js vesta/assets/web/__tests__/e2e/settings-search.spec.js vesta/assets/web/__tests__/e2e/settings-routing.spec.js vesta/assets/web/__tests__/e2e/settings-responsive.spec.js
  git commit -m "feat(settings): deliver honest responsive controls"
  ```

### Task 12: Add deterministic GUI performance evidence

**Files:**
- Create: `vesta/gui_performance.py`
- Create: `tests/test_gui_performance.py`
- Create: `vesta/assets/web/performance.js`
- Modify: `vesta/assets/web/index.html`
- Modify: `vesta/assets/web/app.js`
- Modify: `vesta/cli.py`
- Create: `vesta/assets/web/__tests__/e2e/settings-performance.spec.js`
- Modify: `docs/ALPHA_READINESS_2026-07-12.md`

**Interfaces:**
- Produces: immutable `GuiPerfReport` and `run_gui_perf(workspace, *, cold_samples, warm_samples, output=None)`.
- Adds CLI: `vesta perf gui --workspace PATH --cold-samples 5 --warm-samples 20 --output PATH`.
- Adds JS: `VestaPerf.mark()`, `measure()`, and `snapshot()`.

- [ ] **Step 1: Write deterministic counter/marker red tests**

  Use injected clocks/delays to assert shell zero Git/ledger calls, cold snapshot <=2 Git/one ledger, warm zero, hidden Settings zero provider processes, marker order, submit-to-dispatch separation from provider latency, 100-message render path, and no synthetic main-thread task >50 ms.

- [ ] **Step 2: Run red tests**

  ```powershell
  python -m pytest tests/test_gui_performance.py -q
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/settings-performance.spec.js --project=chromium
  ```

- [ ] **Step 3: Implement instrumentation, CLI harness, and release JSON**

  Record process/shell/chat/dispatch/first-byte/first-render/Settings/history markers; observe paint/longtask where supported; emit hardware/cache/sample/p50/p95/payload/process/ledger data without prompts or source paths.

- [ ] **Step 4: Record before/after reference evidence and commit**

  ```powershell
  vesta perf gui --workspace C:\Users\Frist\Documents\website\QuotePack --cold-samples 5 --warm-samples 20 --output .vestahub\generated\gui-perf.json
  python -m pytest tests/test_gui_performance.py -q
  npm run test:e2e -- vesta/assets/web/__tests__/e2e/settings-performance.spec.js --project=chromium
  git add vesta/gui_performance.py vesta/assets/web/performance.js vesta/assets/web/index.html vesta/assets/web/app.js vesta/cli.py tests/test_gui_performance.py vesta/assets/web/__tests__/e2e/settings-performance.spec.js docs/ALPHA_READINESS_2026-07-12.md
  git commit -m "perf(gui): enforce alpha responsiveness budgets"
  ```

### Task 13: Enterprise verification, independent review, PR, and merge

**Files:**
- Verify: every file changed in Tasks 1–12
- Modify only if evidence requires: `docs/ALPHA_READINESS_2026-07-12.md`, `CHANGELOG.md`

**Interfaces:**
- Produces: one reviewable PR from `codex/alpha-reliability` to `main` with test, security, migration, screenshot, and performance evidence.

- [ ] **Step 1: Run focused programme suites**

  ```powershell
  python -m pytest tests/test_atomic_io.py tests/test_completion_contract.py tests/test_model_identity.py tests/test_usage_report.py tests/test_usage_ledger_v2.py tests/test_usage_ledger_multiprocess.py tests/test_tool_loop_controller.py tests/test_tool_context_compaction.py tests/test_execution_guard.py tests/test_run_checkpoints.py tests/test_session_resume.py tests/test_usage_snapshots_v2.py tests/test_gui_preferences_atomic.py tests/test_usage_pipeline.py tests/test_workspace_snapshot.py tests/test_gui_boot_hydration.py tests/test_gui_settings.py tests/test_gui_performance.py -q
  npm run test:unit
  npm run test:e2e -- --project=chromium
  ```

- [ ] **Step 2: Run canonical, static, security, package, install, and release gates**

  ```powershell
  python -m pytest -q
  python -B -m ruff format --check .
  python -B -m ruff check --no-cache .
  python -B -m bandit -r vesta vestahub opcoding -q
  python -B -m vestahub validate
  python -m build
  python -m twine check dist\*
  python -m vestahub.release_preflight --help
  git diff --check
  ```

- [ ] **Step 3: Run independent correctness, security, and UX reviews**

  Review the full `origin/main...HEAD` diff for false-success paths, usage double counting, reset races, command/network bypasses, secret leakage, stale UI responses, accessibility, performance overclaims, and unrelated changes. Fix every validated finding with a failing test first, then rerun affected gates.

- [ ] **Step 4: Push and open the ready PR**

  ```powershell
  git push -u origin codex/alpha-reliability
  gh pr create --base main --head codex/alpha-reliability --title "fix: make Vesta continuous, honest, and responsive" --body-file .vestahub\generated\alpha-reliability-pr.md
  ```

- [ ] **Step 5: Monitor CI, resolve failures, and merge**

  ```powershell
  gh pr checks --watch
  gh pr merge --merge
  ```

  Expected: required checks pass, PR is merged into `main`, branch remains available for rollback evidence, and the merged commit is verified through the GitHub API/CLI.
