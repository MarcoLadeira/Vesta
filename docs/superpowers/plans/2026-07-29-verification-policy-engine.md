# Verification Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a deterministic, versioned verification-policy resolver whose durable effective-policy artifact is captured before edit-capable Vesta runs.

**Architecture:** Add vestahub.verification_policy as a pure resolver with strict YAML overlays, repository/task classification, source provenance, stable artifact hashing, and atomic local persistence. The GUI pipeline persists and propagates this artifact before dispatch; a new CLI command calls the same resolver without running repository commands.

**Tech Stack:** Python 3.10+, dataclasses, JSON, PyYAML through vestahub.loader, hashlib, argparse, pytest/unittest.

---

### Task 1: Establish policy-engine contracts and red tests

**Files:**
- Create: vestahub/verification_policy.py
- Create: tests/test_verification_policy.py

- [ ] **Step 1: Write failing tests for deterministic built-in resolution and human-review requirements.**

~~~python
from pathlib import Path

from vestahub.verification_policy import resolve_verification_policy


def test_python_edit_policy_is_deterministic_and_records_provenance(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    first = resolve_verification_policy(tmp_path, task="Fix parser and run tests.", mode="implement")
    second = resolve_verification_policy(tmp_path, task="Fix parser and run tests.", mode="implement")

    assert first.digest == second.digest
    assert first.status == "ready"
    assert first.sources[0].source == "builtin"
    assert {check.kind for check in first.required_checks} >= {"unit", "lint"}


def test_unautomatable_acceptance_is_visible_human_review(tmp_path: Path) -> None:
    policy = resolve_verification_policy(
        tmp_path,
        task="Update docs and have legal approve the disclosure wording.",
        mode="implement",
    )

    assert any(review.requirement == "legal approval" for review in policy.human_reviews)
~~~

- [ ] **Step 2: Run the new tests to confirm the module is missing.**

Run: python -m pytest tests/test_verification_policy.py -q

Expected: collection failure naming vestahub.verification_policy.

- [ ] **Step 3: Implement immutable schema and built-in resolution.**

~~~python
POLICY_SCHEMA_VERSION = 1
RESOLVER_SEMANTICS_VERSION = 1


@dataclass(frozen=True)
class PolicyCheck:
    check_id: str
    kind: str
    requirement: str
    command: tuple[str, ...] = ()
    reason: str = ""
    source: str = "builtin"


@dataclass(frozen=True)
class VerificationPolicy:
    status: str
    checks: tuple[PolicyCheck, ...]
    sources: tuple[PolicySource, ...]
    human_reviews: tuple[HumanReviewRequirement, ...]
    digest: str


def resolve_verification_policy(
    root: Path, *, task: str, mode: str, delivery: str = "local"
) -> VerificationPolicy:
    classification = classify_repository_and_task(root, task=task, mode=mode)
    return _finalize(_builtin_policy(classification, delivery=delivery))
~~~

The built-in policy reads only structural repository facts. It may declare a command as metadata but must never execute it.

- [ ] **Step 4: Run Task 1 tests.**

Run: python -m pytest tests/test_verification_policy.py -q

Expected: PASS.

- [ ] **Step 5: Commit the policy contract.**

~~~bash
git add vestahub/verification_policy.py tests/test_verification_policy.py
git commit -m "feat(verify): resolve built-in verification policies"
~~~

### Task 2: Add strict overlays, linting, and fail-closed decisions

**Files:**
- Modify: vestahub/verification_policy.py
- Modify: vestahub/team_policy.py
- Modify: tests/test_verification_policy.py

- [ ] **Step 1: Write failing overlay and linter tests.**

~~~python
def test_repository_overlay_cannot_downgrade_inherited_required_check(tmp_path: Path) -> None:
    (tmp_path / "vesta-verification-policy.yaml").write_text(
        "schema_version: 1\nchecks:\n  - id: unit\n    requirement: optional\n",
        encoding="utf-8",
    )
    policy = resolve_verification_policy(tmp_path, task="Fix code", mode="implement")

    assert policy.status == "blocked"
    assert any(finding.code == "required_check_downgrade" for finding in policy.findings)


def test_malformed_repository_policy_never_returns_permissive_policy(tmp_path: Path) -> None:
    (tmp_path / "vesta-verification-policy.yaml").write_text("checks: [", encoding="utf-8")
    policy = resolve_verification_policy(tmp_path, task="Fix code", mode="implement")

    assert policy.status == "blocked"
    assert policy.required_checks
~~~

- [ ] **Step 2: Run the selected tests to prove the unimplemented behaviour fails.**

Run: python -m pytest tests/test_verification_policy.py -k "downgrade or malformed" -q

Expected: FAIL because overlays are not yet loaded or linted.

- [ ] **Step 3: Load overlays and preserve source provenance.**

~~~python
def load_repository_overlay(root: Path) -> PolicyOverlay | PolicyFinding:
    path = root / "vesta-verification-policy.yaml"
    if not path.exists():
        return PolicyOverlay.empty(source="repository")
    try:
        raw = load_registry(path)
    except RegistryLoadError as exc:
        return PolicyFinding("malformed_policy", str(exc), source="repository")
    return parse_policy_overlay(raw, source="repository")


def apply_overlay(policy: _MutablePolicy, overlay: PolicyOverlay) -> None:
    for change in overlay.checks:
        inherited = policy.by_id.get(change.check_id)
        if inherited and inherited.requirement == "required" and change.requirement != "required":
            policy.block("required_check_downgrade", change.check_id, overlay.source)
            continue
        policy.apply(change, source=overlay.source)
~~~

Support an optional verification_policy mapping in the existing team-policy object. Merge team before repository; reject unknown check kinds, duplicate IDs, unsupported conditions, invalid commands, incompatible versions, and every weakening attempt.

- [ ] **Step 4: Run the policy suite.**

Run: python -m pytest tests/test_verification_policy.py -q

Expected: PASS.

- [ ] **Step 5: Commit overlay handling.**

~~~bash
git add vestahub/verification_policy.py vestahub/team_policy.py tests/test_verification_policy.py
git commit -m "feat(verify): lint policy overlays fail closed"
~~~

### Task 3: Persist reproducible effective-policy artifacts

**Files:**
- Modify: vestahub/verification_policy.py
- Modify: tests/test_verification_policy.py

- [ ] **Step 1: Write failing persistence and compatibility tests.**

~~~python
def test_persisted_artifact_is_atomic_redacted_and_round_trips(tmp_path: Path) -> None:
    policy = resolve_verification_policy(tmp_path, task="Fix auth", mode="implement")
    reference = persist_effective_policy(tmp_path, policy, task_id="task-1", run_id="run-1")

    payload = json.loads(reference.path.read_text(encoding="utf-8"))
    assert payload["digest"] == policy.digest
    assert payload["resolver_semantics_version"] == RESOLVER_SEMANTICS_VERSION
    assert "secret" not in json.dumps(payload).lower()


def test_unknown_policy_schema_blocks_historical_task_replay(tmp_path: Path) -> None:
    policy = resolve_verification_policy(
        tmp_path, task="Fix parser", mode="implement", schema_version=999
    )

    assert policy.status == "blocked"
    assert any(finding.code == "schema_incompatible" for finding in policy.findings)
~~~

- [ ] **Step 2: Run the persistence tests.**

Run: python -m pytest tests/test_verification_policy.py -k "persisted or historical" -q

Expected: FAIL because no artifact writer exists.

- [ ] **Step 3: Add canonical serialization, SHA-256 digesting, and atomic persistence.**

~~~python
def canonical_policy_bytes(policy: VerificationPolicy) -> bytes:
    return json.dumps(
        policy.payload_without_digest(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def persist_effective_policy(
    root: Path, policy: VerificationPolicy, *, task_id: str, run_id: str
) -> PolicyArtifactRef:
    target = _policy_artifact_path(root, task_id=task_id, run_id=run_id)
    payload = policy.to_dict()
    atomic_write_text(target, json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return PolicyArtifactRef(path=target, digest=policy.digest)
~~~

Validate task/run identifiers before constructing paths, persist only redacted metadata, and retain blocked artifacts for audit/reproducibility.

- [ ] **Step 4: Run policy and completion regressions.**

Run: python -m pytest tests/test_verification_policy.py tests/test_completion_contract.py -q

Expected: PASS.

- [ ] **Step 5: Commit artifact durability.**

~~~bash
git add vestahub/verification_policy.py tests/test_verification_policy.py
git commit -m "feat(verify): persist effective policy artifacts"
~~~

### Task 4: Capture policy before GUI provider dispatch

**Files:**
- Modify: vestahub/gui_pipeline.py
- Modify: tests/test_pipeline_routing_and_safety.py
- Modify: tests/test_verification_policy.py

- [ ] **Step 1: Write a failing pipeline ordering test.**

~~~python
from vestahub.verification_policy import (
    PolicyArtifactRef,
    persist_effective_policy as real_persist_effective_policy,
)

def test_edit_capable_pipeline_persists_policy_before_provider_dispatch(self) -> None:
    observed: list[str] = []

    def remember_policy(*args: object, **kwargs: object) -> PolicyArtifactRef:
        observed.append("policy")
        return real_persist_effective_policy(*args, **kwargs)

    runner = FakeAccountRunner(text="Applied the parser fix.")
    with mock.patch(
        "vestahub.gui_pipeline.persist_effective_policy", side_effect=remember_policy
    ):
        result = handle_gui_message(
            self.root,
            "Fix parser and run tests.",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=runner,
        )

    self.assertEqual(observed, ["policy"])
    self.assertTrue(result["verification_policy"]["artifact"]["digest"])
    self.assertTrue(runner.calls)
~~~

- [ ] **Step 2: Run the selected GUI test.**

Run: python -m pytest tests/test_pipeline_routing_and_safety.py -k "policy_before_provider" -q

Expected: FAIL because the pipeline does not resolve policy before dispatch.

- [ ] **Step 3: Resolve/persist policy before an edit-capable run is dispatchable.**

~~~python
verification_policy = resolve_verification_policy(
    root,
    task=message,
    mode=policy.mode.value,
    delivery="ship" if policy.mode is AgentMode.SHIP else "local",
)
policy_ref = persist_effective_policy(
    root, verification_policy, task_id=runtime.task_id, run_id=turn_id
)
if verification_policy.status == "blocked":
    return _blocked_verification_policy_response(verification_policy, policy_ref)
task_packet = build_task_packet(
    user_request=message,
    mode=policy.mode.value,
    repo=repo_context.to_dict(),
    dirty=dirty.to_dict(),
    issue={},
    constraints=(),
    allowed_actions=policy.capabilities,
    forbidden_actions=(),
    done_criteria=(),
    tests=(),
    last_failure="",
    next_action="dispatch",
    verification_policy=verification_policy.safe_summary(),
)
~~~

Pass only safe_summary() to provider-facing structures. Store the full artifact reference in workflow/checkpoint/response data and do not let provider data mutate it.

- [ ] **Step 4: Run GUI and policy tests.**

Run: python -m pytest tests/test_pipeline_routing_and_safety.py tests/test_verification_policy.py -q

Expected: PASS.

- [ ] **Step 5: Commit pipeline capture.**

~~~bash
git add vestahub/gui_pipeline.py tests/test_pipeline_routing_and_safety.py tests/test_verification_policy.py
git commit -m "feat(verify): capture policy before provider dispatch"
~~~

### Task 5: Expose the pure resolver through CLI and document it

**Files:**
- Modify: vesta/cli.py
- Modify: tests/test_positioning_and_cli.py
- Modify: README.md
- Modify: CHANGELOG.md

- [ ] **Step 1: Write a failing CLI parity test.**

~~~python
def test_verify_policy_cli_matches_pure_resolver(tmp_path: Path) -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        code = main([
            "--project", str(tmp_path), "verify", "policy", "--task", "Fix parser",
            "--mode", "implement", "--delivery", "local", "--json",
        ])

    assert code == 0
    cli_policy = json.loads(output.getvalue())
    direct = resolve_verification_policy(tmp_path, task="Fix parser", mode="implement")
    assert cli_policy["digest"] == direct.digest
~~~

- [ ] **Step 2: Run the CLI test.**

Run: python -m pytest tests/test_positioning_and_cli.py -k "verify_policy" -q

Expected: FAIL because verify policy is not registered.

- [ ] **Step 3: Implement the read-only nested CLI command.**

~~~python
def cmd_verify(args: argparse.Namespace) -> int:
    policy = resolve_verification_policy(
        _project(args.project), task=args.task, mode=args.mode, delivery=args.delivery
    )
    print_json(policy.to_dict())
    return 0 if policy.status == "ready" else 2


verify = sub.add_parser("verify", help="Resolve verification policy without running checks")
verify_sub = verify.add_subparsers(dest="verify_command", required=True)
policy = verify_sub.add_parser("policy", help="Inspect effective verification policy")
policy.add_argument("--task", required=True)
policy.add_argument("--mode", default="implement")
policy.add_argument("--delivery", default="local", choices=("local", "ship"))
policy.add_argument("--json", action="store_true")
policy.set_defaults(func=cmd_verify)
~~~

Document that this is a dry run. It does not execute checks; structured execution and terminal verdicts remain #539.

- [ ] **Step 4: Run final focused suites and static checks.**

Run: python -m pytest tests/test_verification_policy.py tests/test_completion_contract.py tests/test_pipeline_routing_and_safety.py tests/test_positioning_and_cli.py -q

Run: python -m ruff format --check .

Run: python -m ruff check .

Expected: every command exits 0.

- [ ] **Step 5: Commit surface and documentation changes.**

~~~bash
git add vesta/cli.py tests/test_positioning_and_cli.py README.md CHANGELOG.md
git commit -m "feat(verify): inspect effective policies from CLI"
~~~

### Task 6: Final review and delivery

**Files:**
- Review: vestahub/verification_policy.py
- Review: vestahub/gui_pipeline.py
- Review: vesta/cli.py
- Review: tests/test_verification_policy.py

- [ ] **Step 1: Inspect for unsafe policy weakening, secret persistence, and command-execution side effects.**

Run: git diff --check origin/main...HEAD

Run: git diff --no-ext-diff origin/main...HEAD

Expected: no whitespace errors; inspection never runs commands and no provider-controlled input can downgrade required checks.

- [ ] **Step 2: Run the complete Python suite once after focused checks pass.**

Run: python -m pytest -q

Expected: exit 0 with no collection warnings.

- [ ] **Step 3: Create a PR that closes #538 only.**

~~~bash
git push -u origin codex/issue-538-verification-policy
gh pr create --base main --title "feat(verify): resolve versioned verification policies" --body "Closes #538"
~~~

The PR description must say #539 still executes declared checks and derives evidence-based terminal verdicts.
