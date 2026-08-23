from __future__ import annotations

import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from queue import Empty
from typing import Any

from opaihub.eval_harness import eval_path, read_scorecard, run_eval


_FIXTURES = [
    {
        "task": "show git status and summarize the diff",
        "expected_max_tier": "L1",
    }
]


def _write_scorecard(
    root: str,
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        scorecard = run_eval(Path(root), fixtures=_FIXTURES, write=True)
        results.put(
            (
                "ok",
                scorecard["evaluation_id"],
                scorecard["publication_sequence"],
            )
        )
    except BaseException as exc:  # pragma: no cover - reported to the parent
        results.put(("error", type(exc).__name__, str(exc)))


def _read_scorecards(
    root: str,
    start: Any,
    stop: Any,
    ready: Any,
    results: Any,
) -> None:
    ready.set()
    start.wait()
    reads = 0
    degraded: list[str] = []
    try:
        while not stop.is_set():
            status = read_scorecard(Path(root))
            reads += 1
            if status["state"] != "ready":
                degraded.append(str(status.get("reason") or status["state"]))
            time.sleep(0.001)
        results.put(("ok", reads, degraded))
    except BaseException as exc:  # pragma: no cover - reported to the parent
        results.put(("error", type(exc).__name__, str(exc)))


class EvalScorecardPublicationTests(unittest.TestCase):
    def test_scorecards_carry_unique_evaluation_identity_and_publish_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run_eval(root, fixtures=_FIXTURES, write=True)
            second = run_eval(root, fixtures=_FIXTURES, write=True)
            status = read_scorecard(root)

        self.assertNotEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertEqual(first["publication_sequence"], 1)
        self.assertEqual(second["publication_sequence"], 2)
        self.assertIn("evaluation_started_at", second)
        self.assertIn("published_at", second)
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["scorecard"]["evaluation_id"], second["evaluation_id"])

    def test_malformed_scorecard_is_reported_as_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = eval_path(root)
            path.parent.mkdir(parents=True)
            path.write_text('{"report": ', encoding="utf-8")

            status = read_scorecard(root)

        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["reason"], "scorecard_invalid_json")
        self.assertEqual(status["path"], str(path))
        self.assertNotIn("scorecard", status)

    def test_concurrent_publications_are_atomic_and_monotonically_ordered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial = run_eval(root, fixtures=_FIXTURES, write=True)
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            stop = context.Event()
            reader_ready = context.Event()
            writer_results = context.Queue()
            reader_results = context.Queue()
            writers = [
                context.Process(
                    target=_write_scorecard,
                    args=(str(root), start, writer_results),
                )
                for _ in range(4)
            ]
            reader = context.Process(
                target=_read_scorecards,
                args=(str(root), start, stop, reader_ready, reader_results),
            )

            reader.start()
            for writer in writers:
                writer.start()
            self.assertTrue(reader_ready.wait(timeout=10))
            start.set()

            publications = []
            try:
                for _ in writers:
                    publications.append(writer_results.get(timeout=30))
            except Empty as exc:  # pragma: no cover - timeout is the assertion
                self.fail(f"concurrent scorecard writer timed out: {exc}")
            finally:
                for writer in writers:
                    writer.join(timeout=30)
                stop.set()
                reader.join(timeout=30)

            try:
                reader_result = reader_results.get(timeout=5)
            except Empty as exc:  # pragma: no cover - timeout is the assertion
                self.fail(f"concurrent scorecard reader timed out: {exc}")

            final_status = read_scorecard(root)
            persisted = json.loads(eval_path(root).read_text(encoding="utf-8"))

        self.assertTrue(all(result[0] == "ok" for result in publications))
        self.assertEqual([writer.exitcode for writer in writers], [0, 0, 0, 0])
        self.assertEqual(reader.exitcode, 0)
        self.assertEqual(reader_result[0], "ok")
        self.assertGreater(reader_result[1], 0)
        self.assertEqual(reader_result[2], [])
        self.assertEqual(
            sorted(result[2] for result in publications),
            list(
                range(
                    initial["publication_sequence"] + 1,
                    initial["publication_sequence"] + len(writers) + 1,
                )
            ),
        )
        self.assertEqual(final_status["state"], "ready")
        self.assertEqual(final_status["scorecard"], persisted)
        self.assertIn(
            persisted["evaluation_id"],
            {result[1] for result in publications},
        )
        self.assertEqual(persisted["publication_sequence"], 5)


if __name__ == "__main__":
    unittest.main()
