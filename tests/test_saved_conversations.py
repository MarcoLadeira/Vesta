"""The sidebar's "Recent chats" are chats, not prompts.

The sidebar was labelled "Recent chats" and stored a list of prompt *strings*.
Selecting one re-typed the question into the composer and threw the answer away
— there was no way to read a past conversation at all, because the single
resumable ``thread.json`` had already been overwritten by the next chat.

Conversations are now archived on every finished turn, keyed by a conversation
id the thread carries, so a multi-turn chat stays one entry that grows. The id
had to be new state: ``task_id`` falls back to the per-turn request id, so
archiving on it produced one saved chat per reply, each holding the whole
accumulated transcript. Archiving on turn completion rather
than when the user presses "New chat" is deliberate: history that depends on the
user performing a bookkeeping step is missing exactly when they need it.

The prompt list still exists and is unchanged — it is what the composer's
Up-arrow history reads, which is the job it was always doing.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from opai.gui_recents import (
    MAX_CONVERSATIONS,
    archive_conversation,
    begin_thread_turn,
    clear_conversations,
    clear_recents,
    clear_thread,
    conversations_dir,
    finish_thread_turn,
    list_conversations,
    load_conversation,
    load_recents,
)


def _repo(root: Path) -> Path:
    subprocess.run(  # nosec B603 B607 - fixed argv, throwaway test repo
        ["git", "init", "-q"], cwd=root, capture_output=True, check=False
    )
    return root


class ConversationArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _turn(self, request_id: str, question: str, answer: str) -> None:
        """One completed turn in the current chat."""
        begin_thread_turn(self.root, request_id=request_id, text=question, mode="ask")
        finish_thread_turn(
            self.root,
            request_id=request_id,
            answer=answer,
            status="complete",
            task_id=request_id,
        )

    def _new_chat(self) -> None:
        """What the New chat button does: end this conversation, start another."""
        clear_thread(self.root)

    def test_a_finished_turn_is_archived_without_being_asked(self) -> None:
        self._turn("r1", "How does auth work?", "It uses OAuth.")
        saved = list_conversations(self.root)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["title"], "How does auth work?")

    def test_the_answer_is_kept_not_just_the_question(self) -> None:
        # The whole point: the old sidebar stored the prompt and lost the reply.
        self._turn("r1", "How does auth work?", "It uses OAuth.")
        conv = load_conversation(self.root, list_conversations(self.root)[0]["id"])
        self.assertEqual(
            [(m["role"], m["text"]) for m in conv["messages"]],
            [("user", "How does auth work?"), ("assistant", "It uses OAuth.")],
        )

    def test_a_multi_turn_chat_stays_one_conversation(self) -> None:
        # Archiving per finished turn must not produce one entry per reply.
        self._turn("r1", "How does auth work?", "OAuth.")
        self._turn("r2", "And sessions?", "Cookies.")
        saved = list_conversations(self.root)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["message_count"], 4)

    def test_separate_chats_are_separate_entries(self) -> None:
        self._turn("r1", "First question", "First answer")
        self._new_chat()
        self._turn("r2", "Second question", "Second answer")
        self.assertEqual(len(list_conversations(self.root)), 2)

    def test_a_conversation_is_titled_by_its_first_question(self) -> None:
        # Not the latest one — the user recalls a chat by how it started.
        self._turn("r1", "How does auth work?", "OAuth.")
        self._turn("r2", "And sessions?", "Cookies.")
        self.assertEqual(
            list_conversations(self.root)[0]["title"], "How does auth work?"
        )

    def test_the_newest_conversation_is_listed_first(self) -> None:
        self._turn("r1", "Older question", "a")
        self._new_chat()
        self._turn("r2", "Newer question", "b")
        titles = [c["title"] for c in list_conversations(self.root)]
        self.assertEqual(titles[0], "Newer question")

    def test_history_is_bounded(self) -> None:
        for index in range(MAX_CONVERSATIONS + 5):
            self._turn(f"r{index}", f"Question {index}", "answer")
            self._new_chat()
        self.assertLessEqual(len(list_conversations(self.root)), MAX_CONVERSATIONS)
        # And the oldest are the ones dropped, not an arbitrary subset.
        titles = [c["title"] for c in list_conversations(self.root)]
        self.assertIn(f"Question {MAX_CONVERSATIONS + 4}", titles)
        self.assertNotIn("Question 0", titles)

    def test_continuing_a_chat_does_not_create_an_entry_per_reply(self) -> None:
        # The bug the first implementation had. Keying on `task_id` looked
        # right, but it falls back to the per-turn request id, so a three-turn
        # chat became three saved chats -- each one holding the entire
        # transcript so far, and all three titled by the first question.
        self._turn("r1", "First question", "a")
        self._turn("r2", "Second question", "b")
        self._turn("r3", "Third question", "c")

        saved = list_conversations(self.root)
        self.assertEqual(len(saved), 1, [c["title"] for c in saved])
        self.assertEqual(saved[0]["message_count"], 6)

    def test_two_chats_finished_in_the_same_second_still_order(self) -> None:
        # `updated_at` is second-granularity, so ties made the sidebar order
        # arbitrary -- reliably wrong on a fast machine, which is where a user
        # sends two short prompts back to back.
        self._turn("r1", "Older question", "a")
        self._new_chat()
        self._turn("r2", "Newer question", "b")

        saved = list_conversations(self.root)
        self.assertEqual(
            [c["title"] for c in saved], ["Newer question", "Older question"]
        )
        self.assertGreater(saved[0]["updated_ts"], saved[1]["updated_ts"])

    def test_a_secret_in_a_prompt_never_reaches_disk(self) -> None:
        # Saved history is durable and local; the redaction contract the prompt
        # list already had must hold for transcripts too.
        self._turn("r1", "deploy with sk-live-abcdef123456789012345 please", "done")
        stored = "\n".join(
            p.read_text(encoding="utf-8")
            for p in conversations_dir(self.root).glob("*.json")
        )
        self.assertNotIn("sk-live-abcdef123456789012345", stored)

    def test_a_started_turn_is_saved_before_the_answer_arrives(self) -> None:
        # The chat appears in the sidebar as soon as it is sent, which is what
        # the old prompt list did -- and it means a question survives a crash
        # mid-answer rather than depending on the separate resume path.
        begin_thread_turn(
            self.root, request_id="r1", text="Pending question", mode="ask"
        )
        saved = list_conversations(self.root)
        self.assertEqual([c["title"] for c in saved], ["Pending question"])
        self.assertEqual(saved[0]["message_count"], 1)

    def test_the_answer_updates_the_same_entry_rather_than_adding_one(self) -> None:
        begin_thread_turn(self.root, request_id="r1", text="A question", mode="ask")
        finish_thread_turn(
            self.root,
            request_id="r1",
            answer="An answer.",
            status="complete",
            task_id="r1",
        )
        saved = list_conversations(self.root)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["message_count"], 2)

    def test_clearing_history_removes_saved_conversations_too(self) -> None:
        # The privacy control promises to delete this workspace's history; a
        # transcript surviving "Clear history" would break that promise.
        self._turn("r1", "Something private", "answer")
        clear_recents(self.root)
        self.assertEqual(list_conversations(self.root), [])
        self.assertEqual(load_recents(self.root), [])


class ConversationSafetyTests(unittest.TestCase):
    """Ids arrive from the front-end, so they are untrusted input."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_traversal_id_cannot_read_outside_the_folder(self) -> None:
        for bad in ("../../../etc/passwd", "..\\..\\secrets", "a/../../b", ""):
            with self.subTest(conversation_id=bad):
                self.assertEqual(load_conversation(self.root, bad), {})

    def test_an_unknown_id_is_empty_not_an_error(self) -> None:
        self.assertEqual(load_conversation(self.root, "nope"), {})

    def test_a_corrupt_file_is_ignored_rather_than_listed(self) -> None:
        folder = conversations_dir(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "broken.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(list_conversations(self.root), [])

    def test_archiving_a_non_dict_is_a_no_op(self) -> None:
        self.assertEqual(archive_conversation(self.root, None), {})
        self.assertEqual(archive_conversation(self.root, "nope"), {})

    def test_clearing_an_empty_archive_is_safe(self) -> None:
        self.assertTrue(clear_conversations(self.root))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
