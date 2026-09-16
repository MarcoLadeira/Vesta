# Vesta Alpha Trust Programme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Vesta's free alpha fail closed around provider permissions, reject unsupported edit paths honestly, and count every model call exactly once.

**Architecture:** Keep `AgentPolicy` as the shared intent source, add request-exact publish capabilities, and enforce provider limitations before launching native CLIs. Keep route decisions as comparison evidence while making model-call events the only spend source; propagate typed capability mismatches through the shared GUI/CLI core without receipts or false diff-review transitions.

**Tech Stack:** Python 3.10+, unittest/pytest, dataclasses, append-only JSONL ledgers, PySide/QWebChannel backend contracts, Ruff, Bandit.

---

## File responsibilities

- `vestahub/agent_policy.py`: resolve the current request into exact read, edit, publish, and ship capabilities.
- `vestahub/accounts.py`: construct native account CLI commands; Copilot remains read-only until it has granular tool controls.
- `vesta/app_state.py`: shared provider dispatch and typed capability gating for GUI and CLI.
- `vestahub/ask.py`: local/cache execution; mutation requests must not use a prose answer cache or claim edit success.
- `vestahub/gui_pipeline.py`: turn typed provider outcomes into honest runtime, receipt, and workflow state.
- `vestahub/budget.py`: calculate spent budget from actual/estimated model-call events only.
- `vestahub/ledger.py`: aggregate routing comparisons separately from spend.
- `tests/test_agent_autonomy.py`: intent/capability decision table.
- `tests/test_copilot_connector.py`: Copilot command and dispatch safety contract.
- `tests/test_ai_model_bugfixes.py`: direct local-runner regression contract.
- `tests/test_pipeline_routing_and_safety.py`: GUI pipeline outcome parity and no-false-success behavior.
- `tests/test_free_models.py`: route-record ownership between app state and GUI.
- `tests/test_budget_firewall.py`: budget spend semantics.
- `tests/test_cost_ledger.py`: ledger aggregation semantics.
- `tests/test_savings_honesty.py`: end-to-end free-call event multiplicity.
- `CHANGELOG.md`: user-visible trust and accounting changes.
- `docs/PRODUCT_IDENTITY.md`: free-launch and cost-truth boundary.

### Task 1: Make publish capabilities request-exact

**Files:**
- Modify: `tests/test_agent_autonomy.py`
- Modify: `vestahub/agent_policy.py`

- [ ] **Step 1: Add failing capability-decision tests**

Add these methods to `AgentPolicyTests` in `tests/test_agent_autonomy.py`:

```python
    def test_plain_implementation_does_not_authorize_remote_git_operations(self):
        policy = resolve_agent_policy("Fix the parser and run the tests.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertTrue(policy.allows("commit"))
        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_explicit_push_authorizes_publish_without_merge(self):
        policy = resolve_agent_policy("Fix the parser, commit it, and push the branch.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("push"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_explicit_pr_authorizes_publish_without_merge(self):
        policy = resolve_agent_policy("Fix the parser and open a pull request.")

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("push"))
        self.assertTrue(policy.allows("create_pr"))
        self.assertFalse(policy.allows("merge_pr"))

    def test_explicit_publish_prohibition_keeps_remote_git_disabled(self):
        policy = resolve_agent_policy(
            "Fix the parser locally. Do not push or open a pull request."
        )

        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)
        self.assertTrue(policy.allows("edit_files"))
        self.assertFalse(policy.allows("push"))
        self.assertFalse(policy.allows("create_pr"))
```

- [ ] **Step 2: Run the policy tests and verify the plain implementation case fails**

Run:

```powershell
python -m pytest tests/test_agent_autonomy.py::AgentPolicyTests -q
```

Expected: `test_plain_implementation_does_not_authorize_remote_git_operations` fails because IMPLEMENT currently contains `push` and `create_pr`.

- [ ] **Step 3: Split edit, publish, and ship capabilities**

Replace the capability constants near the top of `vestahub/agent_policy.py` with:

```python
_READ = frozenset({"read_files", "search_code", "inspect_git"})
_IMPLEMENT = _READ | frozenset(
    {
        "edit_files",
        "create_files",
        "run_tests",
        "create_branch",
        "commit",
    }
)
_PUBLISH = frozenset({"push", "create_pr"})
_SHIP = _IMPLEMENT | _PUBLISH | frozenset({"merge_pr"})

_CAPABILITIES = {
    AgentMode.EXPLAIN: _READ,
    AgentMode.REVIEW: _READ,
    AgentMode.IMPLEMENT: _IMPLEMENT,
    AgentMode.SHIP: _SHIP,
    AgentMode.DANGEROUS: _READ,
}
```

Add this expression after `_SHIP_SIGNAL`:

```python
_PUBLISH_SIGNAL = re.compile(
    r"\b(?:push(?:\s+(?:the\s+)?branch)?|create\s+(?:a\s+)?pr|make\s+(?:a\s+)?pr|"
    r"open\s+(?:a\s+)?pr|pull\s+request)\b",
    re.IGNORECASE,
)
```

Add a clause-aware positive publish helper next to `_last_positive_write` so a
request such as "do not push or open a PR" cannot grant either capability:

```python
def _last_positive_publish(text: str) -> int:
    matches = []
    for match in _PUBLISH_SIGNAL.finditer(text):
        clause = re.split(r"[.;\n]", text[: match.start()])[-1]
        if re.search(
            r"\b(?:do\s+not|don't|never|without|avoid|forbid|must\s+not)\b",
            clause,
            re.IGNORECASE,
        ):
            continue
        matches.append(match)
    return matches[-1].start() if matches else -1
```

In `resolve_agent_policy`, calculate `publish_at`, include it in the latest write decision, and derive an immutable capability set before returning:

```python
    ship_at = _last_match(_SHIP_SIGNAL, text)
    publish_at = _last_positive_publish(text)
    implement_at = _last_positive_write(text)
    read_only_at = _last_match(_READ_ONLY_SIGNAL, text)
    review_at = _last_match(_REVIEW_SIGNAL, text)
    explain_at = _last_match(_EXPLAIN_SIGNAL, text)

    latest_write = max(ship_at, publish_at, implement_at)
    latest_read_only = max(read_only_at, review_at, explain_at)
```

Keep the existing mode selection, then replace the final `AgentPolicy` construction with:

```python
    capabilities = _CAPABILITIES[mode]
    if mode is AgentMode.IMPLEMENT and publish_at >= 0 and publish_at > read_only_at:
        capabilities = capabilities | _PUBLISH

    return AgentPolicy(
        mode,
        capabilities,
        merge_requirements=merge_requirements,
        rationale=(
            "Latest explicit write request controls."
            if latest_write > latest_read_only
            else "Latest explicit read-only request controls."
        ),
    )
```

- [ ] **Step 4: Run policy and provider-contract tests**

Run:

```powershell
python -m pytest tests/test_agent_autonomy.py tests/test_provider_git_tools.py tests/test_provider_adapters.py tests/agent_evals/test_vestabench.py -q
```

Expected: all tests pass; explicit PR/ship behavior remains available while a plain implementation has no remote Git capabilities.

- [ ] **Step 5: Commit the exact-capability change**

```powershell
git add vestahub/agent_policy.py tests/test_agent_autonomy.py
git commit -m "fix(policy): require explicit publish intent"
```

### Task 2: Fail closed for Copilot edit modes

**Files:**
- Modify: `tests/test_copilot_connector.py`
- Modify: `vestahub/accounts.py`
- Modify: `vesta/app_state.py`
- Modify: `vestahub/gui_pipeline.py`

- [ ] **Step 1: Replace unsafe Copilot command expectations with failing safety tests**

Replace `test_safe_auto_mode_allows_all_tools` and
`test_full_auto_mode_allows_all_tools` in `tests/test_copilot_connector.py` with:

```python
    def test_safe_auto_command_never_enables_unbounded_tools(self):
        runner = _copilot_runner()
        cmd = runner.build_command("fix the parser", mode="safe-auto")

        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("Do not modify files", cmd[-1])

    def test_full_auto_command_never_enables_unbounded_tools(self):
        runner = _copilot_runner()
        cmd = runner.build_command("build a feature", mode="full-auto")

        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("Do not modify files", cmd[-1])
```

Add this method to `CopilotAppStateAskTests`:

```python
    def test_copilot_edit_request_fails_before_runner_launch(self):
        from vesta import app_state as A
        from vestahub.ledger import read_events

        fake = FakeAccountRunner(account_id="copilot", text="should not run")
        result = A.ask(
            self.root,
            "Fix app.py",
            "account:copilot:gpt-5.2",
            allow_edits=True,
            mode="safe-auto",
            account_runner=fake,
        )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["provider"], "copilot")
        self.assertEqual(result["capability"], "edit_files")
        self.assertEqual(fake.calls, [])
        self.assertEqual(read_events(self.root), [])
```

- [ ] **Step 2: Run the Copilot tests and verify failures**

Run:

```powershell
python -m pytest tests/test_copilot_connector.py::CopilotBuildCommandTests tests/test_copilot_connector.py::CopilotAppStateAskTests -q
```

Expected: the two command tests fail because `--allow-all-tools` is present, and the dispatch test fails because the fake runner is called.

- [ ] **Step 3: Make every Vesta-generated Copilot command read-only**

Replace the Copilot branch of `AccountRunner.build_command` in
`vestahub/accounts.py` with:

```python
        if self.account_id == "copilot":
            cmd = [self.cli_path, "-s", "--no-ask-user"]
            if self.model:
                cmd += [f"--model={self.model}"]
            prompt = (
                "Do not modify files or run mutating commands. "
                "Return an answer or patch plan only.\n\n" + prompt
            )
            cmd += ["-p", prompt]
            return cmd
```

- [ ] **Step 4: Reject Copilot edits in the shared app-state dispatcher**

In `_ask_account` in `vesta/app_state.py`, immediately after the panic-mode
gate, add:

```python
    if account_id == "copilot" and allow_edits:
        return {
            "status": "capability_mismatch",
            "provider": "copilot",
            "capability": "edit_files",
            "reason": (
                "Vesta cannot safely grant Copilot edit access because its "
                "non-interactive CLI currently exposes only an all-tools bypass."
            ),
            "hint": (
                "Switch to Ask or Plan, or choose a provider with enforceable "
                "workspace-scoped edit controls."
            ),
        }
```

- [ ] **Step 5: Propagate capability mismatches without receipts**

In `_decorate` in `vestahub/gui_pipeline.py`, treat the typed mismatch as a
blocked workflow:

```python
        elif (
            status.startswith("needs_")
            or status in {"blocked", "capability_mismatch"}
        ):
```

In the account-provider branch, immediately after the cancelled-result block
and before building a receipt, add:

```python
        if result.get("status") == "capability_mismatch":
            answer = str(result.get("hint") or result.get("reason") or "")
            _phase_close("warning", "Provider cannot enforce this edit mode")
            _emit(
                "capability_mismatch",
                "warning",
                "Choose a tool-capable provider",
                metadata={"provider": provider, "capability": "edit_files"},
            )
            return _decorate(
                {
                    "status": "capability_mismatch",
                    "answer": answer,
                    "tool_trace": tool_trace,
                    "receipt": {},
                    "changed_files": [],
                    "warnings": [result.get("reason") or answer],
                    "next_actions": [result.get("hint") or answer],
                    "raw_result": result,
                }
            )
```

- [ ] **Step 6: Run Copilot and shared pipeline tests**

Run:

```powershell
python -m pytest tests/test_copilot_connector.py tests/test_agent_autonomy.py tests/test_paid_savings_truth.py tests/test_pipeline_routing_and_safety.py -q
```

Expected: all tests pass, no generated Copilot command contains the bypass, and the mismatch creates no ledger spend or receipt.

- [ ] **Step 7: Commit the Copilot fail-closed change**

```powershell
git add vestahub/accounts.py vesta/app_state.py vestahub/gui_pipeline.py tests/test_copilot_connector.py
git commit -m "fix(providers): fail closed for Copilot edits"
```

### Task 3: Reject prose-only local edits instead of reporting success

**Files:**
- Modify: `tests/test_ai_model_bugfixes.py`
- Modify: `tests/test_pipeline_routing_and_safety.py`
- Modify: `vestahub/ask.py`
- Modify: `vesta/app_state.py`
- Modify: `vestahub/gui_pipeline.py`

- [ ] **Step 1: Add a failing shared-core local-edit test**

Add this test to `tests/test_ai_model_bugfixes.py` in the local-answer test
class that already exercises `run_ask`:

```python
    def test_prose_only_local_runner_rejects_edit_before_cache_or_model_call(self):
        from vestahub.ask import run_ask

        class ProseOnlyRunner:
            name = "ollama"
            model = "qwen-coder"

            def __init__(self):
                self.called = False

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                self.called = True
                return "I changed app.py"

        runner = ProseOnlyRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("vestahub.ask.result_cache.lookup") as cache_lookup:
                result = run_ask(
                    root,
                    "Fix app.py",
                    runner=runner,
                    record=False,
                    allow_edits=True,
                )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["capability"], "edit_files")
        self.assertFalse(runner.called)
        cache_lookup.assert_not_called()
```

- [ ] **Step 2: Add a failing GUI outcome test**

Add this method to the selected-local-model tests in
`tests/test_pipeline_routing_and_safety.py`:

```python
    def test_local_edit_capability_mismatch_is_not_answered_or_receipted(self):
        mismatch = {
            "status": "capability_mismatch",
            "capability": "edit_files",
            "reason": "The selected local runner cannot edit files safely.",
            "hint": "Choose a provider with bounded repository tools.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("vestahub.ask.run_ask", return_value=mismatch) as run:
                result = handle_gui_message(
                    root,
                    "Fix app.py",
                    model_id="ollama:qwen-coder",
                    mode="safe-auto",
                )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["receipt"], {})
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["workflow"]["phase"], "blocked")
        self.assertTrue(run.call_args.kwargs["allow_edits"])
```

- [ ] **Step 3: Run both tests and verify failure**

Run:

```powershell
python -m pytest tests/test_ai_model_bugfixes.py tests/test_pipeline_routing_and_safety.py -k "prose_only_local_runner_rejects_edit or local_edit_capability_mismatch" -q
```

Expected: `run_ask` rejects the unknown `allow_edits` argument and the GUI path returns a non-empty prospective receipt.

- [ ] **Step 4: Add mutation intent to the local shared core**

Add `allow_edits: bool = False` to the keyword-only arguments of `run_ask` in
`vestahub/ask.py`. Preserve the cheap cache-first read path, skip cached answers
for mutation requests, and return a typed mismatch when a local runner is
present:

```python
    active = runner
    active_checked = runner is not None
    if not allow_edits:
        cached = result_cache.lookup(root, task, model_id)
        if cached is not None:
            if record:
                _record(root, task, tier, cache_hit=True)
            return {
                **base,
                "status": "cache_hit",
                "free": True,
                "source": "cache",
                "answer": cached.get("answer", ""),
            }

    if not active_checked:
        active = detect_local_runner(root)
    active_available = active is not None and active.available()

    if allow_edits and active_available:
        return {
            **base,
            "status": "capability_mismatch",
            "capability": "edit_files",
            "provider": str(getattr(active, "name", "local")),
            "reason": (
                "The selected local runner can answer, but Vesta has no bounded "
                "repository-tool adapter for it yet."
            ),
            "hint": (
                "Switch to Ask or Plan, or choose a provider with bounded "
                "repository tools for edits."
            ),
        }

    if active_available:
        # retain the existing cancellable prose-completion body
```

Remove the old duplicate `active` assignment and unconditional cache block.
When no local runner is available, retain the existing `no_local_model` result
so Auto can still offer a confirmed fallback.

- [ ] **Step 5: Propagate edit intent from both callers**

In the local branch of `vesta.app_state.ask`, call:

```python
    return run_ask(
        root,
        task,
        runner=runner,
        record=True,
        allow_cloud=allow_cloud,
        allow_edits=allow_edits,
        cancel=cancel,
    )
```

In the local GUI branch in `vestahub/gui_pipeline.py`, add
`allow_edits=allow_edits` to the existing `run_ask` call.

- [ ] **Step 6: Return local mismatches without route, receipt, or diff review**

Immediately after the cancelled-result block in the local GUI branch, add:

```python
    if result.get("status") == "capability_mismatch":
        answer = str(result.get("hint") or result.get("reason") or "")
        _phase_close("warning", "Local model cannot enforce this edit mode")
        _emit(
            "capability_mismatch",
            "warning",
            "Choose a tool-capable provider",
            metadata={"provider": result.get("provider") or "local"},
        )
        return _decorate(
            {
                "status": "capability_mismatch",
                "answer": answer,
                "tool_trace": tool_trace,
                "receipt": {},
                "changed_files": [],
                "warnings": [result.get("reason") or answer],
                "next_actions": [result.get("hint") or answer],
                "raw_result": result,
            }
        )
```

- [ ] **Step 7: Run local-answer, cache, cancellation, and pipeline tests**

Run:

```powershell
python -m pytest tests/test_ai_model_bugfixes.py tests/test_pipeline_routing_and_safety.py tests/test_local_cancel.py tests/test_desktop_gui.py tests/test_savings_honesty.py -q
```

Expected: all tests pass; Ask/Plan remain compatible, cancellation remains real,
and local edit intent cannot become an answered result.

- [ ] **Step 8: Commit the truthful local outcome change**

```powershell
git add vestahub/ask.py vesta/app_state.py vestahub/gui_pipeline.py tests/test_ai_model_bugfixes.py tests/test_pipeline_routing_and_safety.py
git commit -m "fix(local): reject unsupported edit execution"
```

### Task 4: Record one route and one spend event per free-model call

**Files:**
- Modify: `tests/test_free_models.py`
- Modify: `tests/test_savings_honesty.py`
- Modify: `tests/test_budget_firewall.py`
- Modify: `tests/test_cost_ledger.py`
- Modify: `vesta/app_state.py`
- Modify: `vestahub/gui_pipeline.py`
- Modify: `vestahub/budget.py`
- Modify: `vestahub/ledger.py`

- [ ] **Step 1: Assert GUI ownership of free-model route recording**

In `test_gui_pipeline_dispatches_confirmed_free_model` in
`tests/test_free_models.py`, add:

```python
        self.assertFalse(ask_mock.call_args.kwargs["record_route"])
```

In `test_ask_free_dispatches_with_allow_cloud`, add:

```python
        self.assertTrue(run_explicit.call_args.kwargs["record"])
```

- [ ] **Step 2: Add an end-to-end event-multiplicity regression test**

Add this method to `RecordAfterOutcomeTests` in `tests/test_savings_honesty.py`:

```python
    def test_gui_free_call_records_one_route_and_one_spend_event(self):
        class FakeFreeRunner:
            name = "free-api"
            model = "gemini-3.1-flash-lite"
            last_usage = {
                "tokens": 120,
                "input_tokens": 80,
                "output_tokens": 40,
                "measurement": "provider",
            }

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                return "Free answer"

        selected = "free:gemini:gemini-3.1-flash-lite"
        with mock.patch(
            "vestahub.local_runner.runner_for_model", return_value=FakeFreeRunner()
        ):
            result = handle_gui_message(
                self.root,
                "Explain the parser",
                model_id=selected,
                mode="ask",
                allow_cloud=True,
            )

        events = read_events(self.root)
        routes = [event for event in events if event.get("event_type") == "route_decision"]
        calls = [event for event in events if event.get("event_type") == "model_call"]
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(routes), 1)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(
            budget_status(self.root)["spent"]["today_usd"],
            calls[0]["estimated_actual_usd"],
            places=6,
        )
```

- [ ] **Step 3: Add explicit budget and ledger aggregation tests**

Add to `BudgetConfigTests` in `tests/test_budget_firewall.py`:

```python
    def test_route_comparison_estimate_is_not_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "route", model_tier="L2")
            before_call = budget_status(root)
            record_model_call(
                root,
                "call",
                model_tier="L2",
                provider_type="free_api",
                tokens=100,
                confirmed=True,
            )
            after_call = budget_status(root)

        self.assertEqual(before_call["spent"]["today_usd"], 0.0)
        self.assertGreater(after_call["spent"]["today_usd"], 0.0)
```

Extend `test_summarize_aggregates_tiers_and_cloud_avoidance` in
`tests/test_cost_ledger.py` with:

```python
        self.assertGreater(summary["route_estimated_actual_usd"], 0.0)
        expected_spend = sum(
            event.get("estimated_actual_usd", 0.0)
            for event in read_events(root)
            if event.get("event_type") == "model_call"
        )
        self.assertAlmostEqual(
            summary["estimated_actual_spend_usd"], expected_spend, places=6
        )
```

- [ ] **Step 4: Run the new tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_free_models.py tests/test_savings_honesty.py -k "dispatches_with_allow_cloud or pipeline_dispatches_confirmed or gui_free_call_records_one_route" -q
python -m pytest tests/test_budget_firewall.py::BudgetConfigTests tests/test_cost_ledger.py::LedgerTests::test_summarize_aggregates_tiers_and_cloud_avoidance -q
```

Expected: the new `record_route` argument assertions fail, the integrated free
call has two route events, and route estimates appear in budget spend.

- [ ] **Step 5: Add route-record ownership to app-state dispatch**

Add `record_route: bool = True` to the keyword-only parameters of
`vesta.app_state.ask` and `_ask_free_model`. Forward it from `ask`:

```python
        return _ask_free_model(
            root,
            task,
            model_choice,
            allow_cloud=allow_cloud,
            allow_edits=allow_edits,
            mode=mode,
            record_route=record_route,
            cancel=cancel,
        )
```

In `_ask_free_model`, pass it to the explicit runner:

```python
    result = run_explicit_model(
        project_root,
        task,
        runner=runner,
        selected_model_id=model_id,
        allow_edits=allow_edits,
        mode=mode or ("safe-auto" if allow_edits else "ask"),
        record=record_route,
        cancel=cancel,
    )
```

In the free-model GUI call in `vestahub/gui_pipeline.py`, add:

```python
            record_route=False,
```

The GUI then remains the sole owner of `_record_gui_route`, while direct CLI
calls keep their existing route event.

- [ ] **Step 6: Make model calls the only budget-spend events**

In `vestahub/budget.py`, change `_spent` to ignore route comparisons:

```python
    for event in read_events(project_root):
        if event.get("event_type") != EVENT_MODEL_CALL:
            continue
```

Remove the now-unused `EVENT_ROUTE` import from the module.

- [ ] **Step 7: Separate route estimates from spend in ledger summaries**

In the summary dictionary in `summarize_ledger` in `vestahub/ledger.py`, replace
the current combined spend expression with:

```python
        "route_estimated_actual_usd": _sum(routes, "estimated_actual_usd"),
        "estimated_actual_spend_usd": _sum(
            model_calls, "estimated_actual_usd"
        ),
```

This is a read-time semantic correction. Keep the append-only JSONL schema and
all historical events unchanged.

- [ ] **Step 8: Run all accounting and receipt suites**

Run:

```powershell
python -m pytest tests/test_free_models.py tests/test_budget_firewall.py tests/test_cost_ledger.py tests/test_paid_savings_truth.py tests/test_savings_honesty.py tests/test_receipt.py tests/test_proxy.py tests/test_cost_telemetry.py -q
```

Expected: all tests pass; routes still drive savings comparisons and model calls
alone drive spend and budget consumption.

- [ ] **Step 9: Commit the single-entry accounting change**

```powershell
git add vesta/app_state.py vestahub/gui_pipeline.py vestahub/budget.py vestahub/ledger.py tests/test_free_models.py tests/test_savings_honesty.py tests/test_budget_firewall.py tests/test_cost_ledger.py
git commit -m "fix(cost): count model spend exactly once"
```

### Task 5: Document the free-launch trust boundary

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/PRODUCT_IDENTITY.md`

- [ ] **Step 1: Add the trust patch to the Alpha.2 changelog**

Under `### Security & privacy` in `CHANGELOG.md`, add:

```markdown
- Safe Auto now fails closed when a native provider cannot enforce granular
  edit permissions; Vesta never enables Copilot's unbounded all-tools bypass.
- Push and pull-request authority now requires an explicit current request and
  is no longer implied by an ordinary implementation task.
```

Under `### Cost & honesty`, add:

```markdown
- Route comparisons and model spend are now separate ledger concepts: one
  provider call creates one spend event, and budgets never add route estimates
  to actual spend.
- Local runners without bounded repository tools return an actionable
  capability mismatch for edit requests instead of a false success.
```

- [ ] **Step 2: Update the product identity for a fully free launch**

Replace the final revenue sentence in `docs/PRODUCT_IDENTITY.md` with:

```markdown
## Launch and future pricing

Vesta launches fully free. The alpha optimizes for trust, successful tasks, and
measured cost reduction rather than artificial feature gates. Pricing will be
introduced gradually only after real usage identifies future capabilities that
create durable paid value; core safety and honest accounting will never be paid
upgrades.
```

- [ ] **Step 3: Verify documentation consistency and formatting**

Run:

```powershell
rg -n "Founding Pro|Free / Plus / Unlimited|offline license|paid launch" README.md docs -g "*.md"
git diff --check
```

Expected: remaining monetization references are historical or explicitly
post-launch; changed files contain no whitespace errors.

- [ ] **Step 4: Commit the product-boundary documentation**

```powershell
git add CHANGELOG.md docs/PRODUCT_IDENTITY.md
git commit -m "docs: make the free launch trust boundary explicit"
```

### Task 6: Run full verification and review the complete diff

**Files:**
- Verify: all files changed in Tasks 1–5

- [ ] **Step 1: Run the focused trust suite**

```powershell
python -m pytest tests/test_agent_autonomy.py tests/test_provider_git_tools.py tests/test_copilot_connector.py tests/test_ai_model_bugfixes.py tests/test_pipeline_routing_and_safety.py tests/test_free_models.py tests/test_budget_firewall.py tests/test_cost_ledger.py tests/test_paid_savings_truth.py tests/test_savings_honesty.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the canonical Python suite**

```powershell
python -B -m unittest discover -s tests
```

Expected: at least the 1,503-test baseline passes with only the two established
skips plus the new regression tests.

- [ ] **Step 3: Run static and registry gates**

```powershell
python -B -m ruff format --check .
python -B -m ruff check --no-cache .
python -B -m bandit -r vesta vestahub opcoding -q
python -B -m vestahub validate
git diff --check origin/main...HEAD
```

Expected: all commands exit 0. Existing Bandit warnings about malformed nosec
comments may print, but there are no Bandit findings.

- [ ] **Step 4: Run local GUI and CLI smoke checks**

```powershell
python -B -m vesta version
python -B -m vesta gui --once
python -B -m vestahub validate
```

Expected: version reports `0.2.0a2`; GUI once returns JSON with `"ok": true`;
registry validation passes.

- [ ] **Step 5: Inspect branch scope and secrets**

```powershell
git status --short --branch
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
python -m detect_secrets scan --all-files --exclude-files "(^|[\\/])\.vestahub([\\/]|$)|(^|[\\/])\.ruff_cache([\\/]|$)"
```

Expected: only trust-programme code, tests, and documentation are present; no
new secret findings are introduced.

- [ ] **Step 6: Review the final diff against the design**

Confirm all of the following directly from code and test output:

```text
Copilot all-tools production path: absent
Plain implementation push/PR capability: absent
Explicit PR/ship capability: preserved
Unsupported local edit success: impossible
Free GUI call route events: exactly one
Free GUI call spend events: exactly one
Budget source: model_call only
Ledger migration: none
```

Do not create the pull request until each line is proven.
