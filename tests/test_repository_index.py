"""Repository-wide indexed context selection for daily-driver tasks (#312)."""

from __future__ import annotations

import json
import hashlib
import gc
import os
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from opaihub.aci import AgentComputerInterface
from opaihub.build_loop import build_edit_prompt, select_context
from opaihub.semantic_index import INDEX_VERSION, LocalSemanticIndex
from opaihub.state import state_dir


def _repo(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def _rehash_payload(payload: dict[str, object]) -> str:
    """Recompute the declared stable hash so schema validation is tested too."""

    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "index_hash", "source_persisted"}
    }
    files = payload.get("files") or {}
    stable["files"] = {
        relative: (
            {
                "content_hash": metadata["content_hash"],
                "chunk_count": metadata["chunk_count"],
            }
            if "chunk_count" in metadata
            else metadata["content_hash"]
        )
        for relative, metadata in sorted(files.items())
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RepositoryRetrievalTests(unittest.TestCase):
    def test_symbol_definition_and_use_win_without_path_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "packages/api/processor.go": (
                        "package api\n\n"
                        "type PaymentGateway struct{}\n\n"
                        "func (PaymentGateway) Charge(amount int) bool {\n"
                        "    return amount > 0\n"
                        "}\n"
                    ),
                    "a/noise.py": "def unrelated_report():\n    return 'x'\n",
                    "packages/web/client.go": (
                        "package web\n\n"
                        "func Checkout(gateway api.PaymentGateway, total int) bool {\n"
                        "    return gateway.Charge(total)\n"
                        "}\n"
                    ),
                },
            )
            manifest = {
                "name": "multi-package",
                "files": [
                    "a/noise.py",
                    "packages/api/processor.go",
                    "packages/web/client.go",
                ],
            }
            two_relevant = sum(
                len((root / path).read_text(encoding="utf-8"))
                for path in (
                    "packages/api/processor.go",
                    "packages/web/client.go",
                )
            )

            selection = select_context(
                root,
                "Make PaymentGateway retries visible to its callers",
                manifest,
                budget_chars=two_relevant,
            )

        paths = [item["path"] for item in selection["files"]]
        self.assertEqual(
            paths,
            ["packages/api/processor.go", "packages/web/client.go"],
        )
        self.assertEqual(selection["index"]["state"], "ready")
        self.assertGreater(selection["retrieval_quality"]["definition_hits"], 0)
        self.assertGreater(selection["retrieval_quality"]["usage_hits"], 0)
        self.assertLessEqual(selection["chars_selected"], selection["budget_chars"])
        self.assertTrue(selection["within_budget"])

    def test_oversized_top_hit_is_a_bounded_relevant_slice(self) -> None:
        marker = "class RareTargetSymbol:\n    pass\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {"src/large.py": ("# filler\n" * 400) + marker + ("# tail\n" * 400)},
            )
            selection = select_context(
                root,
                "change RareTargetSymbol",
                {"name": "large", "files": ["src/large.py"]},
                budget_chars=240,
            )

        self.assertEqual(len(selection["files"]), 1)
        self.assertEqual(selection["files"][0]["path"], "src/large.py")
        self.assertIn("RareTargetSymbol", selection["files"][0]["content"])
        self.assertTrue(selection["files"][0]["truncated"])
        self.assertEqual(selection["files"][0]["kind"], "indexed_slice")
        self.assertLessEqual(selection["chars_selected"], 240)

    def test_every_truncated_selection_is_labelled_non_editable_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"src/large.py": "value = 1\n" * 100})
            manifest = {"name": "large", "files": ["src/large.py"]}
            selection = select_context(root, "", manifest, budget_chars=40)
            prompt = build_edit_prompt("inspect", manifest, selection)

        self.assertTrue(selection["files"][0]["truncated"])
        self.assertIn("```context:src/large.py", prompt)
        self.assertNotIn("```file:src/large.py", prompt)
        self.assertIn("never return an excerpt", prompt)

    def test_comment_is_not_a_definition_and_real_definition_ranks_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a_comment.py": "# class TargetSymbol is documented here\n",
                    "z_real.py": "class TargetSymbol:\n    pass\n",
                },
            )
            index = LocalSemanticIndex(root)
            index.build()
            results = index.search("TargetSymbol", limit=2)

        self.assertEqual(results[0]["path"], "z_real.py")
        self.assertEqual(results[0]["match_kind"], "definition")
        comment = next(item for item in results if item["path"] == "a_comment.py")
        self.assertNotEqual(comment["match_kind"], "definition")

    def test_comment_symbol_is_semantic_text_not_an_exact_usage(self) -> None:
        use = "def caller():\n    return TargetSymbol()\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a_comment.py": (
                        'label = "TargetSymbol"\nvalue = 1  # TargetSymbol\n'
                    ),
                    "z_use.py": use,
                },
            )
            index = LocalSemanticIndex(root)
            retrieved = index.retrieve("TargetSymbol", limit=2)
            selection = select_context(
                root,
                "TargetSymbol",
                {"name": "usage", "files": ["a_comment.py", "z_use.py"]},
                budget_chars=len(use),
            )

        self.assertEqual(retrieved["results"][0]["path"], "z_use.py")
        comment = next(
            item for item in retrieved["results"] if item["path"] == "a_comment.py"
        )
        self.assertEqual(comment["match_kind"], "semantic")
        self.assertEqual(retrieved["quality"]["usage_hits"], 1)
        self.assertEqual([item["path"] for item in selection["files"]], ["z_use.py"])

    def test_multiline_comment_is_not_a_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a_comment.js": "/*\nclass TargetSymbol {}\n*/\n",
                    "z_real.js": "class TargetSymbol {}\n",
                },
            )
            index = LocalSemanticIndex(root)
            index.build()
            results = index.search("TargetSymbol", limit=2)

        self.assertEqual(results[0]["path"], "z_real.js")
        self.assertEqual(results[0]["match_kind"], "definition")
        comment = next(item for item in results if item["path"] == "a_comment.js")
        self.assertNotEqual(comment["match_kind"], "definition")

    def test_definition_excerpt_centres_on_symbol_not_early_component_word(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "gateway.py": (
                        "# payment behavior overview\n"
                        + ("# unrelated filler text\n" * 80)
                        + "class PaymentGateway:\n    pass\n"
                    )
                },
            )
            index = LocalSemanticIndex(root, chunk_lines=120)
            index.build()
            result = index.search("change PaymentGateway", limit=1, max_chars=120)[0]

        self.assertIn("PaymentGateway", result["text"])
        self.assertLessEqual(len(result["text"]), 120)

    def test_usage_excerpt_centres_on_exact_symbol_not_early_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "checkout.py": (
                        "# payment gateway overview\n"
                        + ("unrelated_value = 1\n" * 80)
                        + "gateway = PaymentGateway()\n"
                    )
                },
            )
            index = LocalSemanticIndex(root, chunk_lines=120)
            index.build()
            result = index.search("change PaymentGateway", limit=1, max_chars=120)[0]

        self.assertEqual(result["match_kind"], "usage")
        self.assertIn("PaymentGateway", result["text"])
        self.assertLessEqual(len(result["text"]), 120)

    def test_truncated_excerpt_reports_exact_complete_line_range(self) -> None:
        source_lines = [f"filler_{line} = {line}" for line in range(1, 100)]
        source_lines[79] = "class RareTargetSymbol:"
        source_lines[80] = "    pass"
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"large.py": "\n".join(source_lines) + "\n"})
            index = LocalSemanticIndex(root, chunk_lines=120, overlap_lines=0)
            index.build()

            result = index.search("RareTargetSymbol", limit=1, max_chars=100)[0]

        expected = "\n".join(
            source_lines[result["start_line"] - 1 : result["end_line"]]
        )
        self.assertEqual(result["text"], expected)
        self.assertIn("RareTargetSymbol", result["text"])
        self.assertLessEqual(len(result["text"]), 100)
        self.assertGreater(result["start_line"], 1)
        self.assertLess(result["end_line"], len(source_lines))

    def test_long_line_excerpt_reports_exact_end_exclusive_columns(self) -> None:
        line = (
            ("prefix_value = 1; " * 20)
            + "RareTargetSymbol"
            + ("; suffix_value = 2" * 20)
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"large.py": line + "\n"})
            index = LocalSemanticIndex(root)
            index.build()

            result = index.search("RareTargetSymbol", limit=1, max_chars=80)[0]

        self.assertEqual(result["start_line"], 1)
        self.assertEqual(result["end_line"], 1)
        self.assertGreater(result["start_column"], 1)
        self.assertEqual(
            line[result["start_column"] - 1 : result["end_column"] - 1],
            result["text"],
        )
        self.assertEqual(
            result["end_column"] - result["start_column"],
            len(result["text"]),
        )

    def test_interpolation_is_code_but_plain_string_literal_is_not_usage(self) -> None:
        cases = (
            (
                "py",
                'message = "TargetSymbol"\n',
                'def render():\n    return f"{TargetSymbol}"\n',
            ),
            (
                "js",
                'const message = "TargetSymbol";\n',
                "function render() { return `${TargetSymbol}`; }\n",
            ),
        )
        for suffix, literal, use in cases:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                root = _repo(
                    Path(tmp),
                    {
                        f"a_literal.{suffix}": literal,
                        f"z_use.{suffix}": use,
                    },
                )
                index = LocalSemanticIndex(root)
                retrieved = index.retrieve("TargetSymbol", limit=2)
                selection = select_context(
                    root,
                    "TargetSymbol",
                    {
                        "name": "interpolation",
                        "files": [f"a_literal.{suffix}", f"z_use.{suffix}"],
                    },
                    budget_chars=len(use),
                )

            self.assertEqual(retrieved["results"][0]["path"], f"z_use.{suffix}")
            literal_hit = next(
                item
                for item in retrieved["results"]
                if item["path"] == f"a_literal.{suffix}"
            )
            self.assertEqual(literal_hit["match_kind"], "semantic")
            self.assertEqual(retrieved["quality"]["usage_hits"], 1)
            self.assertEqual(
                [item["path"] for item in selection["files"]], [f"z_use.{suffix}"]
            )

    def test_quality_coverage_is_union_across_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "alpha.py": "class Alpha:\n    pass\n",
                    "beta.py": "class Beta:\n    pass\n",
                },
            )
            result = LocalSemanticIndex(root).retrieve("Alpha Beta")

        self.assertEqual(result["quality"]["matched_query_terms"], 2)
        self.assertEqual(result["quality"]["coverage"], 1.0)

    def test_generic_task_words_do_not_create_exact_definition_or_usage_hits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "generic.py": "def add_feature():\n    return True\n",
                    "target.py": "class PaymentGateway:\n    pass\n",
                },
            )

            result = LocalSemanticIndex(root).retrieve(
                "Add PaymentGateway feature", limit=2
            )
            uppercase = LocalSemanticIndex(root).retrieve(
                "Please ADD a FEATURE to PaymentGateway", limit=2
            )

        generic = next(
            item for item in result["results"] if item["path"] == "generic.py"
        )
        self.assertEqual(result["results"][0]["path"], "target.py")
        self.assertEqual(generic["match_kind"], "semantic")
        self.assertEqual(generic["definition_match_count"], 0)
        self.assertEqual(generic["usage_match_count"], 0)
        self.assertEqual(result["quality"]["query_terms"], 2)
        self.assertEqual(result["quality"]["matched_query_terms"], 2)
        self.assertEqual(result["quality"]["coverage"], 1.0)
        uppercase_generic = next(
            item for item in uppercase["results"] if item["path"] == "generic.py"
        )
        self.assertEqual(uppercase_generic["match_kind"], "semantic")
        self.assertEqual(uppercase["quality"]["query_terms"], 2)
        self.assertEqual(uppercase["quality"]["matched_query_terms"], 2)

    def test_compound_symbol_keeps_exact_hashes_even_when_parts_are_stopwords(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"feature.py": "class AddFeature:\n    pass\n"})

            result = LocalSemanticIndex(root).retrieve("AddFeature", limit=1)

        self.assertEqual(result["results"][0]["match_kind"], "definition")
        self.assertEqual(result["results"][0]["definition_match_count"], 2)
        self.assertEqual(result["quality"]["query_terms"], 2)
        self.assertEqual(result["quality"]["matched_query_terms"], 2)
        self.assertEqual(result["quality"]["coverage"], 1.0)


class IncrementalIndexTests(unittest.TestCase):
    def test_reuses_unchanged_updates_changed_and_removes_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a.py": "class StableSymbol:\n    pass\n",
                    "b.py": "def changing():\n    return 1\n",
                },
            )
            index = LocalSemanticIndex(root)

            first = index.build()
            second = index.build()
            (root / "b.py").write_text(
                "def changing():\n    return 2\n", encoding="utf-8"
            )
            third = index.build()
            (root / "a.py").unlink()
            fourth = index.build()

            self.assertEqual(first["updated_files"], 2)
            self.assertEqual(first["reused_files"], 0)
            self.assertTrue(second["cache_hit"])
            self.assertEqual(second["reused_files"], 2)
            self.assertEqual(second["updated_files"], 0)
            self.assertEqual(third["updated_files"], 1)
            self.assertEqual(third["reused_files"], 1)
            self.assertEqual(fourth["deleted_files"], 1)
            self.assertEqual(index.search("StableSymbol"), [])

    def test_state_is_missing_ready_stale_and_building(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root)
            self.assertEqual(index.status()["state"], "missing")
            index.build()
            self.assertEqual(index.status()["state"], "ready")
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            self.assertEqual(index.status()["state"], "stale")
            index.building_path.parent.mkdir(parents=True, exist_ok=True)
            index.building_path.write_text("{}", encoding="utf-8")
            self.assertEqual(index.status()["state"], "building")

    def test_empty_unchanged_file_is_a_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"empty.py": ""})
            index = LocalSemanticIndex(root)

            first = index.build()
            second = index.build()

        self.assertEqual(first["updated_files"], 1)
        self.assertEqual(first["chunks"], 0)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["reused_files"], 1)
        self.assertEqual(second["updated_files"], 0)

    def test_chunk_limited_cache_stays_partial_on_consecutive_builds(self) -> None:
        source = "\n".join(
            ["early_value = 1"] * 10
            + ["class LateTarget:", "    pass"]
            + ["tail_value = 2"] * 10
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": source + "\n"})
            index = LocalSemanticIndex(
                root, chunk_lines=5, overlap_lines=0, max_chunks=1
            )

            first = index.build()
            cached, cached_state = index._load_with_state()
            second = index.build()
            status = index.status()

        self.assertEqual(first["state"], "partial")
        self.assertEqual(first["previous_state"], "missing")
        self.assertGreater(first["truncation"]["chunks_omitted"], 0)
        self.assertIsNotNone(cached)
        self.assertEqual(cached_state, "partial")
        self.assertEqual(second["state"], "partial")
        self.assertEqual(second["previous_state"], "partial")
        self.assertGreater(second["truncation"]["chunks_omitted"], 0)
        self.assertEqual(status["state"], "partial")

    def test_shared_state_directory_rejects_repository_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            outside = base / "outside-state"
            root.mkdir()
            outside.mkdir()
            try:
                os.symlink(outside, root / ".opaihub", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with self.assertRaisesRegex(OSError, "state directory"):
                state_dir(root)
            with self.assertRaisesRegex(OSError, "state directory"):
                LocalSemanticIndex(root)

        self.assertFalse((outside / "agent" / "semantic-index.json").exists())

    def test_semantic_index_rejects_symlinked_agent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            outside = base / "outside-agent"
            (root / ".opaihub").mkdir(parents=True)
            outside.mkdir()
            try:
                os.symlink(
                    outside,
                    root / ".opaihub" / "agent",
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with self.assertRaisesRegex(OSError, "semantic index state path"):
                LocalSemanticIndex(root)

        self.assertFalse((outside / "semantic-index.json").exists())

    def test_state_path_is_revalidated_immediately_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _repo(base / "repo", {"app.py": "value = 1\n"})
            outside = base / "outside-agent"
            outside.mkdir()
            index = LocalSemanticIndex(root)
            index.path.parent.parent.mkdir(parents=True)
            try:
                os.symlink(
                    outside,
                    index.path.parent,
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with self.assertRaisesRegex(OSError, "semantic index state path"):
                index.build()

            self.assertFalse((outside / "semantic-index.lock").exists())

    def test_builds_are_serialized_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class ConcurrentSafe:\n    pass\n"})
            index = LocalSemanticIndex(root)
            script = "\n".join(
                (
                    "import sys, time",
                    "from pathlib import Path",
                    "from opaihub.semantic_index import LocalSemanticIndex",
                    "index = LocalSemanticIndex(Path(sys.argv[1]))",
                    "original = index._repo_files",
                    "def slow_inventory():",
                    "    print('BUILD_LOCKED', flush=True)",
                    "    time.sleep(1.25)",
                    "    return original()",
                    "index._repo_files = slow_inventory",
                    "index.build()",
                )
            )
            child = subprocess.Popen(  # nosec B603 - fixed hermetic test argv
                [sys.executable, "-c", script, str(root)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                if child.stdout is None:
                    self.fail("subprocess stdout was not captured")
                self.assertEqual(child.stdout.readline().strip(), "BUILD_LOCKED")
                self.assertEqual(index.status()["state"], "building")
                started = time.monotonic()
                second = index.build()
                elapsed = time.monotonic() - started
                _stdout, stderr = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)

            self.assertEqual(child.returncode, 0, stderr)
            self.assertGreaterEqual(elapsed, 0.75)
            self.assertEqual(second["state"], "ready")
            self.assertEqual(index.status()["state"], "ready")
            json.loads(index.path.read_text(encoding="utf-8"))

    def test_corrupt_cache_recovers_without_persisting_source(self) -> None:
        marker = "PRIVATE_SOURCE_MARKER_314159"
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"secretless.py": f"value = '{marker}'\n"})
            index = LocalSemanticIndex(root)
            index.path.parent.mkdir(parents=True, exist_ok=True)
            index.path.write_text("{broken", encoding="utf-8")

            result = index.build()
            raw = index.path.read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertEqual(result["previous_state"], "corrupt")
        self.assertEqual(result["state"], "ready")
        self.assertNotIn(marker, raw)
        self.assertFalse(payload["source_persisted"])

    def test_oversized_cache_is_rejected_before_json_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root, max_index_bytes=4_096)
            index.path.parent.mkdir(parents=True, exist_ok=True)
            index.path.write_bytes(b" " * (index.max_index_bytes + 1))

            with mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("oversized cache should not be read"),
            ):
                data, state = index._load_with_state()

        self.assertIsNone(data)
        self.assertEqual(state, "corrupt")

    def test_tampered_index_hash_is_corrupt_and_aci_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class TargetSymbol:\n    pass\n"})
            index = LocalSemanticIndex(root)
            index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))
            payload["chunks"][0]["term_hashes"].append("0" * 16)
            index.path.write_text(json.dumps(payload), encoding="utf-8")

            corrupt = index.status()
            found = AgentComputerInterface(root).semantic_search("TargetSymbol")

        self.assertEqual(corrupt["state"], "corrupt")
        self.assertTrue(found.ok)
        self.assertEqual(found.data["matches"][0]["path"], "app.py")

    def test_malformed_chunk_is_corrupt_even_with_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class TargetSymbol:\n    pass\n"})
            index = LocalSemanticIndex(root)
            index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))
            payload["chunks"][0]["vector"] = [[0]]
            payload["index_hash"] = _rehash_payload(payload)
            index.path.write_text(json.dumps(payload), encoding="utf-8")

            status = index.status()
            found = AgentComputerInterface(root).semantic_search("TargetSymbol")

        self.assertEqual(status["state"], "corrupt")
        self.assertTrue(found.ok)

    def test_cache_paths_cannot_bypass_private_file_exclusions(self) -> None:
        private_paths = (".env.local", ".opaihub/secret.py", "nested/id_rsa")
        for private_path in private_paths:
            with self.subTest(path=private_path), tempfile.TemporaryDirectory() as tmp:
                root = _repo(Path(tmp), {"app.py": "value = 1\n"})
                index = LocalSemanticIndex(root)
                index.build()
                payload = json.loads(index.path.read_text(encoding="utf-8"))
                metadata = payload["files"].pop("app.py")
                payload["files"][private_path] = metadata
                payload["chunks"][0]["path"] = private_path
                payload["index_hash"] = _rehash_payload(payload)
                index.path.write_text(json.dumps(payload), encoding="utf-8")

                _data, state = index._load_with_state()

            self.assertEqual(state, "corrupt")

    def test_ntfs_alias_paths_are_rejected_by_discovery_and_cache(self) -> None:
        unsafe_paths = (
            "src/app:stream.py",
            "src./app.py",
            "con/app.py",
            "src/NUL.py",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root)
            index.build()
            original = json.loads(index.path.read_text(encoding="utf-8"))

            for unsafe_path in unsafe_paths:
                with self.subTest(path=unsafe_path):
                    self.assertFalse(index._candidate_name_allowed(unsafe_path))
                    payload = json.loads(json.dumps(original))
                    metadata = payload["files"].pop("app.py")
                    payload["files"][unsafe_path] = metadata
                    payload["chunks"][0]["path"] = unsafe_path
                    payload["index_hash"] = _rehash_payload(payload)
                    index.path.write_text(json.dumps(payload), encoding="utf-8")

                    _data, state = index._load_with_state()

                    self.assertEqual(state, "corrupt")

            for portable_path in ("src/conduit.py", "src/com10.py", "a.b/app.py"):
                with self.subTest(portable_path=portable_path):
                    self.assertTrue(index._candidate_name_allowed(portable_path))

    def test_selected_refresh_rejects_lexical_alias_before_file_resolution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            (root / "safe").mkdir()
            index = LocalSemanticIndex(root)

            built = index.build(paths=["safe/../app.py"])
            payload = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertEqual(built["files"], 0)
        self.assertEqual(payload["files"], {})
        self.assertEqual(payload["chunks"], [])

    def test_build_rejects_multi_hardlink_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class HardlinkTarget:\n    pass\n"})
            try:
                os.link(root / "app.py", root / "alias.py")
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")

            index = LocalSemanticIndex(root)
            built = index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertEqual(built["files"], 0)
        self.assertEqual(payload["files"], {})
        self.assertEqual(payload["chunks"], [])

    def test_build_rechecks_hardlink_count_after_reading_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class HardlinkTarget:\n    pass\n"})
            source = root / "app.py"
            alias = root / "alias.py"
            probe = root / "probe.py"
            try:
                os.link(source, probe)
                probe.unlink()
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            original_read_text = Path.read_text

            def read_and_link(path: Path, *args: object, **kwargs: object) -> str:
                text = original_read_text(path, *args, **kwargs)
                if path == source and not alias.exists():
                    os.link(source, alias)
                return text

            index = LocalSemanticIndex(root)
            with mock.patch.object(Path, "read_text", new=read_and_link):
                built = index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertEqual(built["files"], 0)
        self.assertEqual(payload["files"], {})
        self.assertEqual(payload["chunks"], [])

    def test_search_rechecks_and_rejects_new_multi_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class HardlinkTarget:\n    pass\n"})
            index = LocalSemanticIndex(root)
            index.build()
            self.assertEqual(index.search("HardlinkTarget")[0]["path"], "app.py")
            try:
                os.link(root / "app.py", root / "alias.py")
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")

            results = index.search("HardlinkTarget")

        self.assertEqual(results, [])

    def test_aci_turns_unexpected_index_shape_errors_into_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root)
            index.build()

            with mock.patch.object(
                LocalSemanticIndex,
                "search",
                side_effect=IndexError("malformed cached vector"),
            ):
                found = AgentComputerInterface(root).semantic_search("value")

        self.assertFalse(found.ok)
        self.assertEqual(found.error_code, "SEMANTIC_SEARCH_FAILED")
        self.assertNotIn("malformed cached vector", found.data.get("matches", []))

    def test_changed_index_configuration_forces_rebuild(self) -> None:
        cases = (
            {"dimension": 64},
            {"chunk_lines": 12},
            {"overlap_lines": 2},
        )
        for changed in cases:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root = _repo(Path(tmp), {"notes.txt": "uniqueworda\n"})
                LocalSemanticIndex(
                    root, dimension=32, chunk_lines=10, overlap_lines=1
                ).build()

                rebuilt = LocalSemanticIndex(
                    root,
                    dimension=changed.get("dimension", 32),
                    chunk_lines=changed.get("chunk_lines", 10),
                    overlap_lines=changed.get("overlap_lines", 1),
                ).build()

            self.assertEqual(rebuilt["previous_state"], "incompatible")
            self.assertFalse(rebuilt["cache_hit"])
            self.assertEqual(rebuilt["updated_files"], 1)

    def test_inventory_stops_after_the_bounded_overflow_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {f"src/file_{number:03d}.py": "value = 1\n" for number in range(20)},
            )
            index = LocalSemanticIndex(root, max_files=2)

            with mock.patch.object(
                index, "_eligible", wraps=index._eligible
            ) as eligible:
                built = index.build()

        self.assertEqual(built["state"], "partial")
        self.assertLessEqual(eligible.call_count, index.max_files + 1)
        self.assertTrue(built["truncation"]["inventory_truncated"])

    def test_git_inventory_closes_process_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root)

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                index.build()
                gc.collect()

        resource_warnings = [
            warning
            for warning in caught
            if issubclass(warning.category, ResourceWarning)
        ]
        self.assertEqual(resource_warnings, [])

    def test_total_source_bytes_bound_stays_partial_on_cache_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a.py": "class Included:\n    pass\n",
                    "b.py": "class Omitted:\n    pass\n",
                },
            )
            index = LocalSemanticIndex(root, max_total_bytes=30)

            first = index.build()
            second = index.build()

        self.assertEqual(first["state"], "partial")
        self.assertGreater(first["truncation"]["bytes_omitted"], 0)
        self.assertEqual(second["state"], "partial")
        self.assertGreater(second["truncation"]["bytes_omitted"], 0)

    def test_selected_refresh_preserves_global_file_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {"a.py": "value = 1\n", "b.py": "value = 2\n"},
            )
            index = LocalSemanticIndex(root, max_files=1)
            index.build()

            refreshed = index.build(paths=["b.py"])
            payload = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertEqual(refreshed["state"], "partial")
        self.assertLessEqual(refreshed["files"], index.max_files)
        self.assertLessEqual(len(payload["files"]), index.max_files)

    def test_cache_metadata_cannot_exceed_total_source_byte_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 1\n"})
            index = LocalSemanticIndex(root, max_total_bytes=30)
            index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))
            payload["files"]["app.py"]["size"] = 31
            payload["index_hash"] = _rehash_payload(payload)
            index.path.write_text(json.dumps(payload), encoding="utf-8")

            _data, state = index._load_with_state()

        self.assertEqual(state, "corrupt")

    def test_term_hashes_and_serialized_cache_have_hard_bounds(self) -> None:
        unique = " ".join(f"identifier{number:05d}" for number in range(400))
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"adversarial.py": unique + "\n"})
            index = LocalSemanticIndex(
                root,
                chunk_lines=5,
                max_term_hashes=8,
                max_index_bytes=4_096,
            )

            built = index.build()
            payload = json.loads(index.path.read_text(encoding="utf-8"))
            cache_size = index.path.stat().st_size

        self.assertEqual(built["state"], "partial")
        self.assertLessEqual(cache_size, index.max_index_bytes)
        self.assertTrue(
            all(len(chunk["term_hashes"]) <= 8 for chunk in payload["chunks"])
        )
        self.assertGreater(built["truncation"]["term_hashes_omitted"], 0)

    def test_term_hash_limit_is_reported_as_truncated(self) -> None:
        unique = " ".join(f"identifier{number:05d}" for number in range(100))
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"adversarial.py": unique + "\n"})
            index = LocalSemanticIndex(root, max_term_hashes=8)

            built = index.build()
            status = index.status()

        self.assertEqual(built["state"], "partial")
        self.assertEqual(built["truncation"]["chunks_omitted"], 0)
        self.assertGreater(built["truncation"]["term_hashes_omitted"], 0)
        self.assertEqual(status["state"], "partial")
        self.assertTrue(status["truncated"])

    def test_unchanged_metadata_does_not_hide_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "value = 'old'\n"})
            index = LocalSemanticIndex(root)
            index.build()
            original = (root / "app.py").stat()

            (root / "app.py").write_text("value = 'new'\n", encoding="utf-8")
            # Coarse filesystems and build tools can preserve timestamps. The
            # cache must still validate content before reusing indexed chunks.
            os.utime(
                root / "app.py",
                ns=(original.st_atime_ns, original.st_mtime_ns),
            )
            stale = index.status()
            found = AgentComputerInterface(root).semantic_search("new")
            old_results = index.search("old")
            new_results = index.search("new")

        self.assertEqual(stale["state"], "stale")
        self.assertTrue(found.ok)
        self.assertEqual(found.data["matches"][0]["path"], "app.py")
        self.assertEqual(old_results, [])
        self.assertEqual(new_results[0]["path"], "app.py")

    def test_git_discovery_timeout_falls_back_to_local_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"app.py": "class TimeoutSafe:\n    pass\n"})
            index = LocalSemanticIndex(root)

            with mock.patch.object(index, "_git_inventory", return_value=None):
                result = index.build()

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["files"], 1)

    def test_aci_search_rebuilds_an_existing_legacy_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), {"router.py": "class UpgradeTarget:\n    pass\n"})
            index = LocalSemanticIndex(root)
            index.path.parent.mkdir(parents=True, exist_ok=True)
            index.path.write_text(
                json.dumps(
                    {
                        "schema_version": INDEX_VERSION - 1,
                        "model": "opai-local-hash-v1",
                        "files": {},
                        "chunks": [],
                    }
                ),
                encoding="utf-8",
            )

            found = AgentComputerInterface(root).semantic_search("UpgradeTarget")
            rebuilt = json.loads(index.path.read_text(encoding="utf-8"))

        self.assertTrue(found.ok)
        self.assertEqual(found.data["matches"][0]["path"], "router.py")
        self.assertEqual(rebuilt["schema_version"], INDEX_VERSION)

    def test_file_limit_is_reported_as_partial_and_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {
                    "a.py": "class A:\n    pass\n",
                    "b.py": "class B:\n    pass\n",
                    "c.py": "class C:\n    pass\n",
                },
            )
            index = LocalSemanticIndex(root, max_files=2)
            built = index.build()
            status = index.status()

        self.assertEqual(built["state"], "partial")
        self.assertTrue(built["truncated"])
        self.assertEqual(built["truncation"]["files_omitted"], 1)
        self.assertEqual(status["state"], "partial")
        self.assertTrue(status["truncated"])

    def test_first_requested_path_build_stays_honestly_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(
                Path(tmp),
                {"a.py": "class A:\n    pass\n", "b.py": "class B:\n    pass\n"},
            )
            index = LocalSemanticIndex(root)
            built = index.build(paths=["a.py"])
            rebuilt = index.build(paths=["a.py"])
            status = index.status()

        self.assertEqual(built["state"], "partial")
        self.assertEqual(built["reason"], "requested_paths_only")
        self.assertEqual(rebuilt["state"], "partial")
        self.assertEqual(rebuilt["previous_state"], "partial")
        self.assertTrue(rebuilt["cache_hit"])
        self.assertEqual(rebuilt["reused_files"], 1)
        self.assertEqual(status["state"], "partial")
        self.assertEqual(status["reason"], "requested_paths_only")


if __name__ == "__main__":
    unittest.main()
