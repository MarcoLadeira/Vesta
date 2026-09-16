"""Chat-history recents (#145): per-workspace, redacted, clearable, no legacy leak."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _helpers import isolated_home

from vesta.gui_recents import (
    MAX_RECENTS,
    add_recent,
    begin_thread_turn,
    clear_recents,
    current_conversation_id,
    legacy_recents_path,
    load_recents,
    load_thread,
    recents_path,
    thread_path,
)


class RecentsTests(unittest.TestCase):
    def test_add_then_load_roundtrips(self):
        with isolated_home():
            add_recent("C:/proj-a", "summarize my changes")
            self.assertIn("summarize my changes", load_recents("C:/proj-a"))

    def test_most_recent_first_and_deduped(self):
        with isolated_home():
            add_recent("C:/proj-a", "a")
            add_recent("C:/proj-a", "b")
            add_recent("C:/proj-a", "a")  # re-sending moves to front, no duplicate
            recents = load_recents("C:/proj-a")
        self.assertEqual(recents[0], "a")
        self.assertEqual(recents.count("a"), 1)

    def test_empty_is_ignored(self):
        with isolated_home():
            add_recent("C:/proj-a", "   ")
            self.assertEqual(load_recents("C:/proj-a"), [])

    def test_newlines_collapsed(self):
        with isolated_home():
            add_recent("C:/proj-a", "line one\nline two")
            self.assertEqual(load_recents("C:/proj-a")[0], "line one line two")

    def test_capped(self):
        with isolated_home():
            for i in range(MAX_RECENTS + 8):
                add_recent("C:/proj-a", f"prompt {i}")
            self.assertLessEqual(len(load_recents("C:/proj-a")), MAX_RECENTS)


class WorkspaceIsolationTests(unittest.TestCase):
    def test_prompts_never_cross_workspaces(self):
        with isolated_home():
            add_recent("C:/client-a", "incident report for client A")
            add_recent("C:/client-b", "unrelated question")

            self.assertEqual(
                load_recents("C:/client-a")[0], "incident report for client A"
            )
            self.assertNotIn(
                "incident report for client A", load_recents("C:/client-b")
            )
            self.assertNotIn("unrelated question", load_recents("C:/client-a"))

    def test_history_files_are_keyed_by_workspace_hash_not_path(self):
        with isolated_home():
            path = recents_path("C:/client-a/secret-project-name")
        self.assertEqual(path.parent.name, "recents")
        self.assertNotIn("secret-project-name", path.name)
        self.assertRegex(path.name, r"^[0-9a-f]{16}\.json$")

    def test_same_workspace_resolves_to_the_same_file(self):
        with isolated_home():
            first = add_recent("C:/proj-a", "hello")
            again = load_recents("C:/proj-a/../proj-a")
        self.assertEqual(first, again)


class RedactionTests(unittest.TestCase):
    def test_secret_shaped_input_is_redacted_before_writing(self):
        with isolated_home():
            add_recent("C:/proj-a", "set API_KEY=abcdefghijklmnop1234 in the env")
            add_recent("C:/proj-a", "rotate ghp_abcdefghijklmnopqrstuvwxyz123456 now")
            raw = recents_path("C:/proj-a").read_text(encoding="utf-8")
            shown = load_recents("C:/proj-a")

        self.assertNotIn("abcdefghijklmnop1234", raw)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", raw)
        self.assertTrue(any("[REDACTED" in item for item in shown))


class ClearAndMigrationTests(unittest.TestCase):
    def test_clear_history_deletes_the_workspace_file(self):
        with isolated_home():
            add_recent("C:/proj-a", "hello")
            self.assertTrue(recents_path("C:/proj-a").exists())
            self.assertEqual(clear_recents("C:/proj-a"), [])
            self.assertFalse(recents_path("C:/proj-a").exists())
            self.assertEqual(load_recents("C:/proj-a"), [])

    def test_clear_only_touches_that_workspace(self):
        with isolated_home():
            add_recent("C:/proj-a", "keep me")
            add_recent("C:/proj-b", "remove me")
            clear_recents("C:/proj-b")
            self.assertEqual(load_recents("C:/proj-a"), ["keep me"])
            self.assertEqual(load_recents("C:/proj-b"), [])

    def test_legacy_global_history_is_deleted_and_never_displayed(self):
        with isolated_home():
            legacy = legacy_recents_path()
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(
                json.dumps(["confidential prompt from another client"]),
                encoding="utf-8",
            )
            shown = load_recents("C:/proj-a")
            self.assertEqual(shown, [])
            self.assertFalse(legacy.exists())

    def test_no_global_prompt_file_is_written_anymore(self):
        with isolated_home():
            add_recent("C:/proj-a", "hello world")
            self.assertFalse(legacy_recents_path().exists())
            state_files = list((Path.home() / ".vesta").rglob("*.json"))
        self.assertEqual([item.parent.name for item in state_files], ["recents"])


class CurrentConversationIdTests(unittest.TestCase):
    """#818: the identity the journal mirror needs, read without a lock.

    Unlocked on purpose. The caller is the journal mirror on the admission
    path, and a mirror must never be able to slow -- let alone deadlock -- the
    turn it mirrors. The thread file is replaced by an atomic rename, so an
    unlocked reader sees a whole document or the previous whole document.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_thread_yet_names_no_conversation(self):
        self.assertEqual(current_conversation_id(self.root), "")

    def test_a_started_turn_names_its_conversation(self):
        begin_thread_turn(self.root, request_id="req-1", text="hello", mode="ask")

        conversation = current_conversation_id(self.root)

        self.assertTrue(conversation)
        self.assertEqual(conversation, load_thread(self.root)["conversation_id"])

    def test_a_second_turn_names_the_same_conversation(self):
        begin_thread_turn(self.root, request_id="req-1", text="hello", mode="ask")
        first = current_conversation_id(self.root)
        begin_thread_turn(self.root, request_id="req-2", text="again", mode="ask")

        self.assertEqual(current_conversation_id(self.root), first)

    def test_an_unreadable_thread_names_nothing_rather_than_raising(self):
        begin_thread_turn(self.root, request_id="req-1", text="hello", mode="ask")
        thread_path(self.root).write_text("{not json", encoding="utf-8")

        self.assertEqual(current_conversation_id(self.root), "")


if __name__ == "__main__":
    unittest.main()
