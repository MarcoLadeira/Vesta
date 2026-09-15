# Repository Safety and Worktree Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement #536 and #537 as one #521 foundation that blocks stale or ambiguous repository mutations and safely manages Vesta-owned isolated worktrees.

**Architecture:** A new `vestahub.repository_safety` module becomes the canonical source for repository identity, null-delimited dirty-state parsing, task-bound handles, revalidation, and mutation decisions. `repo_context` projects that model for compatibility. `vestahub.worktree_leases` manages durable, reconciled worktree leases; existing parallel-agent, provider-tool, GUI, and CLI paths consume these contracts instead of making independent safety decisions.

**Tech Stack:** Python 3.10+, standard-library dataclasses/subprocess/pathlib, Git CLI with argv-only calls, existing `atomic_io.interprocess_transaction`, `unittest`, Hypothesis test extra, Ruff.

## Global Constraints

- Start from the approved specification at `docs/superpowers/specs/2026-07-29-repository-safety-worktree-design.md`.
- All Git commands use fixed executable plus argument vectors, `shell=False`, bounded timeouts, redacted diagnostics, and no force/reset/clean operations.
- Parse repository status only from `git status --porcelain=v2 -z --branch --untracked-files=all --ignored=matching`; never infer safety from display-formatted output.
- Revalidate the task-bound handle immediately before every Vesta mutation; an unavailable, stale, ambiguous, or unknown decision blocks that mutation.
- Persist only redacted metadata through `atomic_write_text` inside `interprocess_transaction`; persistence failure is a blocked/degraded result, never a permissive fallback.
- Worktree cleanup never deletes an unowned, changed, unpushed, inconsistent, or unknown worktree; it transitions to `needs_review`.
- Run each focused suite once for its newly completed component. Run the complete test suite only in the final QA task, then run Ruff format and lint once.

---

## File Structure

- Create `vestahub/repository_safety.py`: canonical capture, status parser, identity fingerprint, handle persistence/revalidation, classification, and mutation gate.
- Modify `vestahub/repo_context.py`: compatibility projections and guarded worktree wrapper backed by the canonical service.
- Create `vestahub/worktree_leases.py`: durable worktree lease lifecycle, registry reconciliation, conflict preview, and safe cleanup.
- Modify `vestahub/parallel_agents.py`: replace ad-hoc assignment worktree creation with leases while retaining assignment APIs.
- Modify `vestahub/provider_tools.py`: capture one task handle and gate each patch/write/branch/commit/push mutation.
- Modify `vestahub/gui_pipeline.py` and `vesta/gui_web.py`: expose the same identity/assessment payload and create a task handle before edit-capable work.
- Modify `vesta/cli.py`: add read-only repository inspection and lease recovery/listing commands.
- Create `tests/test_repository_safety.py`, `tests/test_worktree_leases.py`, `tests/test_repository_safety_surfaces.py`: hermetic coverage of the new contracts.
- Modify `tests/test_agent_autonomy.py`, `tests/test_parallel_agents.py`, and `tests/test_github_push_pr_loop.py`: retain compatibility and prove existing entry points use the new guard.
- Modify `README.md` and `CHANGELOG.md`: document the repository-safety inspection/recovery contract and release-visible behavior.

---

### Task 1: Canonical repository identity and null-delimited status parser (#536)

**Files:**
- Create: `vestahub/repository_safety.py`
- Create: `tests/test_repository_safety.py`

**Interfaces:**
- Produces `RepositoryIdentity`, `DirtyState`, `RepositoryHandle`, and `HandleValidation` dataclasses.
- Produces `capture_repository_handle(path, *, task_id, run_id, max_age_seconds=300.0, git_run=subprocess.run) -> RepositoryHandle`.
- Produces `parse_porcelain_v2(raw: bytes) -> DirtyState` and `revalidate_repository_handle(handle, *, git_run=subprocess.run, now=time.time) -> HandleValidation`.

- [ ] **Step 1: Write failing identity/status tests**

```python
def test_capture_records_canonical_git_and_filesystem_identity(self):
    handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
    self.assertTrue(handle.identity.repository_id)
    self.assertEqual(handle.identity.worktree_root, self.repo.resolve())
    self.assertEqual(handle.identity.head_sha, git(self.repo, "rev-parse", "HEAD"))
    self.assertFalse(handle.identity.detached)

def test_porcelain_v2_parser_keeps_spaces_unicode_and_categories(self):
    dirty = parse_porcelain_v2(
        b"1 M. N... 100644 100644 100644 abc abc file with space.py\\0"
        b"? caf\\xc3\\xa9.txt\\0! ignored.tmp\\0u UU N... 100644 100644 100644 100644 abc abc abc conflict.txt\\0"
    )
    self.assertEqual(dirty.staged, ("file with space.py",))
    self.assertEqual(dirty.untracked, ("caf\u00e9.txt",))
    self.assertEqual(dirty.ignored, ("ignored.tmp",))
    self.assertEqual(dirty.conflicted, ("conflict.txt",))
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_repository_safety -v`

Expected: import failure because `vestahub.repository_safety` does not exist.

- [ ] **Step 3: Implement the immutable capture model**

```python
@dataclass(frozen=True)
class RepositoryIdentity:
    worktree_root: Path
    git_dir: Path
    common_git_dir: Path
    filesystem_id: tuple[int, int] | None
    remotes: tuple[tuple[str, str], ...]
    default_branch: str
    branch: str
    detached: bool
    head_sha: str
    status_fingerprint: str
    repository_id: str

@dataclass(frozen=True)
class DirtyState:
    staged: tuple[str, ...] = ()
    unstaged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    conflicted: tuple[str, ...] = ()

def parse_porcelain_v2(raw: bytes) -> DirtyState:
    return _records_to_dirty_state(raw.split(b"\0"))

def capture_repository_handle(
    path: str | Path, *, task_id: str, run_id: str,
    max_age_seconds: float = 300.0, git_run: GitRun = subprocess.run,
) -> RepositoryHandle:
    return _build_handle(_probe_repository(Path(path), git_run=git_run), task_id, run_id,
                         max_age_seconds)
```

Use `git rev-parse --show-toplevel --git-dir --git-common-dir`, `git rev-parse HEAD`, `git symbolic-ref --quiet --short HEAD`, `git remote -v`, and the mandated status command. Canonicalize paths with `resolve(strict=True)` where possible, strip remote credentials, derive the digest from filesystem/Git/remote metadata, and represent any failed required probe as a typed unavailable identity rather than a guessed one.

- [ ] **Step 4: Implement revalidation reasons**

```python
STALE_REASONS = {
    "handle_expired", "repository_missing", "repository_replaced",
    "worktree_relocated", "git_directory_changed", "common_git_directory_changed",
    "remote_changed", "branch_changed", "head_changed", "dirty_state_changed",
    "detached_head", "probe_unavailable",
}

def revalidate_repository_handle(handle, *, git_run=subprocess.run, now=time.time):
    current = capture_repository_handle(
        handle.identity.worktree_root, task_id=handle.task_id, run_id=handle.run_id,
        max_age_seconds=handle.max_age_seconds, git_run=git_run,
    )
    reasons = _identity_differences(handle, current, observed_at=now())
    return HandleValidation(fresh=not reasons, reasons=tuple(reasons), current=current)
```

Compare every safety-relevant identity field and age. Do not use `Path.samefile()` alone because it cannot identify remote or Git-directory substitution.

- [ ] **Step 5: Run the focused suite once**

Run: `python -m unittest tests.test_repository_safety -v`

Expected: all Task 1 tests pass, including deleted/replaced repository, remote rewrite, branch/HEAD/index movement, detached HEAD, no/multiple remotes, and unusual filename parser cases.

- [ ] **Step 6: Commit**

```bash
git add vestahub/repository_safety.py tests/test_repository_safety.py
git commit -m "feat(repo): capture canonical repository identity"
```

### Task 2: Evidence-based dirty classification, durable handles, and mutation gate (#536)

**Files:**
- Modify: `vestahub/repository_safety.py`
- Modify: `tests/test_repository_safety.py`

**Interfaces:**
- Consumes `RepositoryHandle`, `DirtyState`, and `revalidate_repository_handle` from Task 1.
- Produces `DirtyAssessment`, `MutationDecision`, `RepositorySafetyError`, `classify_dirty_state`, `save_repository_handle`, `load_repository_handle`, and `require_mutation_permitted`.

- [ ] **Step 1: Write failing classification/gate tests**

```python
def test_unknown_scope_and_malformed_state_fail_closed(self):
    assessment = classify_dirty_state(self.handle.dirty_state, planned_paths=None)
    self.assertEqual(assessment.outcome, "block")
    self.assertEqual(assessment.rule_id, "unknown_scope")

def test_gate_revalidates_immediately_before_write(self):
    handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
    write_text(self.repo / "src" / "other.py", "changed")
    with self.assertRaises(RepositorySafetyError) as ctx:
        require_mutation_permitted(handle, planned_paths=("src/app.py",))
    self.assertIn("dirty_state_changed", ctx.exception.decision.reasons)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_repository_safety.RepositorySafetyGateTests -v`

Expected: missing classifier/gate interfaces.

- [ ] **Step 3: Implement the policy result and persistence**

```python
@dataclass(frozen=True)
class DirtyAssessment:
    classification: str
    outcome: str
    affected_paths: tuple[str, ...]
    overlapping_paths: tuple[str, ...]
    rule_id: str
    confidence: str

def classify_dirty_state(dirty: DirtyState, *, planned_paths: Iterable[str] | None,
                         vesta_owned_paths: Iterable[str] = ()) -> DirtyAssessment:
    return _classify_paths(dirty, planned_paths=planned_paths,
                           vesta_owned_paths=vesta_owned_paths)

def require_mutation_permitted(handle: RepositoryHandle, *, planned_paths: Iterable[str],
                               operation: str, git_run=subprocess.run) -> MutationDecision:
    return _require_fresh_handle_then_safe_scope(handle, planned_paths, operation, git_run)
```

Persist handles at a deterministic filename derived from the task/run digest in
`.vestahub/repository/handles/` under a resource-specific transaction. Serialize
a redacted, versioned structure only. `save_repository_handle` and
`load_repository_handle` must raise a typed degraded error on corrupt or
unwritable state; neither may return a fresh/allowed result.

- [ ] **Step 4: Add property and no-write coverage**

```python
@given(st.lists(st.text(min_size=1, max_size=40), max_size=80))
def test_path_overlap_classifier_never_allows_unknown_scope(paths):
    dirty = DirtyState(untracked=tuple(paths))
    assert classify_dirty_state(dirty, planned_paths=None).outcome == "block"

def test_capture_and_revalidation_do_not_change_index_or_worktree(self):
    before = tree_and_index_digest(self.repo)
    revalidate_repository_handle(capture_repository_handle(self.repo, task_id="t", run_id="r"))
    self.assertEqual(tree_and_index_digest(self.repo), before)
```

Record the fixed command vector in the injected runner and assert it contains no mutation verb, hook, submodule update, or generated-file operation.

- [ ] **Step 5: Run the focused suite once**

Run: `python -m unittest tests.test_repository_safety.RepositorySafetyGateTests tests.test_repository_safety.RepositorySafetyPersistenceTests tests.test_repository_safety.RepositorySafetyPropertyTests -v`

Expected: all new gate, persistence, property, and no-write tests pass.

- [ ] **Step 6: Commit**

```bash
git add vestahub/repository_safety.py tests/test_repository_safety.py
git commit -m "feat(repo): fail closed on stale mutation state"
```

### Task 3: Compatibility projection and guarded existing context API (#536)

**Files:**
- Modify: `vestahub/repo_context.py`
- Modify: `tests/test_agent_autonomy.py`
- Modify: `tests/test_repository_safety.py`

**Interfaces:**
- Consumes Task 1 identity and Task 2 assessment/decision APIs.
- Preserves `RepoContext`, `DirtyAssessment`, `resolve_repo_context`, `active_repo_context`, `classify_dirty_paths`, and `prepare_isolated_worktree` import compatibility.
- Produces `RepoContext.handle_id`, `RepoContext.safety`, and compatibility mapping with legacy keys intact.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_legacy_context_is_projected_from_canonical_handle(self):
    context = resolve_repo_context(self.repo)
    self.assertEqual(context.path, self.repo.resolve())
    self.assertEqual(context.branch, git(self.repo, "branch", "--show-current"))
    self.assertTrue(context.handle_id)
    self.assertIn("identity", context.to_dict()["safety"])

def test_legacy_unknown_dirty_scope_no_longer_authorizes_a_write(self):
    context = resolve_repo_context(self.repo)
    assessment = classify_dirty_paths(context.dirty_paths, None)
    self.assertEqual(assessment.status, "needs_inspection")
    self.assertFalse(assessment.can_proceed)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_agent_autonomy.RepoContextTests -v`

Expected: new `handle_id` and safety assertions fail.

- [ ] **Step 3: Replace independent probes with projections**

Make `resolve_repo_context` capture a canonical handle and project legacy path/branch/remote/dirty fields. Use `atomic_write_text` plus `interprocess_transaction` for active repository state. Preserve existing callers' data shape, but add redacted `safety` payload. Map a blocked unknown assessment to legacy `needs_inspection` with `can_proceed=False`.

- [ ] **Step 4: Run the focused suite once**

Run: `python -m unittest tests.test_agent_autonomy.RepoContextTests -v`

Expected: existing repository-context behavior remains compatible and new stale/unknown behavior is fail-closed.

- [ ] **Step 5: Commit**

```bash
git add vestahub/repo_context.py tests/test_agent_autonomy.py tests/test_repository_safety.py
git commit -m "refactor(repo): project canonical safety context"
```

### Task 4: Durable worktree lease lifecycle and registry reconciliation (#537)

**Files:**
- Create: `vestahub/worktree_leases.py`
- Create: `tests/test_worktree_leases.py`

**Interfaces:**
- Consumes `RepositoryHandle`, `MutationDecision`, and `require_mutation_permitted`.
- Produces `WorktreeLease`, `WorktreeManager`, `LeaseDecision`, `WorktreeLeaseError`, `list_worktree_leases`, and `reconcile_worktree_leases`.

- [ ] **Step 1: Write failing real-Git lifecycle tests**

```python
def test_creation_records_resolved_base_then_reconciles_active_lease(self):
    lease = self.manager.create(self.handle, task_id="task-a", run_id="run-a",
                                owner="worker-a", branch="codex/task-a",
                                target=self.temp / "task-a", base="HEAD")
    self.assertEqual(lease.state, "active")
    self.assertEqual(lease.base_sha, git(self.repo, "rev-parse", "HEAD"))
    self.assertTrue(Path(lease.path).is_dir())

def test_cleanup_preserves_user_modified_worktree(self):
    lease = self.active_lease()
    write_text(Path(lease.path) / "user-note.txt", "keep me")
    result = self.manager.cleanup(lease.lease_id)
    self.assertEqual(result.state, "needs_review")
    self.assertTrue(Path(lease.path).exists())
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_worktree_leases -v`

Expected: import failure because `vestahub.worktree_leases` does not exist.

- [ ] **Step 3: Implement lease persistence and creation**

```python
@dataclass(frozen=True)
class WorktreeLease:
    lease_id: str
    task_id: str
    run_id: str
    owner: str
    repository_id: str
    path: str
    branch: str
    base_sha: str
    state: str
    created_at: str
    heartbeat_at: str
    expires_at: str
    evidence: dict[str, object]

class WorktreeManager:
    def create(self, handle: RepositoryHandle, *, task_id: str, run_id: str,
               owner: str, branch: str, target: Path, base: str) -> WorktreeLease:
        return self._create_after_preflight(handle, task_id, run_id, owner, branch, target, base)
    def reconcile(self, lease_id: str) -> WorktreeLease:
        return self._reconcile_observed_worktree(self.load(lease_id))
    def cleanup(self, lease_id: str) -> WorktreeLease:
        return self._remove_only_reconciled_pristine_worktree(self.load(lease_id))
```

Use per-repository locks, persist `creating` before `git worktree add`, resolve
the requested base to an immutable SHA before creation, enforce an Vesta branch
prefix, require a destination outside the authoritative root, and reconcile
`git worktree list --porcelain` plus filesystem identity before `active`.

- [ ] **Step 4: Implement interruption, quota, and ownership handling**

Add injected disk-usage and Git runners. On quota/disk/command failure, retain
the lease with `cleanup_failed` or `needs_review` evidence. Make a second owner,
branch, or path claim fail with `WorktreeLeaseError`. A lease file that lacks a
matching registry worktree must be `needs_review`, never recreated blindly.

- [ ] **Step 5: Run the focused suite once**

Run: `python -m unittest tests.test_worktree_leases -v`

Expected: creation, collision, crash/reconcile, missing-path, disk-failure,
user-edit, unpushed-commit, and non-owner-cleanup cases all pass.

- [ ] **Step 6: Commit**

```bash
git add vestahub/worktree_leases.py tests/test_worktree_leases.py
git commit -m "feat(worktrees): manage durable isolated leases"
```

### Task 5: Safe integration preview, recovery actions, and parallel-agent migration (#537)

**Files:**
- Modify: `vestahub/worktree_leases.py`
- Modify: `vestahub/parallel_agents.py`
- Modify: `tests/test_worktree_leases.py`
- Modify: `tests/test_parallel_agents.py`

**Interfaces:**
- Consumes active leases from Task 4.
- Produces `WorktreeManager.preview_apply(lease_id, target_handle) -> LeaseDecision`, `heartbeat`, `recover`, and `release`.
- Preserves `create_assignment_worktree` but delegates it to a lease manager.

- [ ] **Step 1: Write failing preview/recovery tests**

```python
def test_target_divergence_with_overlapping_paths_blocks_apply_preview(self):
    preview = self.manager.preview_apply(self.lease.lease_id, self.moved_target_handle)
    self.assertFalse(preview.allowed)
    self.assertEqual(preview.reason, "target_diverged_overlap")
    self.assertIn("src/shared.py", preview.paths)

def test_orphan_recovery_never_deletes_unknown_worktree(self):
    recovered = self.manager.recover()
    self.assertIn("inspect", recovered[0].recommended_actions)
    self.assertTrue(Path(recovered[0].lease.path).exists())
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_worktree_leases.WorktreePreviewTests tests.test_parallel_agents -v`

Expected: missing preview/recovery integration or old direct worktree call.

- [ ] **Step 3: Implement read-only preview and recovery**

Compare immutable base SHA, source changes, and target changes with read-only
`git diff --name-only`/`git merge-base` queries. Report deterministic potential
conflicts; only an explicitly authority-approved caller may invoke an apply
operation after a fresh target-handle gate. Reconcile every persisted lease on
recovery and return `resume`, `inspect`, or `cleanup` actions without deleting.

- [ ] **Step 4: Migrate parallel-agent creation**

Extend `AgentAssignment` only with additive lease metadata. Replace the direct
`prepare_isolated_worktree` call with a manager-backed lease creation. Keep
existing assignment planning/reconciliation APIs and make collisions surface
the lease decision instead of overwriting another task's state.

- [ ] **Step 5: Run the focused suites once**

Run: `python -m unittest tests.test_worktree_leases.WorktreePreviewTests tests.test_worktree_leases.WorktreeRecoveryTests tests.test_parallel_agents -v`

Expected: all preview, recovery, concurrent-claim, and legacy assignment tests pass.

- [ ] **Step 6: Commit**

```bash
git add vestahub/worktree_leases.py vestahub/parallel_agents.py tests/test_worktree_leases.py tests/test_parallel_agents.py
git commit -m "feat(worktrees): reconcile leases before integration"
```

### Task 6: Gate provider and Git mutation paths (#521 integration)

**Files:**
- Modify: `vestahub/provider_tools.py`
- Modify: `tests/test_github_push_pr_loop.py`
- Modify: `tests/test_repository_safety.py`

**Interfaces:**
- Consumes `capture_repository_handle` and `require_mutation_permitted`.
- `RepositoryToolExecutor` receives optional `repository_handle: RepositoryHandle | None` and captures one when edit or Git capability is enabled.
- Mutation failures return `REPOSITORY_SAFETY_BLOCKED` with redacted typed decision data.

- [ ] **Step 1: Write failing tool-boundary tests**

```python
def test_write_revalidates_handle_before_touching_file(self):
    executor = RepositoryToolExecutor(self.repo, allow_edits=True)
    git(self.repo, "checkout", "-b", "moved")
    result = executor._write_file({"path": "src/app.py", "content": "new"})
    self.assertFalse(result["ok"])
    self.assertEqual(result["error_code"], "REPOSITORY_SAFETY_BLOCKED")
    self.assertEqual((self.repo / "src" / "app.py").read_text(), "original")

def test_commit_and_push_revalidate_after_patch_was_prepared(self):
    executor = RepositoryToolExecutor(self.repo, allow_edits=True, allow_git_ops=True)
    mutate_index_externally(self.repo)
    self.assertEqual(executor._git_commit({"message": "x"})["error_code"], "REPOSITORY_SAFETY_BLOCKED")
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_github_push_pr_loop tests.test_repository_safety.RepositoryToolGateTests -v`

Expected: writes proceed because the current executor only compares initial dirty paths.

- [ ] **Step 3: Gate each mutation**

Call the canonical gate immediately before `_apply_patch`, `_write_file`, branch
creation, `_git_commit`, and `_git_push`. The planned paths must be derived from
validated patch/write/commit arguments, not provider prose. Convert a decision
failure to a structured observation; do not invoke `AgentComputerInterface` or
Git after a block. Preserve the existing one-shot push approval as an additional
gate rather than a substitute for repository safety.

- [ ] **Step 4: Run the focused suite once**

Run: `python -m unittest tests.test_github_push_pr_loop tests.test_repository_safety.RepositoryToolGateTests -v`

Expected: stale/replaced/index-changed repository cases are blocked before any file or Git mutation, while a fresh clean handle preserves normal flow.

- [ ] **Step 5: Commit**

```bash
git add vestahub/provider_tools.py tests/test_github_push_pr_loop.py tests/test_repository_safety.py
git commit -m "feat(repo): gate agent mutations on fresh identity"
```

### Task 7: GUI/CLI parity, documentation, and QA fixtures (#521 integration)

**Files:**
- Modify: `vestahub/gui_pipeline.py`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/cli.py`
- Create: `tests/test_repository_safety_surfaces.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes `RepoContext.to_dict()` and `WorktreeManager` read-only list/recover APIs.
- Produces `build_repository_safety_receipt(handle, assessment, leases) -> dict[str, object]`, `vesta repo inspect --project PROJECT_PATH --json`, `vesta repo worktrees --project PROJECT_PATH --json`, and GUI workspace fields `repository_safety` and `worktree_leases`.

- [ ] **Step 1: Write failing cross-surface tests**

```python
def test_cli_and_gui_expose_the_same_redacted_identity_and_assessment(self):
    cli = run_cli("repo", "inspect", "--project", str(self.repo), "--json")
    gui = gui_workspace_payload(self.repo)
    self.assertEqual(cli["repository_safety"]["identity"]["repository_id"],
                     gui["repository_safety"]["identity"]["repository_id"])
    self.assertEqual(cli["repository_safety"]["assessment"],
                     gui["repository_safety"]["assessment"])
    self.assertEqual(cli["repository_safety"]["receipt"],
                     gui["repository_safety"]["receipt"])

def test_cli_recovery_reports_unknown_lease_without_cleanup(self):
    payload = run_cli("repo", "worktrees", "--project", str(self.repo), "--json")
    self.assertEqual(payload["leases"][0]["state"], "needs_review")
    self.assertTrue(Path(payload["leases"][0]["path"]).exists())
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests.test_repository_safety_surfaces -v`

Expected: the `repo` CLI subcommand and shared GUI safety payload do not exist.

- [ ] **Step 3: Wire shared projections and document behavior**

Capture/persist the task handle before edit-capable GUI work and include the
redacted handle/assessment in task packets and workspace responses. Add only
read-only CLI inspection/list/recovery reporting; do not expose a destructive
cleanup shortcut. Document blocked/stale behavior, inspection, recovery, and
the invariant that user worktrees are preserved. Add a concise changelog entry.

- [ ] **Step 4: Run focused surface tests once**

Run: `python -m unittest tests.test_repository_safety_surfaces tests.test_github_gui -v`

Expected: CLI and GUI render the same canonical data and neither leaks remote credentials or authorizes cleanup.

- [ ] **Step 5: Commit**

```bash
git add vestahub/gui_pipeline.py vesta/gui_web.py vesta/cli.py tests/test_repository_safety_surfaces.py README.md CHANGELOG.md
git commit -m "feat(repo): expose shared repository safety state"
```

### Task 8: Final QA, review, and release-ready PR preparation

**Files:**
- Modify only if validation reveals an issue: files owned by Tasks 1–7

**Interfaces:**
- Validates the complete #521 path from identity capture through worktree recovery and guarded mutation.

- [ ] **Step 1: Run the single complete test suite**

Run: `python -m unittest discover -s tests`

Expected: all tests pass; record exact pass/skip totals and investigate any failure before proceeding.

- [ ] **Step 2: Run formatting and lint once**

Run: `python -m ruff format --check . && python -m ruff check . && git diff --check origin/main...HEAD`

Expected: formatting, lint, and whitespace checks pass.

- [ ] **Step 3: Review the safety diff and prepare the evidence summary**

Run: `git diff --check origin/main...HEAD` and `git diff --stat origin/main...HEAD`

Expected: only #521 scope files are present; PR notes name #536/#537, exact test evidence, and any platform-limited symlink fixture skips.

- [ ] **Step 4: Commit any QA repair and publish**

```bash
git add -- vestahub/repository_safety.py vestahub/repo_context.py vestahub/worktree_leases.py vestahub/parallel_agents.py vestahub/provider_tools.py vestahub/gui_pipeline.py vesta/gui_web.py vesta/cli.py tests/test_repository_safety.py tests/test_worktree_leases.py tests/test_repository_safety_surfaces.py tests/test_agent_autonomy.py tests/test_parallel_agents.py tests/test_github_push_pr_loop.py README.md CHANGELOG.md
git commit -m "fix(repo): address repository safety QA findings"
git push -u origin codex/issue-521-repository-safety
gh pr create --base main --head codex/issue-521-repository-safety --title "feat(repo): enforce repository safety and worktree leases" --body "Closes #521\n\nImplements #536 and #537."
```

Create no duplicate PR. Observe PR reviews/checks, repair only evidence-backed
findings, revalidate the remote head immediately before merge, and merge only
when GitHub policy is satisfied.
