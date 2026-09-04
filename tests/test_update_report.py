"""One rendering of updater state, shared by every surface (#832 scope item 7).

`--json` was declared on six update subcommands and read by none of them, so
the flag promised a choice and the command always printed JSON -- which cannot
answer "why did it not notice the update?" for a person, the question these
surfaces exist for.

The renderer is a formatter over the canonical payload. It recomputes nothing,
so the human and JSON outputs cannot disagree, and a cached answer can never
borrow the words a fresh one uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from opai.update.report import (
    freshness_phrase,
    humanise_age,
    render_status_lines,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _ago(**kwargs) -> str:
    return (NOW - timedelta(**kwargs)).isoformat()


class FreshnessLanguageTests(unittest.TestCase):
    def test_a_cached_result_says_so_and_dates_the_real_check(self) -> None:
        phrase = freshness_phrase(
            {"showing_cached_result": True, "last_remote_check_at": _ago(minutes=47)},
            now=NOW,
        )
        self.assertIn("cached result", phrase)
        self.assertIn("47 min ago", phrase)

    def test_a_fresh_result_never_says_cached(self) -> None:
        phrase = freshness_phrase(
            {"showing_cached_result": False, "last_remote_check_at": _ago(minutes=2)},
            now=NOW,
        )
        self.assertNotIn("cached", phrase)
        self.assertIn("2 min ago", phrase)

    def test_a_cache_with_no_remote_history_admits_it(self) -> None:
        phrase = freshness_phrase({"showing_cached_result": True}, now=NOW)
        self.assertIn("never checked remotely", phrase)

    def test_the_headline_does_not_contradict_the_detail(self) -> None:
        # `remote_checked_at` is newer than some persisted operations. Reading
        # only that field made an installation which had checked report "not
        # yet checked remotely" in the headline while the detail below said it
        # had -- caught by running the real command, not by a test.
        phrase = freshness_phrase(
            {
                "showing_cached_result": False,
                "last_remote_check_at": "",
                "last_successful_remote_check_at": _ago(hours=19),
            },
            now=NOW,
        )
        self.assertIn("19 hr ago", phrase)
        self.assertNotIn("not yet", phrase)

    def test_a_clock_that_moved_backwards_is_not_dated_in_the_future(self) -> None:
        self.assertEqual(humanise_age(_ago(minutes=-30), now=NOW), "just now")

    def test_a_missing_timestamp_renders_as_nothing_not_a_guess(self) -> None:
        self.assertEqual(humanise_age(""), "")
        self.assertEqual(humanise_age(None), "")


class StatusRenderingTests(unittest.TestCase):
    def _payload(self, **discovery) -> dict:
        base = {
            "showing_cached_result": False,
            "last_remote_check_at": _ago(minutes=2),
            "ownership": {"owner": "opai", "mechanism": "git", "self_updatable": True},
        }
        base.update(discovery)
        return {"operation": {"state": "up_to_date"}, "discovery": base}

    def test_a_quiet_installation_renders_one_line(self) -> None:
        lines = render_status_lines(self._payload(), now=NOW)
        self.assertEqual(len(lines), 1)
        self.assertIn("Up to date", lines[0])
        self.assertIn("checked remotely 2 min ago", lines[0])

    def test_an_externally_owned_install_is_told_what_to_run(self) -> None:
        payload = self._payload(
            ownership={
                "owner": "pipx",
                "mechanism": "pipx",
                "self_updatable": False,
                "remediation": "Update with `pipx upgrade opai`.",
            }
        )
        lines = render_status_lines(payload, now=NOW)
        self.assertTrue(any("pipx upgrade opai" in line for line in lines))

    def test_a_self_updatable_install_is_not_told_to_run_anything(self) -> None:
        lines = render_status_lines(self._payload(), now=NOW)
        self.assertFalse(any("Update with" in line for line in lines))

    def test_a_version_disagreement_is_stated_plainly(self) -> None:
        payload = self._payload(
            version_differs_from_disk=True,
            running_version="0.2.1a1",
            disk_version="0.9.9",
        )
        lines = render_status_lines(payload, now=NOW)
        joined = " ".join(lines)
        self.assertIn("0.2.1a1", joined)
        self.assertIn("0.9.9", joined)
        self.assertIn("restart", joined.casefold())

    def test_verbose_adds_the_canonical_facts_without_inventing_any(self) -> None:
        payload = self._payload(
            install_type="source_checkout",
            update_source="origin/main",
            cadence_seconds=600,
            cadence_reason="source checkout tracking origin/main",
            last_trigger="periodic",
        )
        lines = render_status_lines(payload, verbose=True, now=NOW)
        joined = "\n".join(lines)
        self.assertIn("source_checkout", joined)
        self.assertIn("origin/main", joined)
        self.assertIn("10 min", joined)
        # Fields the payload does not carry are absent, never filled in.
        self.assertNotIn("Version on disk", joined)

    def test_an_unknown_state_is_rendered_rather_than_dropped(self) -> None:
        payload = {"operation": {"state": "some_future_state"}, "discovery": {}}
        lines = render_status_lines(payload, now=NOW)
        self.assertIn("Some future state", lines[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
