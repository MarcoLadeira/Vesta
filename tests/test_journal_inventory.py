"""#613 Stage 1: every durable writer is inventoried and has a migration owner.

The issue asks Stage 1 to "list all durable runtime/status/cost/approval/history
files and direct writes" and "define ownership and migration mapping". A
document would answer that once and then quietly rot: OPai has 62 modules that
put bytes on disk, and the next one is added without anyone rereading the list.

So the inventory lives here instead, as a classification the tree is checked
against. A module that starts writing durably and is not classified fails this
test, which forces the question #613 actually cares about -- is this runtime
truth the journal must own, or is it a cache nobody needs to replay?

Deliberately AST-based rather than a filename convention: what matters is
whether a module *calls* a durable-write primitive, not what it is called.

The three classes are decision-shaped, not descriptive:

``JOURNAL_OWNED``
    Runtime, status, cost, approval, lease or history truth. A crash between
    the external effect and this write is exactly the contradiction #613
    exists to remove, so these migrate to the journal (Stages 2-7).

``PROJECTION_OR_EXPORT``
    Derived views and support artifacts. Deletable and rebuildable; they must
    never be the only record of anything, but they do not need to migrate --
    #613's own words, "current GUI/CLI/history views are projections that can
    be deleted and rebuilt".

``NOT_RUNTIME_STATE``
    Caches, preferences, generated scaffolding, benchmark and doc output.
    Explicitly a non-goal: "migrating every cache/preferences file into the
    runtime journal".
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("opai", "opaihub", "opcoding")

#: Calls that put bytes on disk durably enough to outlive the process.
DURABLE_WRITE_CALLS = frozenset(
    {
        "atomic_write_text",
        "write_text",
        "write_bytes",
        "interprocess_transaction",
    }
)

#: Runtime truth the journal must own. Ordered by #613's schema sections so a
#: reader can map each module to the table that will absorb it.
JOURNAL_OWNED = {
    # runs / events / leases
    "opaihub/run_journal.py": "events — append-only journal this issue generalises",
    # Not a durable writer in its own right: it mirrors a record another
    # module has already decided and already persisted, into that module's own
    # journal. Classified here rather than as a projection because what it
    # writes *is* Stage 2's canonical event — it simply never originates one.
    "opaihub/shadow_journal.py": "events — Stage 2 shadow mirror shared by every JOURNAL_OWNED record",
    "opaihub/workflow_runner.py": "runs — run/step transitions",
    "opaihub/background_runs.py": "runs — background run records and notifications",
    "opaihub/agent_runtime.py": "runs — agent process state",
    "opaihub/owner_lease.py": "leases — ownership and fencing",
    "opaihub/worktree_leases.py": "leases — worktree ownership",
    "opaihub/session_registry.py": "leases — cross-process active provider sessions",
    "opaihub/parallel_agents.py": "runs — concurrent agent slots",
    "opaihub/scheduler.py": "runs — scheduled work",
    # operations / approvals
    "opaihub/idempotency.py": "operations -- exact-once external-effect claims",
    "opaihub/checkpoints.py": "operations — run checkpoints",
    "opaihub/audit.py": "approvals — audit trail",
    "opaihub/github_connector.py": "operations — GitHub delivery",
    "opaihub/repository_safety.py": "operations — repository mutation guards",
    "opai/integrations.py": "approvals — connected-service consent",
    "opai/update/storage.py": "operations — packaged update state and fencing",
    # cost
    "opaihub/ledger.py": "cost_events — usage and spend",
    "opaihub/budget.py": "cost_events — budget ceilings and spend",
    # verification / artifacts
    "opaihub/verification_execution.py": "artifacts — verification runs",
    "opaihub/verification_policy.py": "artifacts — verification policy state",
    # history
    "opai/gui_recents.py": "events — conversation/thread history",
}

#: Derived views and support output. Rebuildable, never sole authority.
PROJECTION_OR_EXPORT = {
    # Build-only generated identity written into wheel/sdist staging trees.
    "opai/build_metadata.py",
    "opaihub/dashboard.py",
    "opaihub/dashboard_html.py",
    "opaihub/desktop_artifacts.py",
    "opaihub/registry_writer.py",
    "opaihub/release_preflight.py",
    "opaihub/signing.py",
    "opaihub/evidence_cache.py",
    "opai/gui_web.py",
    "opai/publish.py",
    "opai/update/native.py",
    "opai/update/packaging.py",
    "opai/update/release.py",
    "opai/visibility.py",
    "opcoding/dashboard.py",
    "opcoding/doctor.py",
    "opcoding/ci.py",
}

#: Caches, preferences, scaffolding, benchmarks, docs. Explicit #613 non-goal.
NOT_RUNTIME_STATE = {
    "opai/app_state.py",
    "opai/cli.py",
    "opai/context_slim.py",
    "opai/gui_workspace.py",
    "opai/installer.py",
    "opai/model_overrides.py",
    "opai/updater.py",
    "opaihub/accounts.py",
    "opaihub/app_scaffold.py",
    "opaihub/benchmark.py",
    "opaihub/build_loop.py",
    "opaihub/context_engine.py",
    "opaihub/context_pack.py",
    "opaihub/eval_harness.py",
    "opaihub/guarded.py",
    "opaihub/gui_preferences.py",
    "opaihub/local_fallback.py",
    "opaihub/mcp.py",
    "opaihub/opaibench.py",
    "opaihub/provider_tools.py",
    "opaihub/repo_context.py",
    "opaihub/semantic_index.py",
    "opaihub/team.py",
    "opaihub/team_policy.py",
    "opcoding/cache.py",
    "opcoding/cli.py",
    "opcoding/context_manager.py",
    "opcoding/free_tools.py",
    "opcoding/hooks.py",
    "opcoding/testing.py",
    "opcoding/utils.py",
}


def _durable_writers() -> dict[str, set[str]]:
    """Every module that calls a durable-write primitive, with which ones."""

    found: dict[str, set[str]] = {}
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in relative:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # pragma: no cover - defensive
                continue
            hits = {
                (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
            } & DURABLE_WRITE_CALLS
            if hits:
                found[relative] = hits
    return found


class DurableWriteInventoryTests(unittest.TestCase):
    def test_every_durable_writer_is_classified(self) -> None:
        """A new durable writer must be triaged, not discovered after a crash.

        This is the whole of Stage 1 kept honest: the moment a module starts
        persisting something, someone decides whether it is runtime truth the
        journal owns or a cache it must not.
        """

        classified = set(JOURNAL_OWNED) | PROJECTION_OR_EXPORT | NOT_RUNTIME_STATE
        unclassified = sorted(set(_durable_writers()) - classified)
        self.assertEqual(
            unclassified,
            [],
            "these modules write durably but have no #613 migration owner:\n  "
            + "\n  ".join(unclassified)
            + "\nAdd each to JOURNAL_OWNED (runtime/status/cost/approval/lease/"
            "history truth), PROJECTION_OR_EXPORT (rebuildable view) or "
            "NOT_RUNTIME_STATE (cache/preference/scaffolding).",
        )

    def test_the_classes_do_not_overlap(self) -> None:
        """A module has exactly one owner, or the mapping decides nothing."""

        journal = set(JOURNAL_OWNED)
        for left_name, left, right_name, right in (
            ("JOURNAL_OWNED", journal, "PROJECTION_OR_EXPORT", PROJECTION_OR_EXPORT),
            ("JOURNAL_OWNED", journal, "NOT_RUNTIME_STATE", NOT_RUNTIME_STATE),
            (
                "PROJECTION_OR_EXPORT",
                PROJECTION_OR_EXPORT,
                "NOT_RUNTIME_STATE",
                NOT_RUNTIME_STATE,
            ),
        ):
            with self.subTest(pair=f"{left_name}/{right_name}"):
                self.assertEqual(sorted(left & right), [])

    def test_the_inventory_does_not_reference_modules_that_moved(self) -> None:
        """A stale entry hides a real writer behind a name that no longer exists."""

        missing = sorted(
            relative
            for relative in set(JOURNAL_OWNED)
            | PROJECTION_OR_EXPORT
            | NOT_RUNTIME_STATE
            if not (ROOT / relative).is_file()
        )
        self.assertEqual(
            missing, [], f"inventory names modules that are gone: {missing}"
        )

    def test_the_scan_finds_a_plausible_number_of_writers(self) -> None:
        """A scan that collapses to nothing would make every check above vacuous."""

        self.assertGreater(len(_durable_writers()), 40)

    def test_journal_owned_modules_each_name_their_target_table(self) -> None:
        """Ownership without a destination is not a migration mapping.

        Every journal-owned module says which #613 table absorbs it, so Stage 2
        starts from a mapping rather than a re-audit.
        """

        tables = {
            "tasks",
            "runs",
            "events",
            "operations",
            "approvals",
            "artifacts",
            "cost_events",
            "leases",
            "projections",
        }
        for module, mapping in sorted(JOURNAL_OWNED.items()):
            with self.subTest(module=module):
                table = mapping.split("—")[0].split("--")[0].strip()
                self.assertIn(table, tables, f"{module} names no #613 table")


class AdoptedJournalTests(unittest.TestCase):
    """The existing journal is live, and #613 builds on it rather than beside it.

    `opaihub/run_journal.py` (#517) already provides append-only records with a
    monotonic sequence, quarantine of mid-file corruption and replayable
    recovery. It has real production callers, so #613 is a migration onto a
    working mechanism -- not another primitive built next to one.
    """

    def test_the_run_journal_has_production_callers(self) -> None:
        callers = []
        for package in PACKAGES:
            for path in sorted((ROOT / package).rglob("*.py")):
                relative = path.relative_to(ROOT).as_posix()
                if relative == "opaihub/run_journal.py" or "__pycache__" in relative:
                    continue
                source = path.read_text(encoding="utf-8")
                if "run_journal" in source and "import" in source:
                    callers.append(relative)
        self.assertTrue(
            callers,
            "run_journal has no production caller; #613 would be migrating onto "
            "a dead primitive",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
