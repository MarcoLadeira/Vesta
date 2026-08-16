"""A retry must not repeat an outward side effect (#295 alpha gate 4).

Gate 4: *"Duplicate side effect: 0 after retry, replay, reconnect or failover."*
Nothing enforced it. `create_pull_request` POSTs straight to GitHub and
`comment_pr` straight to the issue thread, so a retried, resumed or reconnected
turn opened a second pull request or posted the same comment twice. Both are
outward, visible to other people, and not undoable by OPai.

The design point these tests exist to protect is the **third state**. A simple
"set of completed keys" is wrong at exactly the moment it matters: the process
can die between performing the side effect and recording it. With two states
the store must guess, and both guesses are real failures — redo duplicates,
skip silently drops work.
"""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub.idempotency import (
    DONE,
    FRESH,
    IN_FLIGHT,
    UNCERTAIN_TTL_SECONDS,
    abandon,
    begin,
    complete,
    operation_key,
    status,
)


def _claim_in_child(
    project_root: str,
    key: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    """Spawn-compatible worker for the duplicate-dispatch race."""

    start.wait(timeout=15)
    try:
        results.put(begin(Path(project_root), key)["state"])
    except Exception as exc:  # pragma: no cover - asserted in parent
        results.put(f"error:{exc!r}")


class KeyTests(unittest.TestCase):
    def test_the_same_request_produces_the_same_key(self) -> None:
        a = operation_key("open_pr", root="/r", head="feat", base="main", title="Fix")
        b = operation_key("open_pr", root="/r", head="feat", base="main", title="Fix")
        self.assertEqual(a, b)

    def test_argument_order_does_not_change_the_key(self) -> None:
        a = operation_key("open_pr", head="feat", title="Fix")
        b = operation_key("open_pr", title="Fix", head="feat")
        self.assertEqual(a, b)

    def test_a_different_request_produces_a_different_key(self) -> None:
        base = operation_key("open_pr", root="/r", head="feat", base="main", title="A")
        for changed in (
            operation_key("open_pr", root="/r", head="feat", base="main", title="B"),
            operation_key("open_pr", root="/r", head="other", base="main", title="A"),
            operation_key("open_pr", root="/x", head="feat", base="main", title="A"),
            operation_key("comment_pr", root="/r", head="feat", base="main", title="A"),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual(base, changed)

    def test_whitespace_alone_is_not_a_different_operation(self) -> None:
        # A retry that re-renders the same body with a trailing newline is the
        # same request; treating it as new would defeat the whole mechanism.
        a = operation_key("comment_pr", body="Looks good to me")
        b = operation_key("comment_pr", body="  Looks good to me\n")
        self.assertEqual(a, b)

    def test_long_user_authored_values_are_hashed_not_stored(self) -> None:
        # A PR body or comment can contain anything the user wrote, so the key
        # must be stable without the store holding its content.
        key = operation_key("comment_pr", body="secret-token-" + "x" * 500)
        self.assertNotIn("secret-token", key)
        self.assertLess(len(key), 96)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self.key = operation_key("open_pr", root="r", head="feat", title="Fix")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_an_unseen_operation_is_fresh(self) -> None:
        self.assertEqual(status(self.root, self.key)["state"], FRESH)

    def test_beginning_claims_the_key_as_in_flight(self) -> None:
        self.assertEqual(begin(self.root, self.key)["state"], FRESH)
        self.assertEqual(status(self.root, self.key)["state"], IN_FLIGHT)

    def test_a_confirmed_operation_returns_its_recorded_result(self) -> None:
        begin(self.root, self.key)
        complete(self.root, self.key, {"url": "https://example/pr/7", "number": 7})
        again = begin(self.root, self.key)
        self.assertEqual(again["state"], DONE)
        self.assertEqual(again["result"]["number"], 7)

    def test_beginning_never_overwrites_an_existing_record(self) -> None:
        # Overwriting would erase the very uncertainty the store exists to keep.
        begin(self.root, self.key)
        second = begin(self.root, self.key)
        self.assertEqual(second["state"], IN_FLIGHT)

    def test_a_crash_between_effect_and_record_stays_uncertain(self) -> None:
        # The case that forces three states: the side effect may exist, and no
        # honest answer is available without asking a human.
        begin(self.root, self.key)  # ...process dies here
        self.assertEqual(begin(self.root, self.key)["state"], IN_FLIGHT)

    def test_a_provably_failed_attempt_releases_the_key(self) -> None:
        # A validation error or refused approval never reached the network, so
        # a corrected retry must not be blocked.
        begin(self.root, self.key)
        abandon(self.root, self.key)
        self.assertEqual(status(self.root, self.key)["state"], FRESH)

    def test_uncertainty_never_expires_into_an_automatic_retry(self) -> None:
        begin(self.root, self.key, now=1000.0)
        still = status(self.root, self.key, now=1000.0 + UNCERTAIN_TTL_SECONDS - 1)
        self.assertEqual(still["state"], IN_FLIGHT)
        later = status(self.root, self.key, now=1000.0 + UNCERTAIN_TTL_SECONDS + 1)
        self.assertEqual(later["state"], IN_FLIGHT)
        self.assertTrue(later["expired"])
        self.assertTrue(later["requires_reconciliation"])

    def test_the_stored_result_holds_no_bodies_or_secrets(self) -> None:
        begin(self.root, self.key)
        complete(
            self.root,
            self.key,
            {"url": "https://example/pr/7", "body": "x" * 5000, "token": "sk-secret"},
        )
        stored = status(self.root, self.key)["result"]
        self.assertLessEqual(len(stored.get("body", "")), 200)
        self.assertLessEqual(len(stored), 8)

    def test_an_unwritable_store_blocks_the_external_effect(self) -> None:
        with mock.patch("opaihub.idempotency._save", side_effect=OSError("read-only")):
            outcome = begin(self.root, self.key)
        self.assertEqual(outcome["state"], IN_FLIGHT)
        self.assertTrue(outcome["persistence_blocked"])

    def test_a_corrupt_store_blocks_instead_of_forgetting_uncertainty(self) -> None:
        from opaihub import idempotency

        path = idempotency._path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not-json", encoding="utf-8")

        outcome = begin(self.root, self.key)

        self.assertEqual(outcome["state"], IN_FLIGHT)
        self.assertTrue(outcome["persistence_blocked"])

    def test_a_malformed_record_blocks_instead_of_looking_fresh(self) -> None:
        from opaihub import idempotency

        path = idempotency._path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"open_pr:test": {"state": "unexpected", "at": 1, "result": {}}}',
            encoding="utf-8",
        )

        outcome = begin(self.root, "open_pr:test")

        self.assertEqual(outcome["state"], IN_FLIGHT)
        self.assertTrue(outcome["persistence_blocked"])

    def test_store_capacity_never_evicts_exact_once_history(self) -> None:
        from opaihub import idempotency

        path = idempotency._path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        store = {
            f"key-{index}": {"state": DONE, "at": 1, "result": {}}
            for index in range(idempotency.MAX_RECORDS)
        }
        path.write_text(json.dumps(store), encoding="utf-8")

        outcome = begin(self.root, "one-too-many")

        self.assertEqual(outcome["state"], IN_FLIGHT)
        self.assertTrue(outcome["persistence_blocked"])
        preserved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(preserved), idempotency.MAX_RECORDS)
        self.assertNotIn("one-too-many", preserved)

    def test_concurrent_processes_create_one_claim(self) -> None:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        workers = [
            context.Process(
                target=_claim_in_child,
                args=(str(self.root), self.key, start, results),
            )
            for _ in range(8)
        ]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(timeout=20)
            self.assertFalse(worker.is_alive(), "operation claimant timed out")
            self.assertEqual(worker.exitcode, 0)

        states = [results.get(timeout=5) for _ in workers]
        self.assertEqual(states.count(FRESH), 1, states)
        self.assertEqual(states.count(IN_FLIGHT), len(workers) - 1, states)


class OpenPrTests(unittest.TestCase):
    """The operation with no protection at all: a retry opened PR #2."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self):
        from opaihub.provider_tools import RepositoryToolExecutor

        executor = RepositoryToolExecutor(
            self.root, allow_edits=True, allow_git_ops=True, allow_github_write=True
        )
        executor._current_branch = lambda: "feature-branch"  # type: ignore[method-assign]
        return executor

    def test_a_retried_turn_does_not_open_a_second_pull_request(self) -> None:
        calls: list[dict] = []

        def fake_create(root, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "url": "https://example/pr/7", "number": 7}

        executor = self._executor()
        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            executor._open_pr({"title": "Fix the bug", "base": "main"})
            second = executor._open_pr({"title": "Fix the bug", "base": "main"})

        self.assertEqual(len(calls), 1, "the second attempt must not POST again")
        self.assertTrue(second["ok"])
        # The repeat still reports the real PR rather than an error or a lie.
        self.assertEqual(second["data"]["number"], 7)

    def test_an_uncertain_earlier_attempt_is_reported_not_repeated(self) -> None:
        executor = self._executor()

        def die(root, **kwargs):
            raise RuntimeError("connection lost after the request was sent")

        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=die
        ):
            with self.assertRaises(RuntimeError):
                executor._open_pr({"title": "Fix the bug", "base": "main"})

        calls: list[dict] = []

        def fake_create(root, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "url": "https://example/pr/9", "number": 9}

        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            retry = executor._open_pr({"title": "Fix the bug", "base": "main"})

        self.assertEqual(calls, [], "an unconfirmed PR must not be opened again")
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["error_code"], "PR_STATE_UNCERTAIN")
        # The message has to tell the user what to actually do about it.
        self.assertIn("may already exist", retry["message"])

    def test_claim_persistence_failure_blocks_before_github_dispatch(self) -> None:
        calls = []
        executor = self._executor()

        with (
            mock.patch("opaihub.idempotency._save", side_effect=OSError("disk full")),
            mock.patch(
                "opaihub.github_connector.create_pull_request",
                side_effect=lambda *args, **kwargs: calls.append((args, kwargs)),
            ),
        ):
            result = executor._open_pr({"title": "Fix the bug", "base": "main"})

        self.assertEqual(calls, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "PR_STATE_UNCERTAIN")

    def test_a_rejected_request_leaves_a_corrected_retry_free(self) -> None:
        executor = self._executor()
        with mock.patch(
            "opaihub.github_connector.create_pull_request",
            return_value={"ok": False, "error": "A PR title is required"},
        ):
            executor._open_pr({"title": "Fix the bug", "base": "main"})

        calls: list[dict] = []

        def fake_create(root, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "url": "https://example/pr/3", "number": 3}

        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            retry = executor._open_pr({"title": "Fix the bug", "base": "main"})
        self.assertEqual(len(calls), 1)
        self.assertTrue(retry["ok"])

    def test_a_genuinely_different_pr_is_not_blocked(self) -> None:
        # Duplicate protection must not become "one PR per repository forever".
        calls: list[dict] = []

        def fake_create(root, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "url": "https://example/pr/1", "number": len(calls)}

        executor = self._executor()
        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            executor._open_pr({"title": "Fix the bug", "base": "main"})
            executor._open_pr({"title": "A different change", "base": "main"})
        self.assertEqual(len(calls), 2)


class CommentPrTests(unittest.TestCase):
    """A duplicate comment is visible to everyone and undoable by nobody."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _adapter(self, run):
        from opaihub.github_workflow import GitHubAdapter

        adapter = GitHubAdapter(self.root)
        adapter._run = run  # type: ignore[method-assign]
        return adapter

    def test_a_retried_turn_does_not_post_the_comment_twice(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "posted"

        adapter = self._adapter(run)
        adapter.comment_pr(4, "Looks good to me")
        adapter.comment_pr(4, "Looks good to me")
        self.assertEqual(len(calls), 1)

    def test_a_different_comment_still_posts(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "posted"

        adapter = self._adapter(run)
        adapter.comment_pr(4, "First thought")
        adapter.comment_pr(4, "Second thought")
        self.assertEqual(len(calls), 2)

    def test_the_same_text_on_a_different_pr_still_posts(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "posted"

        adapter = self._adapter(run)
        adapter.comment_pr(4, "LGTM")
        adapter.comment_pr(5, "LGTM")
        self.assertEqual(len(calls), 2)

    def test_a_failed_invocation_leaves_a_retry_free(self) -> None:
        attempts: list[int] = []

        def run(args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("gh not installed")
            return "posted"

        adapter = self._adapter(run)
        with self.assertRaises(RuntimeError):
            adapter.comment_pr(4, "LGTM")
        self.assertEqual(adapter.comment_pr(4, "LGTM"), "posted")
        self.assertEqual(len(attempts), 2)


class GithubCommentToolTests(unittest.TestCase):
    """provider_tools._github_comment (#541): the path the live agent tool
    dispatch actually calls. CommentPrTests above proves the begin/complete/
    abandon pattern on GitHubAdapter.comment_pr, but that class has no
    production caller -- this is its production sibling.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self):
        from opaihub.provider_tools import RepositoryToolExecutor

        return RepositoryToolExecutor(
            self.root, allow_edits=False, allow_github_write=True
        )

    def test_a_retried_turn_does_not_post_the_comment_twice(self) -> None:
        calls: list[dict] = []

        def fake_add(root, number, body, **kwargs):
            calls.append({"number": number, "body": body})
            return {"ok": True, "url": "https://example/pr/5#comment"}

        executor = self._executor()
        executor.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            first = executor._github_comment({"number": 5, "body": "hi"})
            second = executor._github_comment({"number": 5, "body": "hi"})

        self.assertEqual(len(calls), 1, "the second attempt must not POST again")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

    def test_an_uncertain_earlier_attempt_is_reported_not_repeated(self) -> None:
        executor = self._executor()
        executor.grant_command_once("gh pr comment 5")

        def die(root, number, body, **kwargs):
            raise RuntimeError("connection lost after the request was sent")

        with mock.patch("opaihub.github_connector.add_comment", side_effect=die):
            with self.assertRaises(RuntimeError):
                executor._github_comment({"number": 5, "body": "hi"})

        calls: list[dict] = []

        def fake_add(root, number, body, **kwargs):
            calls.append({"number": number, "body": body})
            return {"ok": True, "url": "https://example/pr/5#comment"}

        executor.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            retry = executor._github_comment({"number": 5, "body": "hi"})

        self.assertEqual(calls, [], "an unconfirmed comment must not be posted again")
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["error_code"], "COMMENT_STATE_UNCERTAIN")
        self.assertIn("may already be on GitHub", retry["message"])

    def test_an_unapproved_retry_still_asks_for_approval_not_uncertain(self) -> None:
        # No grant at all: both calls must hit COMMAND_NEEDS_APPROVAL, never a
        # false "uncertain" -- nothing was ever sent to GitHub either time.
        executor = self._executor()
        first = executor._github_comment({"number": 5, "body": "hi"})
        second = executor._github_comment({"number": 5, "body": "hi"})
        for result in (first, second):
            self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")

    def test_a_genuinely_different_comment_is_not_blocked(self) -> None:
        calls: list[dict] = []

        def fake_add(root, number, body, **kwargs):
            calls.append({"number": number, "body": body})
            return {"ok": True, "url": f"https://example/pr/5#{len(calls)}"}

        executor = self._executor()
        executor.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            executor._github_comment({"number": 5, "body": "First thought"})
        executor.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            executor._github_comment({"number": 5, "body": "Second thought"})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
