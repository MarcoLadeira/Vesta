"""#613 security requirement: least-privilege database permissions.

The requirement says "on supported OSes", and the implementation used to be
``chmod``, which is a no-op on Windows. The docstring said so plainly, which is
better than implying a guarantee, but honest is not the same as implemented.

What the gap actually cost, measured rather than argued. A journal created
under a parent directory that grants local users read access -- an ordinary
shape for a project folder on a shared or badly-configured path -- inherited:

    BUILTIN\\Users:(I)(RX)
    NT AUTHORITY\\SYSTEM:(I)(F)
    BUILTIN\\Administrators:(I)(F)

and after the fix carries only the owner. The journal holds task text, model
routes, cost records and approval fingerprints, so "every local user on this
machine can read it" is not a footnote.

The Windows tests build that parent deliberately, because the default ACL in a
user's own temp directory is already private -- a test written there would pass
with the fix reverted and prove nothing. That is exactly the shape of test this
file exists to avoid.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed argv, throwaway directories
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_store
from opaihub.journal_store import journal_path, open_store, permissions_health

#: BUILTIN\Users by SID, so the tests do not depend on a localised group name.
USERS_SID = "*S-1-5-32-545"

windows_only = unittest.skipUnless(os.name == "nt", "Windows ACL behaviour")
posix_only = unittest.skipIf(os.name == "nt", "POSIX mode bits")


def _icacls(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607 - fixed argv, throwaway directories
        ["icacls", *args], capture_output=True, text=True, check=False
    )


class _PermissionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class PosixPermissionsTests(_PermissionFixture):
    @posix_only
    def test_a_new_journal_is_owner_only(self):
        open_store(self.root).close()

        facts = permissions_health(journal_path(self.root))

        self.assertTrue(facts["checked"])
        self.assertTrue(facts["restricted"], facts["detail"])

    @posix_only
    def test_a_widened_journal_is_reported(self):
        open_store(self.root).close()
        path = journal_path(self.root)
        os.chmod(path, 0o644)

        facts = permissions_health(path)

        self.assertFalse(facts["restricted"])


class WindowsPermissionsTests(unittest.TestCase):
    """Built on a parent that grants local Users, because that is the case."""

    def setUp(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows ACL behaviour")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup)
        self.base = Path(self._tmp.name)
        result = _icacls(str(self.base), "/grant", f"{USERS_SID}:(OI)(CI)(RX)")
        if result.returncode != 0:
            self.skipTest("cannot grant Users on this filesystem")

    def _cleanup(self) -> None:
        _icacls(str(self.base), "/reset", "/t", "/q")
        self._tmp.cleanup()

    def _journal_in(self, name: str) -> Path:
        root = self.base / name
        root.mkdir()
        open_store(root).close()
        return journal_path(root)

    def test_a_new_journal_does_not_inherit_local_user_access(self):
        path = self._journal_in("guarded")

        listing = _icacls(str(path)).stdout

        self.assertNotIn("BUILTIN\\Users", listing)

    def test_the_health_check_agrees_it_is_owner_only(self):
        path = self._journal_in("guarded")

        facts = permissions_health(path)

        self.assertTrue(facts["checked"])
        self.assertTrue(facts["restricted"], facts["detail"])

    def test_without_the_acl_step_the_journal_is_world_readable(self):
        """The measurement the fix exists for; also the teeth for the test above.

        If this ever stops failing to be restricted, the parent no longer grants
        Users and every other Windows test here has quietly become vacuous.
        """

        root = self.base / "unguarded"
        root.mkdir()
        with mock.patch.object(
            journal_store,
            "_restrict_permissions_windows",
            return_value=(False, "disabled"),
        ):
            open_store(root).close()

        facts = permissions_health(journal_path(root))

        self.assertFalse(
            facts["restricted"],
            "the parent directory no longer grants Users; these tests prove nothing",
        )
        self.assertIn("BUILTIN\\Users", facts["detail"])

    def test_a_journal_widened_after_creation_is_reported(self):
        """The case that matters most: a database copied in from elsewhere."""

        path = self._journal_in("guarded")
        _icacls(str(path), "/grant", f"{USERS_SID}:(RX)")

        facts = permissions_health(path)

        self.assertFalse(facts["restricted"])


class TheHardeningNeverBreaksTheStoreTests(_PermissionFixture):
    """A journal that cannot be locked down is still a journal.

    Refusing to open it would trade a confidentiality problem for an
    availability one, and the availability problem is the one that stops
    somebody working.
    """

    def test_a_failing_icacls_does_not_prevent_opening(self):
        with mock.patch.object(
            journal_store.subprocess, "run", side_effect=OSError("icacls missing")
        ):
            store = open_store(self.root)

        self.addCleanup(store.close)
        self.assertTrue(journal_path(self.root).exists())

    def test_a_failure_is_reported_rather_than_swallowed(self):
        if os.name != "nt":
            self.skipTest("Windows ACL behaviour")

        with mock.patch.object(
            journal_store.subprocess, "run", side_effect=OSError("icacls missing")
        ):
            ok, detail = journal_store._restrict_permissions_windows(self.root / "x")

        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_a_user_name_that_cannot_be_expressed_is_refused(self):
        """Belt and braces: icacls gets a fixed argv, never a shell."""

        if os.name != "nt":
            self.skipTest("Windows ACL behaviour")

        with mock.patch.dict(os.environ, {"USERNAME": "evil:(F) Everyone"}):
            ok, detail = journal_store._restrict_permissions_windows(self.root / "x")

        self.assertFalse(ok)
        self.assertIn("ACL entry", detail)

    def test_health_of_a_missing_journal_is_not_an_error(self):
        facts = permissions_health(journal_path(self.root))

        self.assertFalse(facts["checked"])
        self.assertFalse(facts["restricted"])

    def test_health_never_raises_when_the_check_itself_fails(self):
        """Reporting a permissions problem must not become one."""

        open_store(self.root).close()

        with mock.patch.object(
            journal_store.subprocess, "run", side_effect=OSError("icacls gone")
        ):
            facts = permissions_health(journal_path(self.root))

        # On POSIX the subprocess is never reached and the answer is real; on
        # Windows the check could not run and says so. Neither raises, which is
        # the whole assertion.
        self.assertIn("checked", facts)
        self.assertIsInstance(facts["detail"], str)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
