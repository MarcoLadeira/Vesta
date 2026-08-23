"""Only the generated contract may define lifecycle meaning (#612 AC4).

#612's definition of done is not "Python and JavaScript happen to agree today".
It is that there is exactly one place lifecycle meaning can be edited, and the
repository rejects every attempt to make another surface disagree.

Agreement reached by hand is not that property: it decays the moment someone --
human or AI agent -- adds a second terminal-state set "just for this module",
or a transition map in a renderer. Both are cheap to write, look reasonable in
review, and silently re-create the split this issue exists to remove.

So the rule is enforced structurally rather than by convention:

* only ``opaihub/generated_lifecycle.py`` may enumerate the canonical state IDs
  as a literal collection -- everything else imports it;
* only ``generated-lifecycle.js`` may hold a browser transition table;
* presentation may group, rename and re-order states, but may not decide
  legality or terminality.

Uses AST rather than regex for the Python side, so a set spread over several
lines or built with different quoting is still caught, and a mention inside a
docstring or comment is not.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from opaihub.generated_lifecycle import STATE_IDS, TERMINAL_STATE_IDS

ROOT = Path(__file__).resolve().parents[1]

#: The generated module is the definition; run_state.py is the canonical
#: runtime consumer that re-exposes it as an Enum, which necessarily names the
#: members. Nothing else may enumerate the vocabulary.
PYTHON_AUTHORITIES = {
    Path("opaihub/generated_lifecycle.py"),
    Path("opaihub/run_state.py"),
}

#: Generated browser contract, plus the schema/generator that produce it.
BROWSER_AUTHORITY = Path("opai/assets/web/generated-lifecycle.js")

#: A collection literal containing at least this many canonical state IDs is
#: treated as a rival vocabulary. Two is deliberate: a pair like
#: {"completed", "failed"} is already a hand-made terminal set, while a single
#: ID is an ordinary comparison.
REDEFINITION_THRESHOLD = 2

#: Reviewed exceptions, each with the reason it is not lifecycle drift.
#:
#: A blanket "no literal anywhere" rule would be wrong: several of these are
#: genuinely different domains whose vocabulary merely overlaps by name, and
#: forcing them to import the lifecycle contract would assert a relationship
#: that does not exist. Two, however, ARE lifecycle meaning kept by hand, and
#: are recorded as debt rather than quietly blessed.
#:
#: Ratchet, not an allowlist: a NEW literal in a module not listed here fails,
#: and an entry that no longer fires fails too, so a fixed module cannot keep
#: silent permission it no longer needs.
REVIEWED_LITERALS: dict[str, str] = {
    "opai/activity.py": (
        "activity-row vocabulary (pending/success/warning/error) for the UI "
        "timeline. Overlaps the lifecycle only in the words 'running' and "
        "'cancelled'; an activity row is not a run."
    ),
    "opai/gui_recents.py": (
        "persisted thread-status projection for the recents list. Presentation "
        "and storage, downstream of the completion verdict, never an authority "
        "over it."
    ),
    "opai/update/runtime.py": (
        "GUI workflow-phase vocabulary used only to detect a safe replacement "
        "boundary. A workflow phase is active work, not a run lifecycle state."
    ),
    "opaihub/change_attribution.py": (
        "external-operation and change-set outcomes for #620 run-scoped "
        "attribution (intent / succeeded / uncertain, plus worktree probe "
        "kinds). These describe whether a repository *mutation* happened and "
        "who caused it, not what state a run is in; the overlap with the "
        "lifecycle is only the generic words 'failed' and 'cancelled'."
    ),
    "opaihub/checkpoints.py": (
        "checkpoint outcomes (answered / read_only / cancelled_before_edit). A "
        "different domain that happens to share several words."
    ),
    "opaihub/legacy_status.py": (
        "the designated compatibility boundary. Naming legacy strings is its "
        "entire job; #612 AC7 makes these output-only, and #612 AC9 gives them "
        "per-alias telemetry and a removal criterion."
    ),
    "opaihub/provider_reliability.py": (
        "provider failure reasons (auth / rate limit / timeout), not run "
        "lifecycle states."
    ),
    "opaihub/auto_router.py": (
        "routing eligibility checks against two terminal states; reads the "
        "vocabulary, does not define it."
    ),
    # --- lifecycle meaning still held by hand: recorded as debt, not blessed ---
    "opaihub/ledger.py": (
        "DEBT: OUTCOME_CATEGORIES is a hand-maintained set of terminal classes. "
        "It is deliberately NARROWER than TERMINAL_STATE_IDS (no timeout, no "
        "needs_attention), so deriving it mechanically would change which "
        "outcomes a task may record. Whether a task outcome should be "
        "recordable as timeout is a product decision for #288/#618, not a "
        "rename this issue may make silently."
    ),
    # opaihub/run_result.py held a DEBT entry here for _AUTOMATIC_RETRY_STATES
    # ({failed, timeout}) -- retry eligibility restated outside the schema. #618
    # paid it: automatic_retry_eligible is now a required per-state field in
    # lifecycle_schema.json, generated into STATE_SPECS, and derived in
    # run_result.py rather than written there. The exemption is deliberately
    # deleted rather than kept "just in case": this file's ratchet fails on a
    # stale entry, which is what surfaced the change.
}

_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".opaihub",
    ".opcoding",
    "build",
    "dist",
    "tests",
}


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        files.append(path)
    return sorted(files)


def _state_literals(tree: ast.AST) -> list[tuple[int, list[str]]]:
    """Collection literals holding several canonical state IDs."""

    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            continue
        ids = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            and element.value in STATE_IDS
        ]
        if len(ids) >= REDEFINITION_THRESHOLD:
            found.append((getattr(node, "lineno", 0), sorted(ids)))
    return found


class NoSecondPythonAuthorityTests(unittest.TestCase):
    def _offending_modules(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for path in _python_sources():
            rel = path.relative_to(ROOT)
            if rel in PYTHON_AUTHORITIES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            hits = [
                f"{rel.as_posix()}:{lineno} -> {ids}"
                for lineno, ids in _state_literals(tree)
            ]
            if hits:
                found[rel.as_posix()] = hits
        return found

    def test_no_new_module_enumerates_the_state_vocabulary(self):
        """A module not reviewed above must not start its own vocabulary."""
        offenders = self._offending_modules()
        unreviewed = sorted(set(offenders) - set(REVIEWED_LITERALS))
        detail = "\n  ".join(line for name in unreviewed for line in offenders[name])
        self.assertEqual(
            unreviewed,
            [],
            "these modules enumerate canonical lifecycle states outside the "
            "generated contract, which is how a second, drifting authority "
            "starts. Import from opaihub.generated_lifecycle, or add the module "
            "to REVIEWED_LITERALS with the reason it is a different domain:\n  "
            + detail,
        )

    def test_a_module_that_stopped_offending_loses_its_exception(self):
        """A stale entry silently re-permits the debt it was granted for."""
        offenders = self._offending_modules()
        stale = sorted(set(REVIEWED_LITERALS) - set(offenders))
        self.assertEqual(
            stale,
            [],
            "these modules no longer enumerate lifecycle states, so their "
            f"entry in REVIEWED_LITERALS is stale and must be removed: {stale}",
        )

    def test_every_reviewed_exception_states_a_reason(self):
        for name, reason in REVIEWED_LITERALS.items():
            with self.subTest(module=name):
                self.assertGreater(
                    len(reason), 40, f"{name} needs a real reason, not a label"
                )

    def test_the_two_known_debts_stay_named_as_debts(self):
        """#612 must not let a hand-held lifecycle rule quietly become normal.

        Both are genuine lifecycle meaning kept outside the schema. Neither is
        safe to move inside this issue -- one changes which outcomes a task may
        record, the other changes the RunResult contract #618 owns -- so they
        are carried as named debt with an owner rather than silently accepted.
        """
        # run_result.py was the second entry here until #618 paid it: retry
        # eligibility is now a generated per-state field rather than a
        # frozenset written by hand. ledger.py's OUTCOME_CATEGORIES remains,
        # and it is genuinely not #618's to move -- it changes which outcomes a
        # task may record, which #288 owns.
        for module in ("opaihub/ledger.py",):
            with self.subTest(module=module):
                self.assertTrue(
                    REVIEWED_LITERALS[module].startswith("DEBT:"),
                    f"{module} is lifecycle meaning held by hand; if that has "
                    "been fixed, remove the entry rather than downgrading it",
                )

    def test_the_detector_would_catch_a_reintroduced_terminal_set(self):
        """An analyser that never fires leaves this file permanently green."""
        sample = (
            "TERMINAL_STATES = {'completed', 'failed', 'cancelled'}\n"
            "def f():\n    return TERMINAL_STATES\n"
        )
        found = _state_literals(ast.parse(sample))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], ["cancelled", "completed", "failed"])

    def test_the_detector_ignores_a_single_state_comparison(self):
        """`status in ("completed",)` is ordinary code, not a rival vocabulary."""
        self.assertEqual(_state_literals(ast.parse("x = ('completed',)")), [])

    def test_the_detector_ignores_prose(self):
        """A docstring naming states must not be mistaken for a definition."""
        self.assertEqual(
            _state_literals(ast.parse('"""completed, failed, cancelled."""')), []
        )

    def test_the_scan_actually_reaches_production_modules(self):
        """Guard against a glob that silently matches nothing."""
        scanned = {p.relative_to(ROOT).as_posix() for p in _python_sources()}
        self.assertIn("opaihub/run_state.py", scanned)
        self.assertIn("opai/app_state.py", scanned)
        self.assertGreater(len(scanned), 100)


class NoSecondBrowserAuthorityTests(unittest.TestCase):
    def _web_sources(self) -> list[Path]:
        web = ROOT / "opai" / "assets" / "web"
        return sorted(
            path
            for path in web.rglob("*.js")
            if "node_modules" not in path.parts and "__tests__" not in path.parts
        )

    def test_only_the_generated_contract_holds_a_browser_transition_table(self):
        offenders: list[str] = []
        for path in self._web_sources():
            rel = path.relative_to(ROOT)
            if rel == BROWSER_AUTHORITY:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in ("ALLOWED_TRANSITIONS", "TRANSITIONS =", "TERMINAL_STATES"):
                if marker in text:
                    offenders.append(f"{rel.as_posix()} defines {marker}")
        self.assertEqual(
            offenders,
            [],
            "the browser must consume the generated lifecycle contract, never "
            "define its own legality or terminality:\n  " + "\n  ".join(offenders),
        )

    def test_the_message_store_derives_from_the_generated_contract(self):
        """It must fail loudly if the contract is absent, never fall back."""
        source = (ROOT / "opai" / "assets" / "web" / "message-state.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("global.OPaiLifecycle", source)
        self.assertIn("lifecycle.canTransition", source)
        self.assertIn("lifecycle.isTerminal", source)
        self.assertIn("must load first", source)

    def test_the_generated_browser_contract_is_shipped_to_the_package(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("assets/web/*.js", pyproject)
        self.assertTrue((ROOT / BROWSER_AUTHORITY).exists())


class GeneratedArtefactsAreByteStableTests(unittest.TestCase):
    """Generated output must be identical on every platform (#612 AC7).

    The generator writes LF, but the drift check compares *decoded* text, so a
    working tree holding CRLF passes it -- and `write_or_check` skips a write
    whose decoded text already matches, so regenerating cannot repair the file
    either. A byte-level change to a generated artefact was therefore neither
    detected nor fixable.

    `.gitattributes` pins `.py`, `.json` and `.md` to `eol=lf`, which is why
    three of the four targets were fine. The browser contract is a `.js` file
    and was not pinned, so with `core.autocrlf=true` -- this repository's
    Windows default -- it checked out as CRLF. Found by running the drift
    qualification on Windows rather than assuming Linux semantics.
    """

    TARGETS = (
        "opaihub/generated_lifecycle.py",
        "opai/assets/web/generated-lifecycle.js",
        "opaihub/data/lifecycle-fixtures.json",
        "docs/lifecycle-schema.md",
    )

    def test_every_generated_target_is_lf_in_the_working_tree(self):
        offenders = [
            name for name in self.TARGETS if b"\r\n" in (ROOT / name).read_bytes()
        ]
        self.assertEqual(
            offenders,
            [],
            "these generated files hold CRLF, so their bytes differ from what "
            "the generator writes while the drift check still passes: "
            f"{offenders}. Pin them in .gitattributes with `text eol=lf`.",
        )

    def test_every_generated_target_is_pinned_in_gitattributes(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        pinned_extensions = {
            line.split()[0].lstrip("*")
            for line in attributes.splitlines()
            if line.strip() and not line.startswith("#") and "eol=lf" in line
        }
        for name in self.TARGETS:
            with self.subTest(target=name):
                suffix = Path(name).suffix
                covered = suffix in pinned_extensions or any(
                    name in line for line in attributes.splitlines()
                )
                self.assertTrue(
                    covered,
                    f"{name} is generated but not pinned to eol=lf, so its "
                    "bytes depend on the platform that checked it out",
                )


class TerminalityAgreesAcrossTargetsTests(unittest.TestCase):
    """AC3: terminal meaning must be identical in every generated target."""

    def _browser_contract(self) -> dict:
        """Evaluate the generated contract in Node and return what it exposes.

        Deliberately executed rather than pattern-matched: the file is an IIFE
        that assigns ``global.OPaiLifecycle`` and freezes it, so a regex over
        the source proves nothing about what the browser actually receives --
        and my first attempt at one silently matched the wrong thing.
        """
        import json
        import shutil
        import subprocess  # nosec B404 - fixed argv, no shell

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed on this machine")
        script = (
            f"require({json.dumps(str(ROOT / BROWSER_AUTHORITY))});"
            "var c = globalThis.OPaiLifecycle;"
            "process.stdout.write(JSON.stringify({"
            "  stateIds: c.stateIds,"
            "  terminalStateIds: c.terminalStateIds,"
            "  schemaVersion: c.schemaVersion,"
            "  verifyingToRunning: c.canTransition('verifying', 'running'),"
            "  completedToRunning: c.canTransition('completed', 'running'),"
            "  cancelRequestedTerminal: c.isTerminal('cancel_requested')"
            "}));"
        )
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            [node, "-e", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"node could not load the contract: {completed.stderr}",
        )
        return json.loads(completed.stdout)

    def test_python_and_browser_agree_on_the_terminal_set(self):
        contract = self._browser_contract()
        self.assertEqual(
            sorted(contract["terminalStateIds"]), sorted(TERMINAL_STATE_IDS)
        )

    def test_python_and_browser_agree_on_the_state_vocabulary(self):
        contract = self._browser_contract()
        self.assertEqual(sorted(contract["stateIds"]), sorted(STATE_IDS))

    def test_the_repair_edge_is_legal_in_the_browser_too(self):
        """#612 AC2 and the defect this issue opened with.

        The audit found Python permitting `verifying -> running` for bounded
        repair while the browser's hand-maintained graph rejected it, so a
        legitimate repair continued in the engine while the UI showed a stale
        or dead-end state. Both sides now read one generated table; this is the
        permanent regression fixture for that disagreement.
        """
        from opaihub.run_state import can_transition

        contract = self._browser_contract()
        self.assertTrue(can_transition("verifying", "running"), "Python rejects repair")
        self.assertTrue(contract["verifyingToRunning"], "browser rejects repair")

    def test_terminal_regression_is_illegal_on_both_sides(self):
        from opaihub.run_state import can_transition

        contract = self._browser_contract()
        self.assertFalse(can_transition("completed", "running"))
        self.assertFalse(contract["completedToRunning"])

    def test_cancel_requested_is_non_terminal_on_both_sides(self):
        from opaihub.run_state import is_terminal

        contract = self._browser_contract()
        self.assertFalse(is_terminal("cancel_requested"))
        self.assertFalse(contract["cancelRequestedTerminal"])

    def test_cancel_requested_is_never_terminal(self):
        """AC5, in the one place both surfaces read from."""
        self.assertIn("cancel_requested", STATE_IDS)
        self.assertNotIn("cancel_requested", TERMINAL_STATE_IDS)


if __name__ == "__main__":
    unittest.main()
