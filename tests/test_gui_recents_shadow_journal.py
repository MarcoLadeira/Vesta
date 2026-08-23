"""#613 Stage 2: saved conversations' shadow journal, and what it must *not* remember.

Stage 1 named ``opai/gui_recents.py`` JOURNAL_OWNED -- "events: conversation /
thread history". This is the user's chat history, which makes it the module
where a shadow journal is most obviously useful and most obviously dangerous.

Useful, because losing history is the loss users actually notice. Dangerous,
because ``gui_recents``' own docstring states a privacy contract -- chat
history is "local, per-workspace, redacted, and *clearable*" -- and a shadow
that survives an erasure breaks the last of those. A journal is not exempt
from a privacy contract just because it is an implementation detail.

So the interesting half of these tests is negative. They pin that deliberate
removals -- retention pruning and ``clear_conversations`` -- are mirrored as
deletions, not quietly preserved; and that the journal's own files stay outside
the folder ``_prune_conversations`` deletes from.
"""

from __future__ import annotations

import concurrent.futures
import json
import subprocess  # nosec B404 - fixed argv, throwaway test repo
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from opai import gui_recents
from opai.gui_recents import (
    MAX_CONVERSATIONS,
    archive_conversation,
    begin_thread_turn,
    clear_conversations,
    clear_thread,
    conversation_contradiction_report,
    conversation_shadow_projection,
    conversations_dir,
    finish_thread_turn,
    list_conversations,
    load_conversation,
)
from opaihub import shadow_journal


def _repo(root: Path) -> Path:
    subprocess.run(  # nosec B603 B607 - fixed argv, throwaway test repo
        ["git", "init", "-q"], cwd=root, capture_output=True, check=False
    )
    return root


class _ConversationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _repo(Path(self._tmp.name))

    def _turn(self, request_id: str, question: str, answer: str) -> None:
        begin_thread_turn(self.root, request_id=request_id, text=question, mode="ask")
        finish_thread_turn(
            self.root,
            request_id=request_id,
            answer=answer,
            status="complete",
            task_id=request_id,
        )

    def _chat(self, request_id: str, question: str, answer: str) -> str:
        """One complete conversation; returns its id."""

        self._turn(request_id, question, answer)
        conversation_id = list_conversations(self.root)[0]["id"]
        clear_thread(self.root)
        return conversation_id

    def _path(self, conversation_id: str) -> Path:
        return conversations_dir(self.root) / f"{conversation_id}.json"


class ShadowMirrorsArchivedConversationsTests(_ConversationFixture):
    def test_archiving_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        conversation_id = self._chat("r1", "How does auth work?", "It uses OAuth.")

        shadow = conversation_shadow_projection(self.root, conversation_id)

        self.assertEqual(shadow["id"], conversation_id)
        self.assertEqual(
            [(m["role"], m["text"]) for m in shadow["messages"]],
            [("user", "How does auth work?"), ("assistant", "It uses OAuth.")],
        )
        self.assertIsNone(
            conversation_contradiction_report(self.root, conversation_id)
        )

    def test_a_growing_multi_turn_chat_moves_both_sides_together(self):
        """Archiving is per finished turn, so the shadow must track the growth."""

        self._turn("r1", "How does auth work?", "OAuth.")
        conversation_id = list_conversations(self.root)[0]["id"]
        self._turn("r2", "And sessions?", "Cookies.")

        shadow = conversation_shadow_projection(self.root, conversation_id)
        stored = load_conversation(self.root, conversation_id)

        self.assertEqual(len(shadow["messages"]), 4)
        self.assertEqual(shadow["messages"], stored["messages"])
        self.assertIsNone(
            conversation_contradiction_report(self.root, conversation_id)
        )

    def test_separate_chats_keep_separate_shadows(self):
        first = self._chat("r1", "How does auth work?", "OAuth.")
        second = self._chat("r2", "How do migrations run?", "Alembic.")

        self.assertNotEqual(first, second)
        self.assertEqual(
            conversation_shadow_projection(self.root, first)["title"],
            "How does auth work?",
        )
        self.assertEqual(
            conversation_shadow_projection(self.root, second)["title"],
            "How do migrations run?",
        )

    def test_an_archive_refused_for_having_no_messages_writes_neither_side(self):
        result = archive_conversation(self.root, {"conversation_id": "c-empty"})

        self.assertEqual(result, {})
        self.assertEqual(conversation_shadow_projection(self.root, "c-empty"), {})

    def test_concurrent_archives_leave_file_and_shadow_agreeing(self):
        """Pins the lock this migration added, by forcing the race it prevents.

        The archive write held no cross-process lock before #613 -- only
        ``save_thread`` and friends used ``_thread_transaction``. The file
        write and the mirror are two steps, so without a lock one writer can
        land its *file* last while another lands its *journal* last, leaving
        the shadow describing a conversation the file never ended up holding.

        Simply running many threads does not reproduce that: the window is
        narrow and the first draft of this test passed with the lock removed,
        which made it worthless as a regression pin. Delaying one writer's
        mirror is what makes the interleaving deterministic --

            no lock: A writes file, stalls; B writes file *and* mirrors;
                     A mirrors last  ->  file is B's, shadow is A's
            lock:    B cannot start until A has both written and mirrored
        """

        conversation_id = self._chat("r1", "How does auth work?", "OAuth.")
        base = load_conversation(self.root, conversation_id)
        real_snapshot = shadow_journal.record_snapshot
        stalls = {"slow": 0.6, "fast": 0.0}

        def archive(name: str) -> None:
            def stalled(*args, **kwargs):
                time.sleep(stalls[name])
                return real_snapshot(*args, **kwargs)

            with mock.patch.object(
                gui_recents.shadow_journal, "record_snapshot", stalled
            ):
                archive_conversation(
                    self.root,
                    {
                        "conversation_id": conversation_id,
                        "mode": "ask",
                        "messages": [
                            *base["messages"],
                            {"role": "user", "text": f"follow-up from {name}"},
                        ],
                    },
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(archive, "slow")
            time.sleep(0.15)  # let the slow writer reach its stalled mirror
            fast = pool.submit(archive, "fast")
            slow.result()
            fast.result()

        self.assertIsNone(
            conversation_contradiction_report(self.root, conversation_id)
        )


class DeliberateRemovalsMustBeMirroredTests(_ConversationFixture):
    """The half of this migration that is about *forgetting*.

    A shadow journal's default behaviour is to remember. For chat history that
    default is wrong twice over, and both are pinned here.
    """

    def test_clearing_history_clears_the_shadow_too(self):
        """The module's stated privacy contract: chat history is clearable.

        A shadow that outlives an erasure is not a migration detail; it is the
        erasure failing. This is the test that would have caught it.
        """

        first = self._chat("r1", "How does auth work?", "OAuth.")
        second = self._chat("r2", "How do migrations run?", "Alembic.")

        clear_conversations(self.root)

        self.assertEqual(list_conversations(self.root), [])
        self.assertEqual(conversation_shadow_projection(self.root, first), {})
        self.assertEqual(conversation_shadow_projection(self.root, second), {})
        self.assertIsNone(conversation_contradiction_report(self.root, first))

    def test_retention_pruning_prunes_the_shadow_too(self):
        """The journal must not silently extend retention past MAX_CONVERSATIONS.

        Keeping pruned conversations in the shadow would look harmless now and
        would flip this workspace's retention from twenty to unbounded the
        moment Stage 5 makes the journal canonical -- a policy change nobody
        decided.
        """

        ids = [
            self._chat(f"r{index}", f"question {index}", f"answer {index}")
            for index in range(MAX_CONVERSATIONS + 3)
        ]

        surviving = {item["id"] for item in list_conversations(self.root)}
        self.assertEqual(len(surviving), MAX_CONVERSATIONS)

        pruned = [item for item in ids if item not in surviving]
        self.assertTrue(pruned, "expected the oldest conversations to be pruned")
        for conversation_id in pruned:
            self.assertEqual(
                conversation_shadow_projection(self.root, conversation_id),
                {},
                "a pruned conversation must not survive in the shadow",
            )
            self.assertIsNone(
                conversation_contradiction_report(self.root, conversation_id)
            )

    def test_a_surviving_conversation_is_untouched_by_pruning(self):
        """Teeth for the test above: pruning must not tombstone everything."""

        for index in range(MAX_CONVERSATIONS + 3):
            self._chat(f"r{index}", f"question {index}", f"answer {index}")

        newest = list_conversations(self.root)[0]["id"]

        self.assertEqual(
            conversation_shadow_projection(self.root, newest)["id"], newest
        )
        self.assertIsNone(conversation_contradiction_report(self.root, newest))


class TheJournalMustNotBePrunedAsIfItWereAChatTests(_ConversationFixture):
    """The glob trap, sixth occurrence -- and the most destructive so far.

    ``_prune_conversations`` globs ``*.json`` and *deletes* every file it
    cannot read as a conversation, with a comment explaining why that is
    correct. The journal's head cache is unconditionally named
    ``<name>.head.json``. Placed beside the conversation files it would have
    been deleted on every single archive: silent, repeated, and justified by
    the surrounding comment.
    """

    def test_the_journal_never_appears_in_the_conversation_listing(self):
        # Several turns on *one* chat, so the journal is appended to and its
        # head cache rewritten repeatedly while the folder holds a single
        # conversation file.
        self._turn("r1", "How does auth work?", "OAuth.")
        conversation_id = list_conversations(self.root)[0]["id"]
        self._turn("r2", "And sessions?", "Cookies.")
        self._turn("r3", "And logout?", "Token revocation.")

        listed = list_conversations(self.root)

        self.assertEqual([item["id"] for item in listed], [conversation_id])

    def test_the_journal_survives_the_pruning_that_deletes_stray_files(self):
        conversation_id = self._chat("r1", "How does auth work?", "OAuth.")

        # Force many prune passes, each of which would have unlinked a
        # sibling head cache.
        for index in range(5):
            self._chat(f"r{index + 2}", f"question {index}", f"answer {index}")

        self.assertEqual(
            conversation_shadow_projection(self.root, conversation_id)["id"],
            conversation_id,
        )

    def test_no_journal_file_sits_in_the_pruned_folder(self):
        self._chat("r1", "How does auth work?", "OAuth.")

        stray = [
            path.name
            for path in conversations_dir(self.root).glob("*.json")
            if "journal" in path.name
        ]

        self.assertEqual(stray, [])


class ContradictionReportIsExactTests(_ConversationFixture):
    def test_an_out_of_band_file_write_is_reported(self):
        """The scenario #613 exists for: something rewrote the history file."""

        conversation_id = self._chat("r1", "How does auth work?", "OAuth.")
        stored = load_conversation(self.root, conversation_id)
        tampered = {**stored, "title": "something else entirely"}
        self._path(conversation_id).write_text(
            json.dumps(tampered), encoding="utf-8"
        )

        report = conversation_contradiction_report(self.root, conversation_id)

        self.assertIsNotNone(report)
        self.assertIn("title", report["mismatched_fields"])
        self.assertEqual(report["conversation_id"], conversation_id)

    def test_a_lost_history_file_is_reported_against_a_surviving_shadow(self):
        """The loss case, and the reason #613 wants a journal at all."""

        conversation_id = self._chat("r1", "How does auth work?", "OAuth.")
        self._path(conversation_id).unlink()

        report = conversation_contradiction_report(self.root, conversation_id)

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["id"], conversation_id)

    def test_a_never_archived_conversation_agrees_as_both_empty(self):
        self.assertIsNone(conversation_contradiction_report(self.root, "c-nothing"))
        self.assertEqual(conversation_shadow_projection(self.root, "c-nothing"), {})

    def test_an_unsafe_conversation_id_is_refused_rather_than_resolved(self):
        """The accessors take a front-end-supplied id; it stays untrusted."""

        self.assertEqual(conversation_shadow_projection(self.root, "../escape"), {})
        self.assertIsNone(conversation_contradiction_report(self.root, "../escape"))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
