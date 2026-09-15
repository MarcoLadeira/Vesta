"""A retry must not repeat an outward side effect (#295 alpha gate 4).

Gate 4: *"Duplicate side effect: 0 after retry, replay, reconnect or failover."*
Nothing enforced it. `create_pull_request` POSTs straight to GitHub and
`comment_pr` straight to the issue thread, so a retried, resumed or reconnected
turn opened a second pull request or posted the same comment twice. Both are
outward, visible to other people, and not undoable by Vesta.

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
            if args[:2] == ["pr", "view"]:
                # The reconcile read: GitHub reachable, comment absent.
                return '{"comments": []}'
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("gh exited non-zero")
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


class MergePrTests(unittest.TestCase):
    """A merge lands on the default branch's history and cannot be undone by
    Vesta -- the most irreversible outward action there is. A retried, resumed
    or reconnected turn must not dispatch it twice (#616).
    """

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

    def test_a_retried_turn_does_not_merge_twice(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "merged"

        adapter = self._adapter(run)
        adapter.merge_pr(9)
        adapter.merge_pr(9)
        self.assertEqual(len(calls), 1)

    def test_a_different_method_is_a_different_operation(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "merged"

        adapter = self._adapter(run)
        adapter.merge_pr(9, method="squash")
        adapter.merge_pr(9, method="rebase")
        self.assertEqual(len(calls), 2)

    def test_the_same_method_on_a_different_pr_still_merges(self) -> None:
        calls: list[list[str]] = []

        def run(args, **kwargs):
            calls.append(list(args))
            return "merged"

        adapter = self._adapter(run)
        adapter.merge_pr(9)
        adapter.merge_pr(10)
        self.assertEqual(len(calls), 2)

    def test_a_failed_invocation_leaves_a_retry_free(self) -> None:
        attempts: list[int] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                # The reconcile read: GitHub reachable, PR observably open —
                # the failed merge provably never landed.
                return '{"state": "OPEN"}'
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("required checks are still pending")
            return "merged"

        adapter = self._adapter(run)
        with self.assertRaises(RuntimeError):
            adapter.merge_pr(9)
        self.assertEqual(adapter.merge_pr(9), "merged")
        self.assertEqual(len(attempts), 2)

    def test_an_unconfirmed_attempt_fails_closed_as_uncertain(self) -> None:
        # Simulated crash: the key stays in_flight because the process died
        # between GitHub accepting the merge and Vesta recording it. With the
        # remote unobservable, a retry must refuse to guess — never a silent
        # second merge, never a false "merged".
        from opaihub.idempotency import begin, operation_key

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                raise RuntimeError("network unreachable")
            raise AssertionError("must not dispatch while uncertain")

        adapter = self._adapter(run)
        key = operation_key("merge_pr", root=str(self.root), pr=9, method="squash")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        self.assertEqual(status(self.root, key)["state"], "in_flight")
        with self.assertRaises(RuntimeError) as caught:
            adapter.merge_pr(9)
        self.assertIn("may already be merged", str(caught.exception))


class GithubRequestReviewToolTests(unittest.TestCase):
    """provider_tools._github_request_review: requesting a review notifies
    real people under the user's account. A retried turn must not notify
    them again (#616).
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

    def test_a_retried_turn_does_not_notify_twice(self) -> None:
        calls: list[dict] = []

        def fake_request(root, number, reviewers, **kwargs):
            calls.append({"number": number, "reviewers": list(reviewers)})
            return {"ok": True, "requested": list(reviewers)}

        executor = self._executor()
        executor.grant_command_once("gh pr edit 7 --add-reviewer alice")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            first = executor._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertTrue(first["ok"], first)
        # The retry needs no fresh grant: the completed record answers first.
        second = executor._github_request_review({"number": 7, "reviewers": ["alice"]})
        self.assertTrue(second["ok"], second)
        self.assertIn("Already requested", second["message"])
        self.assertEqual(len(calls), 1)

    def test_reviewer_order_does_not_create_a_second_operation(self) -> None:
        calls: list[dict] = []

        def fake_request(root, number, reviewers, **kwargs):
            calls.append({"number": number, "reviewers": list(reviewers)})
            return {"ok": True, "requested": list(reviewers)}

        executor = self._executor()
        executor.grant_command_once("gh pr edit 7 --add-reviewer alice,bob")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            executor._github_request_review(
                {"number": 7, "reviewers": ["alice", "bob"]}
            )
        second = executor._github_request_review(
            {"number": 7, "reviewers": ["bob", "alice"]}
        )
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(calls), 1)

    def test_a_failed_request_leaves_a_retry_free(self) -> None:
        attempts: list[int] = []

        def fake_request(root, number, reviewers, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                return {"ok": False, "error": "reviewer not found"}
            return {"ok": True, "requested": list(reviewers)}

        executor = self._executor()
        executor.grant_command_once("gh pr edit 7 --add-reviewer alice")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            failed = executor._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertEqual(failed["error_code"], "GITHUB_WRITE_FAILED")
        executor.grant_command_once("gh pr edit 7 --add-reviewer alice")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            retry = executor._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(len(attempts), 2)

    def test_an_unconfirmed_attempt_fails_closed_as_uncertain(self) -> None:
        from opaihub.idempotency import begin, operation_key

        executor = self._executor()
        key = operation_key(
            "github_request_review", root=str(self.root), number=7, reviewers="alice"
        )
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        self.assertEqual(status(self.root, key)["state"], "in_flight")
        result = executor._github_request_review({"number": 7, "reviewers": ["alice"]})
        self.assertEqual(result["error_code"], "REVIEW_REQUEST_UNCERTAIN")
        self.assertIn("may already be notified", result["message"])

    def test_an_unapproved_retry_still_asks_for_approval_not_uncertain(self) -> None:
        # No grant at all: both calls must hit COMMAND_NEEDS_APPROVAL, never a
        # false "uncertain" — nothing was ever sent to GitHub either time.
        executor = self._executor()
        first = executor._github_request_review({"number": 7, "reviewers": ["alice"]})
        second = executor._github_request_review({"number": 7, "reviewers": ["alice"]})
        for result in (first, second):
            self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")


class GitPushToolTests(unittest.TestCase):
    """provider_tools._git_push (#616): a push publishes history to the
    remote. A lost response is not proof the ref was never updated, so the
    operation is persisted before dispatch and an uncertain push is
    reconciled against the remote — never blindly repeated.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name), commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self):
        from opaihub.provider_tools import RepositoryToolExecutor

        # allow_edits=True so the repository-safety handle is established;
        # the mutation gate fails closed without it.
        return RepositoryToolExecutor(self.root, allow_edits=True, allow_git_ops=True)

    def _grant(self, executor, branch="feat/x"):
        executor.grant_command_once(f"git push -u origin {branch}")

    def test_an_approved_replay_resolves_to_the_recorded_push(self) -> None:
        dispatches: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                dispatches.append(list(argv))
                return {"ok": True, "output": "pushed"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            self._grant(executor)
            first = executor._git_push({"branch": "feat/x"})
            self._grant(executor)
            second = executor._git_push({"branch": "feat/x"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertIn("Already pushed", second["message"])
        self.assertEqual(len(dispatches), 1)

    def test_new_commits_are_a_new_operation(self) -> None:
        heads = iter(["abc123", "def456"])
        dispatches: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": next(heads)}
            if argv[0] == "push":
                dispatches.append(list(argv))
                return {"ok": True, "output": "pushed"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            for _ in range(2):
                self._grant(executor)
                result = executor._git_push({"branch": "feat/x"})
                self.assertTrue(result["ok"], result)
        self.assertEqual(len(dispatches), 2)

    def test_a_lost_push_that_landed_is_confirmed_not_repeated(self) -> None:
        # Push reports failure (timeout), but the remote carries the head:
        # reconciliation confirms the landing instead of dispatching again.
        dispatches: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                dispatches.append(list(argv))
                return {"ok": False, "output": "timed out"}
            if argv[0] == "ls-remote":
                return {"ok": True, "output": "abc123\trefs/heads/feat/x"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            self._grant(executor)
            result = executor._git_push({"branch": "feat/x"})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on origin", result["message"])
        self.assertEqual(len(dispatches), 1)

    def test_a_lost_push_that_never_landed_leaves_retry_free(self) -> None:
        # Push reports failure and the reachable remote does not carry the
        # head: ref updates are atomic, so the push provably did not land.
        dispatches: list[list[str]] = []
        attempts: list[int] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                attempts.append(1)
                if len(attempts) == 1:
                    return {"ok": False, "output": "connection reset"}
                dispatches.append(list(argv))
                return {"ok": True, "output": "pushed"}
            if argv[0] == "ls-remote":
                return {"ok": True, "output": "999fff\trefs/heads/feat/x"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            self._grant(executor)
            failed = executor._git_push({"branch": "feat/x"})
            self.assertEqual(failed["error_code"], "GIT_PUSH_FAILED")
            self._grant(executor)
            retry = executor._git_push({"branch": "feat/x"})
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(len(dispatches), 1)

    def test_an_unobservable_remote_fails_closed_as_uncertain(self) -> None:
        # Push reports failure AND the remote cannot be checked: the push may
        # have landed, so the operation stays in_flight and a retry is told
        # to reconcile — never silently dispatched again.
        dispatches: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                dispatches.append(list(argv))
                return {"ok": False, "output": "timed out"}
            if argv[0] == "ls-remote":
                return {"ok": False, "output": "could not resolve host"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            self._grant(executor)
            first = executor._git_push({"branch": "feat/x"})
            self.assertEqual(first["error_code"], "PUSH_STATE_UNCERTAIN")
            self._grant(executor)
            second = executor._git_push({"branch": "feat/x"})
        self.assertEqual(second["error_code"], "PUSH_STATE_UNCERTAIN")
        self.assertIn("did not confirm", second["message"])
        self.assertEqual(len(dispatches), 1)

    def test_an_unconfirmed_attempt_reconciles_before_any_redispatch(self) -> None:
        # Crash between dispatch and record: the key is in_flight. The next
        # attempt observes the remote first; finding the head there turns the
        # retry into a confirmation without a second push.
        from opaihub.idempotency import begin, operation_key

        dispatches: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                dispatches.append(list(argv))
                return {"ok": True, "output": "pushed"}
            if argv[0] == "ls-remote":
                return {"ok": True, "output": "abc123\trefs/heads/feat/x"}
            return {"ok": True, "output": ""}

        executor = self._executor()
        key = operation_key(
            "git_push", root=str(self.root), branch="feat/x", head="abc123"
        )
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        with mock.patch.object(executor, "_git", side_effect=fake_git):
            self._grant(executor)
            result = executor._git_push({"branch": "feat/x"})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on origin", result["message"])
        self.assertEqual(len(dispatches), 0)


class GrantedCommandToolTests(unittest.TestCase):
    """provider_tools._run_granted_command (#616): a granted confirm-class
    command is an arbitrary side effect — it can write files, push, call gh.
    The operation must be persisted before dispatch, and a lost process
    status (timeout kill) must fail closed rather than invite a blind rerun
    while the command's effects may already exist.
    """

    COMMAND = "git push origin HEAD"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name), commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self, git_run):
        from opaihub.provider_tools import RepositoryToolExecutor

        return RepositoryToolExecutor(
            self.root, allow_edits=True, allow_git_ops=True, git_run=git_run
        )

    def _invoke(self, executor):
        executor.grant_command_once(self.COMMAND)
        return executor.invoke("run_command", {"command": self.COMMAND})

    def test_a_replayed_granted_command_is_not_run_twice(self) -> None:
        import subprocess

        dispatches: list[list[str]] = []

        def fake_run(argv, **kwargs):
            dispatches.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "done", "")

        executor = self._executor(fake_run)
        first = self._invoke(executor)
        second = self._invoke(executor)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertIn("not repeated", second["message"])
        self.assertEqual(len(dispatches), 1)

    def test_a_timeout_leaves_the_operation_uncertain_not_rerunnable(self) -> None:
        import subprocess

        dispatches: list[list[str]] = []

        def fake_run(argv, **kwargs):
            dispatches.append(list(argv))
            raise subprocess.TimeoutExpired(argv, 30)

        executor = self._executor(fake_run)
        first = self._invoke(executor)
        self.assertEqual(first["error_code"], "TIMEOUT")
        self.assertIn("effects may already exist", first["message"])
        second = self._invoke(executor)
        self.assertEqual(second["error_code"], "COMMAND_STATE_UNCERTAIN")
        self.assertEqual(len(dispatches), 1)

    def test_a_spawn_failure_leaves_the_retry_free(self) -> None:
        import subprocess

        attempts: list[int] = []

        def fake_run(argv, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("executable not found")
            return subprocess.CompletedProcess(argv, 0, "done", "")

        executor = self._executor(fake_run)
        first = self._invoke(executor)
        self.assertEqual(first["error_code"], "SPAWN_FAILED")
        second = self._invoke(executor)
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(attempts), 2)

    def test_an_observed_failure_allows_a_deliberate_new_attempt(self) -> None:
        import subprocess

        attempts: list[int] = []

        def fake_run(argv, **kwargs):
            attempts.append(1)
            code = 1 if len(attempts) == 1 else 0
            return subprocess.CompletedProcess(argv, code, "", "boom")

        executor = self._executor(fake_run)
        first = self._invoke(executor)
        self.assertEqual(first["error_code"], "COMMAND_FAILED")
        # The exit status was observed, so continuity is not in doubt: the
        # fresh explicit grant is the deliberate new attempt, and it runs.
        second = self._invoke(executor)
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(attempts), 2)


class WriteFileToolTests(unittest.TestCase):
    """provider_tools._write_file (#616): a file write is a filesystem
    mutation. The operation is keyed on the intended content, so a replay
    resolves to the record, and a crash mid-write is reconciled against the
    bytes on disk — never a blind rewrite over content that may now be the
    user's.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name), commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self):
        from opaihub.provider_tools import RepositoryToolExecutor

        return RepositoryToolExecutor(self.root, allow_edits=True)

    def _key(self, path: str, content: str) -> str:
        import hashlib

        from opaihub.idempotency import operation_key

        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return operation_key("write_file", root=str(self.root), path=path, sha=sha)

    def test_an_identical_rewrite_resolves_to_the_record(self) -> None:
        executor = self._executor()
        first = executor._write_file({"path": "a.txt", "content": "one"})
        second = executor._write_file({"path": "a.txt", "content": "one"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertIn("Already wrote", second["message"])

    def test_new_content_is_a_new_operation(self) -> None:
        executor = self._executor()
        executor._write_file({"path": "a.txt", "content": "one"})
        result = executor._write_file({"path": "a.txt", "content": "two"})
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "two")

    def test_a_crash_after_landing_is_confirmed_from_disk(self) -> None:
        # Process died between writing the bytes and recording them: the key
        # is in_flight while the file already holds the intended content.
        from opaihub.idempotency import begin

        (self.root / "a.txt").write_text("one", encoding="utf-8", newline="\n")
        key = self._key("a.txt", "one")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        result = self._executor()._write_file({"path": "a.txt", "content": "one"})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on disk", result["message"])

    def test_a_crash_before_landing_leaves_the_retry_free(self) -> None:
        from opaihub.idempotency import begin

        key = self._key("a.txt", "one")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        result = self._executor()._write_file({"path": "a.txt", "content": "one"})
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "one")

    def test_different_content_on_disk_fails_closed(self) -> None:
        from opaihub.idempotency import begin

        # The file holds something the operation cannot explain — a partial
        # write or a user's edit — so the retry must not overwrite it blind.
        (self.root / "a.txt").write_text("someone else", encoding="utf-8")
        key = self._key("a.txt", "one")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        result = self._executor()._write_file({"path": "a.txt", "content": "one"})
        self.assertEqual(result["error_code"], "WRITE_STATE_UNCERTAIN")
        self.assertEqual(
            (self.root / "a.txt").read_text(encoding="utf-8"), "someone else"
        )


class ApplyPatchToolTests(unittest.TestCase):
    """provider_tools._apply_patch (#616): git apply is all-or-nothing, so a
    lost response is reconcilable — forward check proves 'never landed',
    reverse check proves 'fully landed', anything else fails closed.
    """

    PATCH = (
        "diff --git a/a.txt b/a.txt\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "+line2 changed\n"
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(
            Path(self._tmp.name), files={"a.txt": "line1\nline2\n"}, commit=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _executor(self):
        from opaihub.provider_tools import RepositoryToolExecutor

        return RepositoryToolExecutor(self.root, allow_edits=True)

    def _key(self) -> str:
        import hashlib

        from opaihub.idempotency import operation_key

        sha = hashlib.sha256(self.PATCH.encode("utf-8")).hexdigest()
        return operation_key("apply_patch", root=str(self.root), sha=sha)

    def _apply_on_disk(self) -> None:
        import subprocess

        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=self.PATCH,
            text=True,
            check=True,
            capture_output=True,
        )

    def test_a_replayed_patch_is_not_applied_twice(self) -> None:
        executor = self._executor()
        first = executor._apply_patch({"patch": self.PATCH})
        self.assertTrue(first["ok"], first)
        second = executor._apply_patch({"patch": self.PATCH})
        self.assertTrue(second["ok"], second)
        self.assertIn("already applied", second["message"])
        self.assertIn(
            "line2 changed", (self.root / "a.txt").read_text(encoding="utf-8")
        )

    def test_a_crash_after_landing_is_confirmed_by_reverse_check(self) -> None:
        from opaihub.idempotency import begin

        self._apply_on_disk()
        self.assertEqual(begin(self.root, self._key())["state"], "fresh")
        result = self._executor()._apply_patch({"patch": self.PATCH})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on disk", result["message"])

    def test_a_crash_before_landing_leaves_the_retry_free(self) -> None:
        from opaihub.idempotency import begin

        self.assertEqual(begin(self.root, self._key())["state"], "fresh")
        result = self._executor()._apply_patch({"patch": self.PATCH})
        self.assertTrue(result["ok"], result)
        self.assertIn(
            "line2 changed", (self.root / "a.txt").read_text(encoding="utf-8")
        )

    def test_an_unexplainable_file_state_fails_closed(self) -> None:
        from opaihub.idempotency import begin

        # Hand-edited to match neither the unpatched nor the patched state.
        (self.root / "a.txt").write_text("something\nelse entirely\n", encoding="utf-8")
        self.assertEqual(begin(self.root, self._key())["state"], "fresh")
        result = self._executor()._apply_patch({"patch": self.PATCH})
        self.assertEqual(result["error_code"], "PATCH_STATE_UNCERTAIN")
        self.assertEqual(
            (self.root / "a.txt").read_text(encoding="utf-8"),
            "something\nelse entirely\n",
        )

    def test_an_invalid_patch_is_still_a_plain_validation_error(self) -> None:
        executor = self._executor()
        result = executor._apply_patch(
            {"patch": "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ bad\n"}
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "PATCH_CHECK_FAILED")


class GithubAdapterReconcileTests(unittest.TestCase):
    """#616 reconciliation for the gh-CLI adapter: a lost response is settled
    by observing GitHub, never by guessing.
    """

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

    def test_a_lost_merge_that_landed_is_confirmed_not_repeated(self) -> None:
        dispatches: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"state": "MERGED"}'
            dispatches.append(list(args))
            raise RuntimeError("network connection lost")

        adapter = self._adapter(run)
        # The merge "failed" from gh's perspective, but GitHub says MERGED:
        # the answer is the confirmation, not a second merge.
        self.assertEqual(adapter.merge_pr(9), "confirmed merged")
        self.assertEqual(len(dispatches), 1)

    def test_an_unconfirmed_merge_with_an_open_pr_retries_safely(self) -> None:
        from opaihub.idempotency import begin, operation_key

        dispatches: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"state": "OPEN"}'
            dispatches.append(list(args))
            return "merged"

        adapter = self._adapter(run)
        key = operation_key("merge_pr", root=str(self.root), pr=9, method="squash")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        self.assertEqual(adapter.merge_pr(9), "merged")
        self.assertEqual(len(dispatches), 1)

    def test_a_lost_comment_that_landed_is_confirmed_not_repeated(self) -> None:
        dispatches: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"comments": [{"body": "Looks good to me"}]}'
            dispatches.append(list(args))
            raise RuntimeError("network connection lost")

        adapter = self._adapter(run)
        self.assertEqual(
            adapter.comment_pr(4, "Looks good to me"), "confirmed on GitHub"
        )
        self.assertEqual(len(dispatches), 1)

    def test_an_unconfirmed_comment_absent_on_github_posts_normally(self) -> None:
        from opaihub.idempotency import begin, operation_key

        dispatches: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"comments": []}'
            dispatches.append(list(args))
            return "posted"

        adapter = self._adapter(run)
        key = operation_key("comment_pr", root=str(self.root), pr=4, body="LGTM")
        self.assertEqual(begin(self.root, key)["state"], "fresh")
        self.assertEqual(adapter.comment_pr(4, "LGTM"), "posted")
        self.assertEqual(len(dispatches), 1)

    def test_a_replayed_pr_edit_resolves_to_the_record(self) -> None:
        edits: list[list[str]] = []

        def run(args, **kwargs):
            edits.append(list(args))
            return "edited"

        adapter = self._adapter(run)
        adapter.update_pr(3, title="New title", body="New body")
        adapter.update_pr(3, title="New title", body="New body")
        self.assertEqual(len(edits), 1)

    def test_a_lost_pr_edit_that_landed_is_confirmed_not_repeated(self) -> None:
        edits: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"title": "New title", "body": "New body"}'
            edits.append(list(args))
            raise RuntimeError("network connection lost")

        adapter = self._adapter(run)
        self.assertEqual(
            adapter.update_pr(3, title="New title", body="New body"),
            "confirmed on GitHub",
        )
        self.assertEqual(len(edits), 1)


class GithubToolReconcileTests(unittest.TestCase):
    """#616 reconciliation for the provider_tools GitHub writes: transport
    loss marks the operation uncertain, and the next attempt settles it by
    reading GitHub rather than re-posting blind.
    """

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

    def test_a_lost_pr_creation_reconciles_to_the_existing_pr(self) -> None:
        creates: list[dict] = []

        def fake_create(root, **kwargs):
            creates.append(kwargs)
            return {"ok": False, "uncertain": True, "error": "response lost"}

        def fake_find(root, *, head, base="main", **kwargs):
            return {
                "ok": True,
                "found": True,
                "url": "https://example/pr/9",
                "number": 9,
            }

        executor = self._executor()
        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            first = executor._open_pr({"title": "Fix", "base": "main"})
        self.assertEqual(first["error_code"], "PR_STATE_UNCERTAIN")
        with mock.patch(
            "opaihub.github_connector.find_pull_request", side_effect=fake_find
        ):
            second = executor._open_pr({"title": "Fix", "base": "main"})
        self.assertTrue(second["ok"], second)
        self.assertIn("confirmed on GitHub", second["message"])
        self.assertEqual(len(creates), 1)

    def test_a_lost_pr_creation_with_no_pr_on_github_retries(self) -> None:
        calls: list[dict] = []

        def fake_create(root, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"ok": False, "uncertain": True, "error": "response lost"}
            return {"ok": True, "url": "https://example/pr/9", "number": 9}

        def fake_find(root, *, head, base="main", **kwargs):
            return {"ok": True, "found": False}

        executor = self._executor()
        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            first = executor._open_pr({"title": "Fix", "base": "main"})
            self.assertEqual(first["error_code"], "PR_STATE_UNCERTAIN")
            with mock.patch(
                "opaihub.github_connector.find_pull_request", side_effect=fake_find
            ):
                second = executor._open_pr({"title": "Fix", "base": "main"})
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(calls), 2)

    def test_a_lost_comment_reconciles_to_the_existing_comment(self) -> None:
        posts: list[dict] = []

        def fake_add(root, number, body, **kwargs):
            posts.append({"number": number, "body": body})
            return {"ok": False, "uncertain": True, "error": "response lost"}

        def fake_find(root, number, *, body, **kwargs):
            return {"ok": True, "found": True, "url": "https://example/pr/5#c1"}

        executor = self._executor()
        executor.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            first = executor._github_comment({"number": 5, "body": "hi"})
        self.assertEqual(first["error_code"], "COMMENT_STATE_UNCERTAIN")
        with mock.patch("opaihub.github_connector.find_comment", side_effect=fake_find):
            second = executor._github_comment({"number": 5, "body": "hi"})
        self.assertTrue(second["ok"], second)
        self.assertIn("confirmed", second["message"])
        self.assertEqual(len(posts), 1)

    def test_a_lost_review_request_reconciles_to_requested_reviewers(self) -> None:
        calls: list[dict] = []

        def fake_request(root, number, reviewers, **kwargs):
            calls.append({"number": number})
            return {"ok": False, "uncertain": True, "error": "response lost"}

        def fake_find(root, number, reviewers, **kwargs):
            return {"ok": True, "found": True}

        executor = self._executor()
        executor.grant_command_once("gh pr edit 7 --add-reviewer alice")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            first = executor._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertEqual(first["error_code"], "REVIEW_REQUEST_UNCERTAIN")
        with mock.patch(
            "opaihub.github_connector.find_requested_reviewers", side_effect=fake_find
        ):
            second = executor._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertTrue(second["ok"], second)
        self.assertIn("confirmed", second["message"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
