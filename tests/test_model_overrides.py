"""The model list can be corrected without shipping a new OPai.

`opai/model_registry.py` is a table compiled into the release, so it goes stale
the moment a provider ships something new: the model exists, the user's CLI
accepts it, and OPai's picker does not offer it. That coupling is wrong —
provider model names change far more often than this app does.

These cover the layer that fixes it: a JSON file the user owns, merged over the
built-in table.

Deliberately *not* auto-discovery. None of `claude`, `codex` or `copilot`
exposes a model-listing command — they take `--model <id>` and fail at request
time on a bad one. Presenting guessed ids as available would be worse than a
stale list, because a wrong id fails mid-run after the user has committed to the
task.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opai.model_overrides import (
    MAX_MODELS_PER_PROVIDER,
    apply_overrides,
    load_overrides,
    save_overrides,
)
from opai.model_registry import ModelSpec

BUILTIN = (
    ModelSpec("gpt-a", "GPT A", "GPT A", "best"),
    ModelSpec("gpt-b", "GPT B", "GPT B", "balanced"),
)


class _Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "models.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, payload: object) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")


class MergeTests(_Temp):
    def test_a_new_model_is_appended(self) -> None:
        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {"id": "luna", "display": "Luna", "capability": "best"}
                        ]
                    }
                }
            }
        )
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        self.assertEqual([m.id for m in merged], ["gpt-a", "gpt-b", "luna"])

    def test_overriding_a_builtin_keeps_its_position(self) -> None:
        # Correcting one model's label must not reshuffle the picker under the
        # user; a list that reorders itself is its own consistency problem.
        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {"id": "gpt-a", "display": "Renamed", "capability": "fast"}
                        ]
                    }
                }
            }
        )
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        self.assertEqual([m.id for m in merged], ["gpt-a", "gpt-b"])
        self.assertEqual(merged[0].display, "Renamed")
        self.assertEqual(merged[0].capability, "fast")

    def test_a_withdrawn_model_can_be_hidden(self) -> None:
        self.write({"providers": {"codex": {"hide": ["gpt-b"]}}})
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        self.assertEqual([m.id for m in merged], ["gpt-a"])

    def test_an_untouched_provider_is_unchanged(self) -> None:
        self.write(
            {"providers": {"claude": {"models": [{"id": "x", "capability": "best"}]}}}
        )
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        self.assertEqual([m.id for m in merged], ["gpt-a", "gpt-b"])

    def test_no_file_means_the_builtin_list_exactly(self) -> None:
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        self.assertEqual(merged, BUILTIN)

    def test_aliases_resolve_through_the_registry(self) -> None:
        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {
                                "id": "luna",
                                "capability": "best",
                                "aliases": ["luna-1", "LUNA"],
                            }
                        ]
                    }
                }
            }
        )
        merged = apply_overrides("codex", BUILTIN, load_overrides(self.path))
        luna = next(m for m in merged if m.id == "luna")
        self.assertIn("luna-1", luna.aliases)


class ValidationTests(_Temp):
    """A convenience file must fail loudly, not half-apply."""

    def test_a_malformed_file_yields_errors_and_no_overrides(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        report = load_overrides(self.path)
        self.assertFalse(report.ok)
        self.assertEqual(report.models, {})

    def test_one_bad_entry_rejects_the_whole_file(self) -> None:
        # Applying the readable half would give a list matching neither the
        # user's intent nor the default, with no way to reason about which.
        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {"id": "good", "capability": "best"},
                            {"id": "bad", "capability": "wildly-fast"},
                        ]
                    }
                }
            }
        )
        report = load_overrides(self.path)
        self.assertFalse(report.ok)
        self.assertEqual(report.models, {})
        self.assertTrue(any("wildly-fast" in e for e in report.errors))

    def test_a_missing_id_is_named_precisely(self) -> None:
        self.write({"providers": {"codex": {"models": [{"display": "No id"}]}}})
        report = load_overrides(self.path)
        self.assertFalse(report.ok)
        self.assertTrue(any("'id' is required" in e for e in report.errors))

    def test_a_duplicate_id_is_refused(self) -> None:
        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {"id": "dup", "capability": "best"},
                            {"id": "DUP", "capability": "fast"},
                        ]
                    }
                }
            }
        )
        self.assertFalse(load_overrides(self.path).ok)

    def test_an_unbounded_list_is_refused(self) -> None:
        # Read on every model lookup; this is a list, not a database.
        entries = [
            {"id": f"m{i}", "capability": "fast"}
            for i in range(MAX_MODELS_PER_PROVIDER + 1)
        ]
        self.write({"providers": {"codex": {"models": entries}}})
        self.assertFalse(load_overrides(self.path).ok)

    def test_a_non_object_top_level_is_refused(self) -> None:
        self.write(["not", "an", "object"])
        self.assertFalse(load_overrides(self.path).ok)

    def test_defaults_fill_in_for_optional_fields(self) -> None:
        self.write({"providers": {"codex": {"models": [{"id": "bare"}]}}})
        report = load_overrides(self.path)
        self.assertTrue(report.ok, report.errors)
        spec = report.models["codex"][0]
        self.assertEqual(spec.display, "bare")
        self.assertEqual(spec.capability, "balanced")


class SaveTests(_Temp):
    def test_saving_then_loading_round_trips(self) -> None:
        save_overrides(
            {
                "codex": [
                    {
                        "id": "luna",
                        "display": "Luna",
                        "capability": "best",
                        "aliases": ["l"],
                    }
                ]
            },
            path=self.path,
        )
        report = load_overrides(self.path)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.models["codex"][0].id, "luna")

    def test_an_invalid_write_is_refused_and_leaves_no_file(self) -> None:
        # Validated after writing, so what is checked is what landed on disk.
        with self.assertRaises(ValueError):
            save_overrides(
                {"codex": [{"id": "x", "capability": "nonsense"}]}, path=self.path
            )
        self.assertFalse(self.path.exists())

    def test_an_existing_file_survives_a_refused_write(self) -> None:
        save_overrides(
            {"codex": [{"id": "keep", "capability": "best"}]}, path=self.path
        )
        with self.assertRaises(ValueError):
            save_overrides(
                {"codex": [{"id": "x", "capability": "nope"}]}, path=self.path
            )
        self.assertEqual(load_overrides(self.path).models["codex"][0].id, "keep")


class RegistryIntegrationTests(_Temp):
    """The picker, routing and validation must all see the merged view."""

    def test_models_for_reflects_the_users_file(self) -> None:
        from unittest import mock

        self.write(
            {
                "providers": {
                    "codex": {
                        "models": [
                            {"id": "luna", "display": "Luna", "capability": "best"}
                        ]
                    }
                }
            }
        )
        with mock.patch("opai.model_overrides.overrides_path", return_value=self.path):
            from opai.model_registry import models_for, resolve_id

            ids = [m.id for m in models_for("codex")]
            self.assertIn("luna", ids)
            self.assertEqual(resolve_id("codex", "luna"), "luna")

    def test_a_broken_file_never_breaks_model_lookup(self) -> None:
        # model_registry is imported by nearly every layer. A convenience file
        # must not be able to take the picker down.
        from unittest import mock

        self.path.write_text("{broken", encoding="utf-8")
        with mock.patch("opai.model_overrides.overrides_path", return_value=self.path):
            from opai.model_registry import models_for

            self.assertTrue(len(models_for("codex")) > 0)


class BuiltinFreshnessTests(unittest.TestCase):
    def test_the_current_claude_flagship_is_offered(self) -> None:
        # The staleness that prompted this work: the registry topped out at
        # Opus 4.8 while Opus 5 was current, so the picker could not reach it.
        from opai.model_registry import find, models_for

        ids = {m.id for m in models_for("claude")}
        self.assertIn("claude-opus-5", ids)
        self.assertEqual(find("claude", "opus-5").id, "claude-opus-5")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
