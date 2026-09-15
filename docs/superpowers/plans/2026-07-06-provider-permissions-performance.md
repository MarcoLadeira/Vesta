# Provider Permissions and Dispatch Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Vesta editable modes truthfully grant repository edits to built-in API models and Gemini CLI while reducing explicit-provider dispatch overhead.

**Architecture:** Resolve one provider execution plan from user intent, effective autonomy mode, and provider capabilities. Built-in free API models use a bounded OpenAI-compatible repository tool loop backed by the existing `AgentComputerInterface`; external Gemini CLI receives managed instructions and exact approval-mode arguments. Explicit providers bypass Auto recommendation, prose cache, and duplicate evidence collection.

**Tech Stack:** Python 3.10+, `urllib`, dataclasses, unittest/pytest, Qt/web GUI pipeline, GitHub Actions.

---

## File structure

- Create `vestahub/provider_tools.py`: provider-neutral repository tool schemas, argument validation, bounded execution, dirty-path protection, and fixed test-command allowlist.
- Modify `vestahub/provider_adapters.py`: immutable provider capability and execution-plan contracts plus Gemini CLI approval-mode mapping.
- Modify `vestahub/local_runner.py`: OpenAI-compatible tool-call loop for `FreeAPIRunner`.
- Modify `vestahub/ask.py`: direct explicit-provider execution path with no Auto recommendation/cache/evidence duplication.
- Modify `vesta/app_state.py`: carry edit/mode intent into free-provider execution and report tool/change evidence.
- Modify `vestahub/gui_pipeline.py`: preserve acknowledged Full Auto and pass edit authority to free providers.
- Modify `vesta/integrations.py`, `vesta/clients.py`, `vestahub/agent_launch.py`, and `vesta/cli.py`: Gemini project/global discovery, wrappers, and CLI mode mapping.
- Modify focused test modules and add `tests/test_provider_tools.py` for the bounded tool boundary.

### Task 1: Normalize capabilities and preserve effective editable modes

**Files:**
- Modify: `vestahub/provider_adapters.py`
- Modify: `vestahub/gui_pipeline.py`
- Test: `tests/test_agent_autonomy.py`
- Test: `tests/test_provider_adapters.py`

- [ ] **Step 1: Write failing mode and capability tests**

Add tests equivalent to:

```python
def test_pipeline_preserves_pinned_full_auto_for_implementation(self):
    prefs = {"run_mode": "full-auto", "full_auto_pinned": True, "full_auto_acknowledged": True}
    with mock.patch("vestahub.gui_pipeline.load_gui_preferences", return_value=prefs):
        result = handle_gui_message(
            root,
            "Fix the bug and run tests.",
            model_id="account:claude:sonnet",
            mode="full-auto",
            account_runner=runner,
        )
    self.assertEqual(result["effective_run_mode"], "full-auto")
    self.assertEqual(runner.calls[0]["mode"], "full-auto")

def test_execution_plan_intersects_policy_mode_and_provider_capabilities(self):
    plan = resolve_execution_plan(
        adapter_for("gemini"),
        resolve_agent_policy("Fix the bug."),
        effective_mode="full-auto",
    )
    self.assertTrue(plan.allow_edits)
    self.assertEqual(plan.mode, "full-auto")
    self.assertIn("apply_patch", plan.tools)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m unittest tests.test_agent_autonomy tests.test_provider_adapters -v
```

Expected: failures showing Full Auto became `safe-auto` and capability plan symbols do not exist.

- [ ] **Step 3: Implement the immutable capability contract**

Add contracts shaped as:

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    repo_read: bool
    patch_edit: bool
    run_tests: bool
    native_tools: bool
    streaming: bool

@dataclass(frozen=True)
class ProviderExecutionPlan:
    provider_id: str
    mode: str
    allow_edits: bool
    tools: tuple[str, ...]

def gemini_approval_mode(mode: str) -> str:
    return {
        "ask": "plan",
        "plan": "plan",
        "approve-edits": "auto_edit",
        "safe-auto": "auto_edit",
        "full-auto": "yolo",
    }.get(mode, "plan")
```

`resolve_execution_plan` must expose mutating tools only when policy mode is Implement/Ship and the provider supports them. Unknown/read-only intent degrades to no mutating tools.

- [ ] **Step 4: Preserve the acknowledged effective mode in the GUI pipeline**

Replace the unconditional implementation downgrade with:

```python
if policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}:
    selected_mode = (
        requested_run_mode
        if requested_run_mode in {"safe-auto", "full-auto"}
        else "safe-auto"
    )
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 1 command. Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add vestahub/provider_adapters.py vestahub/gui_pipeline.py tests/test_agent_autonomy.py tests/test_provider_adapters.py
git commit -m "fix(autonomy): preserve provider edit permissions"
```

### Task 2: Build the bounded repository tool boundary

**Files:**
- Create: `vestahub/provider_tools.py`
- Modify: `vestahub/aci.py`
- Test: `tests/test_provider_tools.py`

- [ ] **Step 1: Write failing repository-tool tests**

Cover read-only denial, repository escape, baseline dirty conflict, valid patch application, malformed arguments, patch-size limit, cancellation, and allowlisted tests. The central happy-path test is:

```python
def test_editable_executor_applies_valid_patch_inside_clean_repo(self):
    root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
    executor = RepositoryToolExecutor(root, allow_edits=True)
    result = executor.invoke(
        "apply_patch",
        {"patch": """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""},
    )
    self.assertTrue(result["ok"])
    self.assertEqual((root / "app.py").read_text(), "value = 2\n")
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m unittest tests.test_provider_tools -v
```

Expected: import failure because `provider_tools` does not exist.

- [ ] **Step 3: Implement schemas and executor**

`RepositoryToolExecutor` must:

```python
READ_TOOLS = ("find_files", "search_code", "read_file")
WRITE_TOOLS = ("apply_patch", "run_tests")
MAX_TOOL_CALLS = 12
MAX_PATCH_CHARS = 120_000
```

- capture the initial dirty paths once;
- parse modified paths from `diff --git`, `---`, and `+++` headers;
- call `classify_dirty_paths(initial_dirty_paths, patch_paths)` before applying;
- run `git apply --check` before `git apply`;
- expose only fixed test commands detected from repository markers;
- return redacted `Observation.to_dict()` values;
- never accept an arbitrary shell string.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add vestahub/provider_tools.py vestahub/aci.py tests/test_provider_tools.py
git commit -m "feat(providers): add bounded repository tools"
```

### Task 3: Add the free-provider tool loop and direct dispatch path

**Files:**
- Modify: `vestahub/local_runner.py`
- Modify: `vestahub/ask.py`
- Modify: `vesta/app_state.py`
- Modify: `vestahub/gui_pipeline.py`
- Test: `tests/test_free_models.py`
- Test: `tests/test_pipeline_routing_and_safety.py`

- [ ] **Step 1: Write failing tool-loop and dispatch tests**

Create a fake HTTP sequence with:

1. an assistant `read_file` tool call;
2. an assistant `apply_patch` tool call;
3. a final assistant message.

Assert that the real temporary repository changes and the trace names both tools. Add a read-only case asserting `apply_patch` is absent and rejected. Add dispatch tests that patch `recommend_model`, `result_cache.lookup`, and `collect_evidence` to raise if an explicitly selected free model calls them.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m unittest tests.test_free_models tests.test_pipeline_routing_and_safety -v
```

Expected: free provider still receives `allow_edits=False`, has no tool loop, and enters Auto routing helpers.

- [ ] **Step 3: Implement `FreeAPIRunner.complete_with_tools`**

The method must send OpenAI-compatible `tools`, append assistant/tool messages for every call, and stop after a fixed maximum:

```python
for _ in range(max_tool_calls):
    response = self._chat(messages, tools=executor.schemas(), timeout=timeout, cancel=cancel)
    message = (response.get("choices") or [{}])[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return {"text": str(message.get("content") or "").strip(), "tool_trace": trace}
    messages.append({"role": "assistant", **message})
    for call in calls:
        observation = executor.invoke_call(call, cancel=cancel)
        trace.append(observation)
        messages.append({
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": json.dumps(observation, sort_keys=True),
        })
raise RuntimeError("Provider tool-call limit reached")
```

Share the HTTP request helper with the existing one-shot `complete` method and preserve usage/quota accounting.

- [ ] **Step 4: Implement explicit-provider execution**

Add `run_explicit_model` in `vestahub/ask.py`. It must not call `recommend_model`, `result_cache`, or `collect_evidence`. It sends the already-built provider message, records an L2 route only after success, and never stores mutating results in the prose cache.

Thread `allow_edits`, `mode`, and cancellation through `app_state.ask`, `_ask_free_model`, and `handle_gui_message`. Return actual `tool_trace` and changed files instead of hardcoded empty arrays.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 3 command. Expected: all tests pass and a fake Gemini API edits a real temporary repository.

- [ ] **Step 6: Record a local before/after dispatch benchmark**

Use a fake free runner and a temporary repository with at least 2,000 files. Record median time over 15 runs and helper call counts in the PR notes. Do not introduce a flaky wall-clock CI assertion; enforce zero Auto recommendation/cache calls and one context packet in tests.

- [ ] **Step 7: Commit Task 3**

```powershell
git add vestahub/local_runner.py vestahub/ask.py vesta/app_state.py vestahub/gui_pipeline.py tests/test_free_models.py tests/test_pipeline_routing_and_safety.py
git commit -m "feat(providers): enable bounded API model edits"
```

### Task 4: Activate Gemini CLI with truthful modes

**Files:**
- Modify: `vesta/integrations.py`
- Modify: `vesta/clients.py`
- Modify: `vestahub/agent_launch.py`
- Modify: `vesta/cli.py`
- Test: `tests/test_vesta_integrations.py`
- Test: `tests/test_clients.py`
- Test: `tests/test_agent_launch.py`

- [ ] **Step 1: Write failing Gemini activation tests**

Assert that activation:

- creates `GEMINI.md` with exactly one managed block while preserving user text;
- includes Gemini in client status;
- writes `vesta-gemini` and `vesta-gemini.ps1` wrappers;
- includes Gemini aliases when aliases are requested;
- classifies `gemini -p`/`--prompt` calls and maps Plan, Safe Auto, and Full Auto to `plan`, `auto_edit`, and `yolo`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m unittest tests.test_vesta_integrations tests.test_clients tests.test_agent_launch -v
```

Expected: missing Gemini files, status, wrappers, and classifier.

- [ ] **Step 3: Implement Gemini discovery and wrapper support**

- Add `GEMINI.md` to `_write_project_instructions`, `_planned_project_files`, `project_status`, uninstall managed-block cleanup, and `_client_specs`.
- Add `gemini` to global targets, shell wrappers, and optional aliases.
- Update user-facing restart text and CLI target help.
- Extend `classify_invocation` to parse Gemini `-p`/`--prompt`, `-m`/`--model`, and `--approval-mode`; unsupported/interactive forms must remain raw passthrough.
- Keep the raw wrapper contract fail-open when Gemini is missing or cannot be proxied.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 4 command. Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add vesta/integrations.py vesta/clients.py vestahub/agent_launch.py vesta/cli.py tests/test_vesta_integrations.py tests/test_clients.py tests/test_agent_launch.py
git commit -m "feat(integrations): activate Gemini CLI edit modes"
```

### Task 5: Full verification and PR preparation

**Files:**
- Modify as required by failures in the touched behavior only.
- Update: `docs/superpowers/specs/2026-07-06-provider-permissions-performance-design.md` only if implementation evidence requires a factual correction.

- [ ] **Step 1: Run formatting and static checks**

```powershell
python -m ruff format --check .
python -m ruff check .
python -m bandit -q -r vesta vestahub opcoding
python -m pip check
```

Expected: exit code 0.

- [ ] **Step 2: Run full Python suites**

```powershell
python -m unittest discover -s tests
python -m pytest -q
```

Expected: all tests pass with only documented platform skips.

- [ ] **Step 3: Run JavaScript and browser E2E suites**

```powershell
npm ci
npm audit --audit-level=high
npm run test:unit
npx playwright install chromium
npm run test:e2e
```

Expected: all tests pass and npm reports no high-severity audit failure.

- [ ] **Step 4: Run release/security checks**

```powershell
python -m vestahub validate
python -m pip_audit . --progress-spinner off --skip-editable
detect-secrets scan --all-files --exclude-files "(^|[\\/])\\.opcoding-tools([\\/]|$)|(^|[\\/])\\.ruff_cache([\\/]|$)|(^|[\\/])\\.opcoding([\\/]|$)|(^|[\\/])\\.vestahub([\\/]|$)|(^|[\\/])vesta[\\/]assets[\\/].*\\.png$" --exclude-lines "MORPH_API_KEY|api_key_env|api_key_present"
python scripts/smoke-install.py
```

```powershell
python -c "import json,pathlib; [json.loads(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.json') if not any(x in p.parts for x in ('.git','node_modules','build','dist'))]"
python -c "import pathlib,yaml; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8'))"
powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -LiteralPath install.ps1 -Raw)) | Out-Null"
bash -n install.sh
```

Expected: every required gate passes.

- [ ] **Step 5: Review the complete diff**

```powershell
git status --short
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

Confirm there are no secrets, generated artifacts, unrelated changes, permission regressions, or stale documentation.

- [ ] **Step 6: Commit any verification-only corrections**

```powershell
git add -- vestahub/provider_adapters.py vestahub/provider_tools.py vestahub/local_runner.py vestahub/ask.py vesta/app_state.py vestahub/gui_pipeline.py vesta/integrations.py vesta/clients.py vestahub/agent_launch.py vesta/cli.py tests/test_agent_autonomy.py tests/test_provider_adapters.py tests/test_provider_tools.py tests/test_free_models.py tests/test_pipeline_routing_and_safety.py tests/test_vesta_integrations.py tests/test_clients.py tests/test_agent_launch.py
git commit -m "test(providers): complete permission regression coverage"
```

Skip this commit when the worktree is already clean.

- [ ] **Step 7: Push and open a ready PR**

```powershell
git push -u origin codex/provider-permissions-performance
gh pr create --repo MarcoLadeira/OPai --base main --head codex/provider-permissions-performance --title "fix: honor editable modes across AI providers" --body "Fixes Vesta's contradictory read-only behavior in editable modes. Preserves acknowledged Full Auto, gives explicit free API models bounded repository tools, activates Gemini CLI instructions/mode mapping, and removes redundant explicit-provider routing/cache work. Includes mocked end-to-end provider tests; no live cloud credentials or calls are used."
```

The PR body must include root cause, mode/security behavior, benchmark evidence, tests, no-live-cloud-test disclosure, and rollback notes.

- [ ] **Step 8: Watch CI and fix only evidenced failures**

Use `gh pr checks --watch`. Inspect complete failing logs before making a repair. Push normal follow-up commits; never force-push.
