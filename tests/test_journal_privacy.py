"""#613 functional requirement 9: minimise sensitive payload content.

    Encrypt or minimise sensitive payload content according to #527; raw
    prompts/source should not be required for runtime recovery.

The schema has carried ``privacy_class`` on events since Stage 1, and until now
nothing set it and nothing enforced it -- every event was ``internal`` whatever
it held.

The gap was demonstrated rather than argued. A task reading ``fix my auth, the
key is sk-ant-...`` put that key verbatim into ``journal.sqlite3``, found by
reading the raw file back and searching its bytes:

    secret present in the journal on disk: True
      ...task-agui...fix my auth, the key is sk-ant-api03-...  and it 401s

**These tests read the file, not the API.** That distinction is the whole point.
A test asserting that ``redact()`` was called proves the call happened; it says
nothing about what a person holding the database would find. Secrets reach disk
through the main file, through the WAL, and through any sidecar SQLite decides
to write, so the only honest assertion is over the bytes of all of them.

The remaining limits are stated in the tests rather than left implied: redaction
catches shapes it recognises, and a long private paste that matches none of them
survives it. Truncation bounds that without pretending to solve it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opaihub import journal_runtime
from opaihub.journal_runtime import (
    EVENT_ADMITTED,
    EVENT_CANCELLED,
    EVENT_FINISHED,
    EVENT_STARTED,
    EVENT_TRANSITIONED,
    EVENT_VERIFIED,
    PRIVACY_INTERNAL,
    PRIVACY_SENSITIVE,
    privacy_class_for,
    record_admission,
    record_event,
    record_terminal,
)
from opaihub.journal_store import (
    MAX_PAYLOAD_STRING,
    append_event,
    journal_path,
    minimise,
    open_store,
)

NOW = "2026-08-26T12:00:00+00:00"

#: Fake credentials in the shapes the sanctioned redactor recognises. None of
#: these is real; each is here because a user could plausibly paste one into a
#: prompt while asking why it does not work.
SECRETS = {
    "anthropic": "sk-ant-api03-THISISAFAKESECRETVALUE1234567890",  # pragma: allowlist secret
    "openai": "sk-proj-FAKE1234567890abcdefFAKE1234567890",  # pragma: allowlist secret
    "github": "ghp_FAKEABCDEFGHIJKLMNOPQRSTUVWXYZ012345",  # pragma: allowlist secret
    "aws": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
}


class _PrivacyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _bytes_on_disk(self) -> bytes:
        """Everything SQLite has written, main file and sidecars alike.

        Reading only ``journal.sqlite3`` would miss a recent commit still
        living in the WAL -- which is exactly where a just-written secret is.
        """

        path = journal_path(self.root)
        raw = path.read_bytes() if path.exists() else b""
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                raw += sidecar.read_bytes()
        return raw

    def _assert_absent(self, secret: str) -> None:
        # ``assertNotIn`` on bytes puts the whole haystack in the failure
        # message, which for a SQLite file is hundreds of kilobytes of binary
        # in the test output. The interesting part is the neighbourhood.
        raw = self._bytes_on_disk()
        index = raw.find(secret.encode())
        if index == -1:
            return
        context = raw[max(0, index - 60) : index + len(secret) + 20]
        self.fail(
            "the secret is readable in the journal file on disk, near: "
            + context.decode("ascii", "replace")
        )


class SecretsInPromptsNeverReachDiskTests(_PrivacyFixture):
    """The demonstrated leak, closed and pinned."""

    def test_a_key_pasted_into_a_task_is_not_stored(self):
        record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task=f"fix my auth, the key is {SECRETS['anthropic']} and it 401s",
            now=NOW,
        )

        self._assert_absent(SECRETS["anthropic"])

    def test_every_recognised_credential_shape_is_scrubbed(self):
        for name, secret in SECRETS.items():
            with self.subTest(kind=name):
                record_admission(
                    self.root,
                    task_id=f"task-{name}",
                    run_id=f"run-{name}",
                    task=f"here is my token {secret} please debug",
                    now=NOW,
                )

                self._assert_absent(secret)

    def test_the_task_is_still_recognisable_after_scrubbing(self):
        """Teeth the other way: this must not reduce every task to noise."""

        record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task=f"fix the parser in app.py, key {SECRETS['anthropic']}",
            now=NOW,
        )

        store = open_store(self.root)
        self.addCleanup(store.close)
        outcome = store.execute(
            "SELECT requested_outcome FROM tasks WHERE task_id = 'task-a'"
        ).fetchone()[0]

        self.assertIn("fix the parser in app.py", outcome)
        self.assertNotIn(SECRETS["anthropic"], outcome)


class SecretsInEventPayloadsNeverReachDiskTests(_PrivacyFixture):
    """The store boundary, so that no call site can forget."""

    def _fence(self) -> int:
        return record_admission(
            self.root, task_id="t", run_id="run-a", task="a task", now=NOW
        )

    def test_a_secret_in_an_event_payload_is_scrubbed(self):
        fence = self._fence()

        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            payload={"note": f"retrying with {SECRETS['openai']}"},
            now=NOW,
            fence=fence,
        )

        self._assert_absent(SECRETS["openai"])

    def test_a_secret_in_a_terminal_reason_is_scrubbed(self):
        """Reasons carry provider errors, which quote the request that failed."""

        fence = self._fence()

        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="failed",
            reason=f"401 from provider using {SECRETS['anthropic']}",
            now=NOW,
            fence=fence,
        )

        self._assert_absent(SECRETS["anthropic"])

    def test_a_secret_nested_deep_in_a_payload_is_scrubbed(self):
        """Payloads are not flat, and a recursive walk is the only honest one."""

        fence = self._fence()

        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            payload={
                "request": {
                    "headers": [{"name": "authorization", "value": SECRETS["github"]}]
                }
            },
            now=NOW,
            fence=fence,
        )

        self._assert_absent(SECRETS["github"])

    def test_a_secret_used_as_a_payload_key_is_scrubbed(self):
        """Unusual, which is exactly where this kind of thing hides."""

        fence = self._fence()

        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            payload={SECRETS["aws"]: "seen in the environment"},
            now=NOW,
            fence=fence,
        )

        self._assert_absent(SECRETS["aws"])

    def test_ordinary_payload_content_survives_untouched(self):
        """Teeth: minimisation must not eat the data the journal exists for."""

        fence = self._fence()

        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            payload={"model": "claude-opus-5", "route": "account", "attempt": 2},
            now=NOW,
            fence=fence,
        )

        store = open_store(self.root)
        self.addCleanup(store.close)
        payload = store.execute(
            "SELECT payload FROM events WHERE event_type = ?", (EVENT_STARTED,)
        ).fetchone()[0]

        self.assertIn("claude-opus-5", payload)
        self.assertIn("account", payload)


class ThePayloadHashDescribesWhatIsStoredTests(_PrivacyFixture):
    """A self-verifying record must verify the payload that actually exists."""

    def test_the_hash_is_computed_over_the_redacted_payload(self):
        import hashlib
        import json

        fence = record_admission(
            self.root, task_id="t", run_id="run-a", task="a task", now=NOW
        )
        record_event(
            self.root,
            run_id="run-a",
            event_type=EVENT_STARTED,
            payload={"note": f"key {SECRETS['openai']}"},
            now=NOW,
            fence=fence,
        )

        store = open_store(self.root)
        self.addCleanup(store.close)
        row = store.execute(
            "SELECT payload, payload_hash FROM events WHERE event_type = ?",
            (EVENT_STARTED,),
        ).fetchone()

        recomputed = hashlib.sha256(
            json.dumps(
                json.loads(row["payload"]), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(row["payload_hash"], recomputed)


class PayloadsAreBoundedTests(_PrivacyFixture):
    """Redaction catches shapes; truncation catches size. Both are needed.

    A long private paste matching no known credential shape survives the
    redactor. Bounding the length does not make that safe -- it stops the
    journal quietly becoming the transcript store this issue's non-goals
    exclude.
    """

    def test_a_very_long_string_is_truncated(self):
        fence = record_admission(
            self.root, task_id="t", run_id="run-a", task="a task", now=NOW
        )
        store = open_store(self.root)
        try:
            append_event(
                store,
                event_type=EVENT_STARTED,
                payload={"blob": "x" * 50_000},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
                expected_fence=fence,
            )
        finally:
            store.close()

        store = open_store(self.root)
        self.addCleanup(store.close)
        payload = store.execute(
            "SELECT payload FROM events WHERE event_type = ?", (EVENT_STARTED,)
        ).fetchone()[0]

        self.assertLess(len(payload), MAX_PAYLOAD_STRING + 200)

    def test_minimise_bounds_nested_strings_too(self):
        result = minimise({"outer": {"inner": ["y" * 50_000]}})

        self.assertEqual(len(result["outer"]["inner"][0]), MAX_PAYLOAD_STRING)

    def test_minimise_leaves_non_strings_alone(self):
        result = minimise({"count": 7, "ratio": 1.5, "ok": True, "none": None})

        self.assertEqual(result, {"count": 7, "ratio": 1.5, "ok": True, "none": None})

    def test_a_task_summary_stays_bounded(self):
        record_admission(
            self.root,
            task_id="task-a",
            run_id="run-a",
            task="please " * 5000,
            now=NOW,
        )

        store = open_store(self.root)
        self.addCleanup(store.close)
        outcome = store.execute(
            "SELECT requested_outcome FROM tasks WHERE task_id = 'task-a'"
        ).fetchone()[0]

        self.assertLessEqual(len(outcome), 200)


class EveryEventDeclaresItsPrivacyClassTests(unittest.TestCase):
    """A ratchet. The default protects; the table makes the default rare."""

    def test_machine_state_events_are_internal(self):
        for event_type in (
            EVENT_ADMITTED,
            EVENT_STARTED,
            EVENT_TRANSITIONED,
            journal_runtime.EVENT_COSTED,
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(privacy_class_for(event_type), PRIVACY_INTERNAL)

    def test_events_carrying_free_form_reasons_are_sensitive(self):
        for event_type in (EVENT_FINISHED, EVENT_CANCELLED, EVENT_VERIFIED):
            with self.subTest(event_type=event_type):
                self.assertEqual(privacy_class_for(event_type), PRIVACY_SENSITIVE)

    def test_an_unclassified_event_gets_the_cautious_default(self):
        """Forgetting the table must over-protect, never under-protect."""

        self.assertEqual(privacy_class_for("some.future.event"), PRIVACY_SENSITIVE)

    def test_every_event_this_module_emits_is_classified(self):
        """The ratchet itself: a new event constant must be triaged.

        Detected from the module's own constants rather than a hand-kept list,
        because a hand-kept list is the thing that goes stale.
        """

        declared = {
            value
            for name, value in vars(journal_runtime).items()
            if name.startswith("EVENT_") and isinstance(value, str)
        }

        self.assertEqual(
            sorted(declared - set(journal_runtime.EVENT_PRIVACY)),
            [],
            "these event types have no declared privacy class",
        )

    def test_the_stored_class_matches_the_declaration(self):
        """The table is only worth having if it reaches the database."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fence = record_admission(
                root, task_id="t", run_id="run-a", task="a task", now=NOW
            )
            record_terminal(
                root,
                run_id="run-a",
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
                fence=fence,
            )

            store = open_store(root)
            try:
                rows = {
                    row["event_type"]: row["privacy_class"]
                    for row in store.execute(
                        "SELECT event_type, privacy_class FROM events"
                    )
                }
            finally:
                store.close()

        self.assertEqual(rows[EVENT_ADMITTED], PRIVACY_INTERNAL)
        self.assertEqual(rows[EVENT_FINISHED], PRIVACY_SENSITIVE)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class MinimisationIsTotalTests(unittest.TestCase):
    """Every payload shape must come out storable, never raise.

    This is a contract about exceptions, not tidiness. ``append_event``'s
    callers catch a specific set -- ``sqlite3.DatabaseError``,
    ``JournalStoreError``, ``ValueError`` -- so anything else escapes the mirror
    and fails the turn it was only supposed to observe.

    An adversarial pass found two shapes that did exactly that, one of them
    introduced by minimisation itself:

    * a payload holding a reference to itself raised ``RecursionError``. Before
      minimisation existed, ``json.dumps`` refused the same payload with
      ``ValueError``, which *is* caught -- so adding a recursive walk quietly
      converted a swallowed mirror failure into an exception reaching a real
      turn.
    * a ``set`` in a payload reached ``json.dumps`` and raised ``TypeError``,
      which was never caught either.
    """

    def _stored(self, payload) -> str:
        import json

        from opaihub.journal_store import minimise as _minimise

        # json.dumps is the real gate: minimise's output goes straight into it.
        return json.dumps(_minimise(payload))

    def test_a_payload_that_refers_to_itself_is_bounded(self):
        inner: dict = {}
        inner["self"] = inner

        encoded = self._stored({"loop": inner})

        self.assertIn("TRUNCATED_DEPTH", encoded)

    def test_a_deeply_nested_payload_is_bounded(self):
        node: dict = {"leaf": "x"}
        for _ in range(2000):
            node = {"n": node}

        self._stored(node)

    def test_a_set_becomes_a_list_rather_than_an_error(self):
        encoded = self._stored({"kinds": {"a", "b"}})

        self.assertIn("a", encoded)
        self.assertIn("b", encoded)

    def test_an_arbitrary_object_becomes_its_repr(self):
        class Thing:
            def __repr__(self) -> str:
                return "<Thing key=sk-ant-api03-FAKEFAKEFAKEFAKEFAKE1234>"  # pragma: allowlist secret

        encoded = self._stored({"thing": Thing()})

        self.assertIn("Thing", encoded)
        self.assertNotIn(
            "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE1234", encoded
        )  # pragma: allowlist secret

    def test_non_finite_floats_do_not_produce_unparseable_json(self):
        """json.dumps emits bare NaN/Infinity, which strict parsers reject."""

        import json

        encoded = self._stored({"a": float("nan"), "b": float("inf")})

        json.loads(encoded)  # a strict parse, which bare NaN would fail

    def test_booleans_stay_booleans(self):
        """bool subclasses int; a careless numeric branch stores True as 1."""

        from opaihub.journal_store import minimise as _minimise

        self.assertIs(_minimise({"ok": True})["ok"], True)

    def test_minimise_does_not_mutate_the_callers_payload(self):
        from opaihub.journal_store import minimise as _minimise

        original = {"a": SECRETS["anthropic"], "n": [1, 2]}
        _minimise(original)

        self.assertEqual(original["a"], SECRETS["anthropic"])
        self.assertEqual(original["n"], [1, 2])

    def test_a_hostile_payload_still_leaves_the_run_recorded(self):
        """The contract in its consequence: the turn survives."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fence = record_admission(
                root, task_id="t", run_id="run-a", task="a task", now=NOW
            )
            loop: dict = {}
            loop["self"] = loop

            record_event(
                root,
                run_id="run-a",
                event_type=EVENT_STARTED,
                payload={"loop": loop, "kinds": {1, 2}},
                now=NOW,
                fence=fence,
            )

            store = open_store(root)
            try:
                runs = store.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            finally:
                store.close()

        self.assertEqual(runs, 1)
