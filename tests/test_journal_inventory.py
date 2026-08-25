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
        # A module that opens the journal is a durable writer even though it
        # never touches sqlite3 itself. The sqlite3.connect signal added with
        # journal_store.py only sees the *primitive*, so an adapter one layer
        # up stayed invisible -- journal_runtime.py was not flagged until this
        # was added. Same blind spot as before, one level higher.
        "open_store",
    }
)

#: Runtime truth the journal must own. Ordered by #613's schema sections so a
#: reader can map each module to the table that will absorb it.
JOURNAL_OWNED = {
    # the store itself
    # Not a record with a legacy counterpart to disagree with: it is the
    # transactional history the other entries are migrating *into*, so it is
    # machinery in the same sense run_journal and shadow_journal are.
    "opaihub/journal_store.py": "events — the SQLite WAL journal every other entry migrates into",
    # Stage 3's adapter. Originates canonical events into the journal from the
    # live run path; like shadow_journal it is a writer with no legacy record
    # of its own to disagree with, so it is machinery rather than a migration
    # target.
    "opaihub/journal_runtime.py": "runs — Stage 3 journal-backed run lifecycle",
    # Stage 6's bridge. Mirrors every exact-once external effect into the
    # operations table by hooking idempotency, the choke point they all share.
    "opaihub/journal_operations.py": "operations — Stage 6 external-effect transactions",
    # Writes admission, lifecycle, cost and verification from the live turn
    # path. Not a migration target itself -- it originates records rather than
    # owning a legacy file -- so it is machinery, like the other writers.
    "opaihub/gui_pipeline.py": "runs — the live turn path that originates journal records",
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

#: Modules that *are* the journal rather than records migrating into it. They
#: have no legacy counterpart to disagree with, so the dual-read ratchet does
#: not apply to them.
#:
#: One constant rather than a literal in each test: the two copies had already
#: drifted, which is how a module ended up exempt in one check and flagged in
#: the other.
JOURNAL_MACHINERY = frozenset(
    {
        "opaihub/gui_pipeline.py",
        "opaihub/journal_operations.py",
        "opaihub/journal_runtime.py",
        "opaihub/journal_store.py",
        "opaihub/run_journal.py",
        "opaihub/shadow_journal.py",
    }
)

#: JOURNAL_OWNED modules that need no Stage 2 shadow mirror, because the record
#: they own is *already* an append-only sequenced log with replay -- the thing
#: Stage 2's mirror exists to create. Layering a second journal on top would
#: double-write every event and give the migration two append-only logs to keep
#: consistent instead of one.
#:
#: This is a deliberately small and justified list, not a place to park awkward
#: modules: each entry is checked below for the structure that earns the
#: exemption, so a module cannot be excused by assertion alone.
ALREADY_APPEND_ONLY = {
    # Hash-chained, fsync'd audit trail; tamper-evident by construction.
    "opaihub/audit.py",
    # Sequenced + digest-chained event log with a persisted head, torn-line
    # repair and a rebuildable SQLite index.
    "opaihub/ledger.py",
}

#: Derived views and support output. Rebuildable, never sole authority.
PROJECTION_OR_EXPORT = {
    # Stage 4's qualification comparator. Opens the journal to *read* it and
    # writes nothing; flagged by the open_store signal, which is the scan
    # working -- it cannot tell a reader from a writer, and triaging one
    # module is cheaper than a scan that misses the next real writer.
    "opaihub/journal_qualification.py",
    # Stage 5's read path. Opens the journal to serve run state and writes
    # nothing; the open_store signal cannot tell a reader from a writer, which
    # is the correct trade -- triaging a reader is cheaper than a scan that
    # misses the next real writer.
    "opaihub/journal_reader.py",
    # Stage 7's retirement gate. Reads telemetry to answer one question --
    # may the legacy writes go? -- and writes nothing itself.
    "opaihub/journal_retirement.py",
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
    # User-authored notes and decisions, not execution truth. Surfaced only
    # once the scan learned to see SQLite writers -- it had persisted to its
    # own database, untriaged, the whole time. Classified here rather than
    # JOURNAL_OWNED because #613 reconstructs what a *run* did; losing these
    # would be bad, but no crash-recovery replay would rebuild them, and the
    # issue's non-goals rule out absorbing every durable store.
    "opcoding/memory.py",
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


def _opens_a_database(tree: ast.AST) -> bool:
    """True when the module calls ``sqlite3.connect`` (however it imported it)."""

    aliases = {"sqlite3"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3" and alias.asname:
                    aliases.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            for alias in node.names:
                if alias.name == "connect":
                    return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id in aliases
        ):
            return True
    return False


def _imports_the_journal(tree: ast.AST) -> bool:
    """True when a module imports any part of the #613 journal.

    Chasing *primitives* has failed four times running. The scan learned about
    ``atomic_write_text``, then ``sqlite3.connect`` when journal_store.py went
    unflagged, then ``open_store`` when journal_runtime.py did -- and
    journal_operations.py still slipped through, because it writes via
    ``record_operation`` and touches neither.

    Each fix was correct and each was one layer too low. A module that imports
    the journal at all is a writer, a reader or machinery, and every one of
    those needs triage. Detecting the *dependency* rather than the call ends
    the recurrence instead of deferring it.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith(("journal_store", "journal_runtime")):
                return True
            if module in {".", ""} or module.startswith("opaihub"):
                for alias in node.names:
                    if alias.name in {
                        "journal_store",
                        "journal_runtime",
                        "journal_operations",
                        "journal_reader",
                        "journal_qualification",
                    }:
                        return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(("journal_store", "journal_runtime")):
                    return True
    return False


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
            # A SQLite writer is a durable writer. The scan missed them
            # entirely until #613's own journal store arrived and was *not*
            # flagged -- a blind spot for exactly the storage engine this issue
            # standardises on, which would have quietly exempted every future
            # table from triage.
            #
            # Matched as `sqlite3.connect` rather than a bare `connect` in
            # DURABLE_WRITE_CALLS: the bare name also matches Qt's
            # `signal.connect(slot)`, which flagged two GUI modules that
            # persist nothing.
            if _opens_a_database(tree):
                hits = hits | {"sqlite3.connect"}
            if _imports_the_journal(tree):
                hits = hits | {"journal-dependency"}
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

    def test_already_append_only_modules_are_journal_owned_and_earn_it(self):
        """An exemption must be structural, not a line in a set.

        Stage 2 skips these two modules, so the reason has to survive someone
        later editing them. Each must still be JOURNAL_OWNED (they own runtime
        truth) and must still actually append rather than overwrite -- if a
        refactor turned one into a whole-file rewrite, the exemption would be
        silently wrong and this fails.
        """

        for module in sorted(ALREADY_APPEND_ONLY):
            with self.subTest(module=module):
                self.assertIn(
                    module,
                    JOURNAL_OWNED,
                    "an exempt module must still own runtime truth",
                )
                source = (ROOT / module).read_text(encoding="utf-8")
                # assertTrue, not assertIn: a failing assertIn would dump the
                # whole module into the report and bury the actual reason.
                self.assertTrue(
                    'open("a' in source,
                    f"{module} is exempt from the shadow mirror only because it "
                    "appends rather than overwrites -- that is no longer true",
                )
                self.assertTrue(
                    "fsync" in source,
                    f"{module} is trusted as a durable append-only log, but no "
                    "longer fsyncs",
                )

    def test_every_journal_owned_record_has_a_dual_read(self):
        """Stage 2's completion, pinned so it cannot quietly regress.

        #613 Stage 2 is shadow-write *plus* dual-read: every JOURNAL_OWNED
        record is mirrored into a journal, and something can be asked at
        runtime whether the two still agree. The mirror alone is not enough --
        an unverified shadow is just a second file to go stale, and Stage 4
        cannot qualify a cutover on real traffic without a comparator.

        This is a ratchet, not a survey. Its real job is the *next* module
        somebody adds to JOURNAL_OWNED: the entry is cheap to write and the
        migration is not, and without this the gap would only surface at Stage
        4, long after the record started being trusted.

        Three ways to satisfy it, and each is a real design:

        - a shared-helper mirror plus a ``*contradiction_report`` accessor,
          which is what sixteen of these modules do;
        - membership in ALREADY_APPEND_ONLY -- the record *is* the log, so
          there is no second copy to disagree with;
        - the journal machinery itself, which has no record of its own.
        """

        machinery = JOURNAL_MACHINERY
        missing = []
        for module in sorted(JOURNAL_OWNED):
            if module in machinery or module in ALREADY_APPEND_ONLY:
                continue
            source = (ROOT / module).read_text(encoding="utf-8")
            if "contradiction_report" not in source:
                missing.append(module)
        self.assertEqual(
            missing,
            [],
            "JOURNAL_OWNED without a dual read -- add a contradiction report, "
            "or justify an ALREADY_APPEND_ONLY exemption",
        )

    def test_every_mirrored_module_actually_writes_to_a_journal(self):
        """A comparator with nothing behind it would pass the test above.

        Reading a projection that is always empty and comparing it to a file
        that is always populated would report a contradiction on every record
        rather than none -- loud rather than silent, but still wrong. This
        pins that each module reaches a journal, via the shared helper or
        #517 directly.
        """

        machinery = JOURNAL_MACHINERY
        unmirrored = []
        for module in sorted(JOURNAL_OWNED):
            if module in machinery or module in ALREADY_APPEND_ONLY:
                continue
            source = (ROOT / module).read_text(encoding="utf-8")
            if "shadow_journal" not in source and "run_journal" not in source:
                unmirrored.append(module)
        self.assertEqual(
            unmirrored,
            [],
            "JOURNAL_OWNED with a contradiction report but no journal behind it",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
