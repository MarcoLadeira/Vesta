"""Installs from before the OPai -> Vesta rename upgrade automatically and safely.

Every test works in temporary directories and never touches the real home
folder, keychain or environment. unittest style: the CI gate runs unittest.
"""

from __future__ import annotations

import errno
import io
import json
import os
import tempfile
import tokenize
import unittest
from pathlib import Path
from unittest import mock

import vesta
from vesta import legacy
from vesta.context_slim import AI_IGNORE_PATTERNS

REPO = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _TempHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.old = self.home / ".opai"
        self.new = self.home / ".vesta"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def migrate(self, **kwargs):
        kwargs.setdefault("protected_paths", [])
        return legacy.migrate_home(self.home, **kwargs)


class HomeMigrationTests(_TempHome):
    def test_moves_every_child_and_removes_the_empty_legacy_folder(self):
        _write(self.old / "global.json", '{"targets": ["shell"]}')
        _write(self.old / "models.json", "{}")
        _write(self.old / "recents" / "abc.json", "[1]")

        result = self.migrate()

        self.assertEqual(result.status, "migrated")
        self.assertEqual(
            sorted(result.moved), ["global.json", "models.json", "recents"]
        )
        self.assertTrue(result.legacy_removed)
        self.assertFalse(self.old.exists())
        self.assertEqual(_read(self.new / "global.json"), '{"targets": ["shell"]}')
        self.assertEqual(_read(self.new / "recents" / "abc.json"), "[1]")

    def test_nothing_to_do_without_a_legacy_folder(self):
        result = self.migrate()
        self.assertEqual(result.status, "absent")
        self.assertFalse(self.new.exists())

    def test_is_idempotent(self):
        _write(self.old / "models.json", "{}")
        self.migrate()
        snapshot = sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))

        second = self.migrate()

        self.assertEqual(second.status, "absent")
        self.assertEqual(
            sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")),
            snapshot,
        )

    def test_partial_state_converges_and_never_overwrites(self):
        # A previous run moved some children; the current copy of a file that
        # exists on both sides wins and the old copy is kept untouched.
        _write(self.new / "global.json", "new")
        _write(self.new / "recents" / "b.json", "b")
        _write(self.old / "global.json", "old")
        _write(self.old / "recents" / "a.json", "a")
        _write(self.old / "github.json", "{}")

        result = self.migrate()

        self.assertEqual(result.status, "partial")
        self.assertIn("github.json", result.moved)
        self.assertIn("recents/a.json", result.moved)
        self.assertEqual(result.conflicts, ["global.json"])
        self.assertEqual(_read(self.new / "global.json"), "new")
        self.assertEqual(_read(self.old / "global.json"), "old")
        self.assertEqual(_read(self.new / "recents" / "a.json"), "a")
        self.assertEqual(_read(self.new / "recents" / "b.json"), "b")
        self.assertFalse((self.old / "recents").exists())
        self.assertTrue(self.old.exists(), "legacy folder still holds a file")

    def test_two_code_checkouts_are_never_merged(self):
        _write(self.old / "source" / ".git" / "HEAD", "old")
        _write(self.old / "source" / "a.py", "old")
        _write(self.new / "source" / ".git" / "HEAD", "new")

        result = self.migrate()

        self.assertEqual(result.conflicts, ["source"])
        self.assertTrue((self.old / "source" / "a.py").exists())
        self.assertFalse((self.new / "source" / "a.py").exists())

    def test_a_child_in_use_is_reported_read_in_place_and_retried(self):
        _write(self.old / "models.json", "legacy-models")
        _write(self.old / "global.json", "{}")

        def in_use(source: Path, target: Path) -> None:
            if source.name == "models.json":
                raise PermissionError(errno.EACCES, "in use", str(source))
            legacy._move_no_clobber(source, target)

        result = self.migrate(mover=in_use)

        self.assertEqual(result.status, "partial")
        self.assertEqual([item["item"] for item in result.failed], ["models.json"])
        self.assertIn("global.json", result.moved)
        self.assertTrue(self.old.exists())
        # Until it moves, readers and writers use the one live legacy copy.
        self.assertEqual(
            legacy.home_item(self.home, "models.json"), self.old / "models.json"
        )
        self.assertEqual(
            legacy.home_item(self.home, "global.json"), self.new / "global.json"
        )

        retry = self.migrate()

        self.assertEqual(retry.moved, ["models.json"])
        self.assertTrue(retry.legacy_removed)
        self.assertEqual(_read(self.new / "models.json"), "legacy-models")
        self.assertEqual(
            legacy.home_item(self.home, "models.json"), self.new / "models.json"
        )

    def test_an_unexpected_error_never_reaches_startup(self):
        _write(self.old / "models.json", "{}")

        def broken(source: Path, target: Path) -> None:
            raise RuntimeError("boom")

        result = self.migrate(mover=broken)

        self.assertEqual(result.status, "failed")
        self.assertTrue((self.old / "models.json").exists())

    def test_never_moves_the_running_install(self):
        package = _write(self.old / "source" / "vesta" / "__init__.py", "")
        _write(self.old / "source" / "pyproject.toml", "")
        _write(self.old / "models.json", "{}")

        result = self.migrate(protected_paths=[package.parent])

        self.assertEqual(result.running_install, ["source"])
        self.assertEqual(result.moved, ["models.json"])
        self.assertTrue(package.exists())
        self.assertFalse((self.new / "source").exists())
        self.assertFalse(result.legacy_removed)
        self.assertTrue(self.old.is_dir(), "never deleted while source is left")
        # The updater keeps finding the checkout where it runs from.
        self.assertEqual(legacy.home_item(self.home, "source"), self.old / "source")

    def test_an_interpreter_living_in_the_legacy_folder_protects_everything(self):
        _write(self.old / "Lib" / "os.py", "")
        result = self.migrate(protected_paths=[self.old])
        self.assertEqual(result.moved, [])
        self.assertTrue((self.old / "Lib" / "os.py").exists())

    def test_running_install_paths_include_this_package(self):
        paths = {
            os.path.normcase(str(p.resolve())) for p in legacy.running_install_paths()
        }
        self.assertIn(
            os.path.normcase(str(Path(vesta.__file__).resolve().parent)), paths
        )

    def test_move_never_clobbers_an_existing_target(self):
        source = _write(self.home / "a.txt", "a")
        target = _write(self.home / "b.txt", "b")
        with self.assertRaises(FileExistsError):
            legacy._move_no_clobber(source, target)
        self.assertEqual(_read(target), "b")
        self.assertEqual(_read(source), "a")


class ProjectStateMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_state_dir_renames_legacy_state_in_place(self):
        from vestahub.state import load_state, state_dir

        _write(self.root / ".opaihub" / "project.json", '{"notes": ["kept"]}')

        path = state_dir(self.root)

        self.assertEqual(path, self.root / ".vestahub")
        self.assertFalse((self.root / ".opaihub").exists())
        self.assertEqual(load_state(self.root)["notes"], ["kept"])

    def test_attach_keeps_the_legacy_project_configuration(self):
        from vestahub.state import attach_project

        _write(
            self.root / ".opaihub" / "project.json",
            '{"schema_version": 1, "enabled_tools": ["ruff"]}',
        )
        attach_project(self.root)
        self.assertIn("ruff", _read(self.root / ".vestahub" / "project.json"))

    def test_existing_current_state_wins_and_legacy_is_left_alone(self):
        _write(self.root / ".vestahub" / "project.json", "new")
        _write(self.root / ".opaihub" / "project.json", "old")
        self.assertEqual(legacy.migrate_project_state(self.root), "current")
        self.assertEqual(_read(self.root / ".opaihub" / "project.json"), "old")

    def test_rename_failure_is_tolerated_and_retried(self):
        from vestahub.state import state_dir

        _write(self.root / ".opaihub" / "project.json", "{}")
        with mock.patch.object(
            legacy.os, "replace", side_effect=PermissionError(errno.EACCES, "busy")
        ):
            # The old directory stays in use until the move can happen.
            self.assertEqual(state_dir(self.root), self.root / ".opaihub")
        self.assertTrue((self.root / ".opaihub" / "project.json").exists())
        self.assertFalse((self.root / ".vestahub").exists())

        self.assertEqual(legacy.migrate_project_state(self.root), "migrated")
        self.assertTrue((self.root / ".vestahub" / "project.json").exists())

    def test_a_linked_legacy_state_dir_is_not_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        try:
            os.symlink(outside.name, self.root / ".opaihub", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(legacy.migrate_project_state(self.root), "skipped")

    def test_the_home_folder_is_not_renamed(self):
        _write(self.root / ".opaihub" / "worktrees" / "keep", "")
        with mock.patch("pathlib.Path.home", return_value=self.root):
            self.assertEqual(legacy.migrate_project_state(self.root), "skipped")
        self.assertTrue((self.root / ".opaihub" / "worktrees" / "keep").exists())


class EnvironmentTests(unittest.TestCase):
    def test_copies_unset_names_and_drops_the_legacy_ones(self):
        env = {"OPAI_AUTONOMY": "safe", "OPAI_BRANCH": "old", "VESTA_BRANCH": "new"}

        adopted = legacy.adopt_legacy_environment(env)

        self.assertEqual(adopted, ["VESTA_AUTONOMY"])
        self.assertEqual(env, {"VESTA_AUTONOMY": "safe", "VESTA_BRANCH": "new"})

    def test_can_keep_legacy_names_when_asked(self):
        env = {"OPAI_X": "1"}
        legacy.adopt_legacy_environment(env, remove_legacy=False)
        self.assertEqual(env, {"OPAI_X": "1", "VESTA_X": "1"})

    def test_recursion_guard_refuses_with_only_the_legacy_variable(self):
        from vesta import cli

        with mock.patch.dict(os.environ, {"OPAI_AGENT_SESSION": "1"}):
            os.environ.pop("VESTA_AGENT_SESSION", None)
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(cli._refuse_if_nested_agent_session(), 2)
            legacy.adopt_legacy_environment()
            self.assertEqual(os.environ.get("VESTA_AGENT_SESSION"), "1")
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(cli._refuse_if_nested_agent_session(), 2)

    def test_provider_children_carry_vesta_names_only(self):
        from vestahub.proc import AGENT_SESSION_ENV, provider_child_env

        env, removed = provider_child_env(
            "codex",
            {"OPAI_AGENT_SESSION": "parent", "OPAI_AUTONOMY": "full", "PATH": "x"},
        )

        self.assertFalse([name for name in env if name.startswith("OPAI_")])
        self.assertEqual(env[AGENT_SESSION_ENV], "parent")
        self.assertNotIn("VESTA_AUTONOMY", env)
        self.assertEqual(env["PATH"], "x")
        self.assertEqual(removed, [])


class _FakeKeyring:
    priority = 1

    def __init__(self, entries=None, *, fail_set=False, fail_delete=False):
        self.entries = dict(entries or {})
        self.fail_set = fail_set
        self.fail_delete = fail_delete

    def get_password(self, service, username):
        return self.entries.get((service, username))

    def set_password(self, service, username, value):
        if self.fail_set:
            raise RuntimeError("locked")
        self.entries[(service, username)] = value

    def delete_password(self, service, username):
        if self.fail_delete:
            raise RuntimeError("denied")
        del self.entries[(service, username)]


class KeychainMigrationTests(unittest.TestCase):
    OLD = ("OPai/free-model-api", "groq")
    NEW = ("Vesta/free-model-api", "groq")

    def store(self, backend):
        from vestahub.credentials import CredentialStore

        return CredentialStore(backend=backend, environ={})

    def test_a_legacy_entry_moves_to_the_new_service(self):
        backend = _FakeKeyring({self.OLD: "secret"})
        self.assertEqual(self.store(backend).get("groq"), "secret")
        self.assertEqual(backend.entries, {self.NEW: "secret"})

    def test_legacy_entry_is_kept_when_the_write_fails(self):
        backend = _FakeKeyring({self.OLD: "secret"}, fail_set=True)
        self.assertEqual(self.store(backend).get("groq"), "secret")
        self.assertEqual(backend.entries, {self.OLD: "secret"})

    def test_a_failed_legacy_delete_still_returns_the_key(self):
        backend = _FakeKeyring({self.OLD: "secret"}, fail_delete=True)
        self.assertEqual(self.store(backend).get("groq"), "secret")
        self.assertEqual(backend.entries[self.NEW], "secret")

    def test_current_entry_wins_and_legacy_is_untouched(self):
        backend = _FakeKeyring({self.NEW: "new", self.OLD: "old"})
        self.assertEqual(self.store(backend).get("groq"), "new")
        self.assertEqual(backend.entries[self.OLD], "old")

    def test_deleting_a_key_removes_the_legacy_copy_too(self):
        backend = _FakeKeyring({self.NEW: "new", self.OLD: "old"})
        store = self.store(backend)
        store.delete("groq")
        self.assertIsNone(store.get("groq"))
        self.assertEqual(backend.entries, {})

    def test_environment_keys_never_consult_the_keychain(self):
        from vestahub.credentials import CredentialStore

        backend = _FakeKeyring({self.OLD: "old"})
        store = CredentialStore(backend=backend, environ={"GROQ_API_KEY": "env"})
        self.assertEqual(store.get("groq"), "env")
        self.assertEqual(backend.entries, {self.OLD: "old"})


LEGACY_PS_WRAPPER = '$env:OPAI_ACTIVE = "1"\n& $OpaiPython -m opai activate --quiet\n'
LEGACY_SH_WRAPPER = (
    '#!/usr/bin/env sh\nexport OPAI_ACTIVE=1\n"$OPAI_PYTHON" -m opai statusline\n'
)
LEGACY_REGISTRY = (
    '{\n  "schema_version": 1,\n  "skills": [\n'
    '    {"id": "using-opai", "path": "skills/using-opai/SKILL.md"}\n  ]\n}\n'
)


class LegacyGlobalFileTests(_TempHome):
    def setUp(self) -> None:
        super().setUp()
        self._project = tempfile.TemporaryDirectory()
        self.project = Path(self._project.name).resolve()

    def tearDown(self) -> None:
        self._project.cleanup()
        super().tearDown()

    def seed_legacy_files(self) -> dict[str, Path]:
        skills = self.home / ".agents" / "skills" / "opai"
        return {
            "ps": _write(self.new / "bin" / "opai-claude.ps1", LEGACY_PS_WRAPPER),
            "sh": _write(self.new / "bin" / "opai-codex", LEGACY_SH_WRAPPER),
            "user_named": _write(
                self.new / "bin" / "opai-gemini.ps1", "Write-Host me\n"
            ),
            "user_tool": _write(self.new / "bin" / "tool.ps1", "Write-Host tool\n"),
            "instructions": _write(self.new / "instructions" / "OPAI.md", "old\n"),
            "registry": _write(skills / "registry.yaml", LEGACY_REGISTRY),
            "skill": _write(skills / "SKILL.md", "---\nname: opai\n---\nVesta\n"),
            "registered": _write(skills / "using-opai" / "SKILL.md", "x"),
            "user_skill": _write(skills / "mine" / "SKILL.md", "user"),
        }

    def test_removes_only_vesta_generated_global_files(self):
        files = self.seed_legacy_files()

        removed = legacy.remove_legacy_global_artifacts(self.home)

        for key in ("ps", "sh", "instructions", "registry", "skill", "registered"):
            self.assertFalse(files[key].exists(), key)
            self.assertIn(str(files[key]), removed)
        for key in ("user_named", "user_tool", "user_skill"):
            self.assertTrue(files[key].exists(), key)

    def test_a_skill_folder_without_the_vesta_registry_is_not_ours(self):
        skill = _write(
            self.home / ".agents" / "skills" / "opai" / "SKILL.md", "name: opai"
        )
        legacy.remove_legacy_global_artifacts(self.home)
        self.assertTrue(skill.exists())

    def test_install_upgrades_wrappers_aliases_and_claude_memory(self):
        from vesta.integrations import install_global_integrations

        files = self.seed_legacy_files()
        profile = _write(
            self.home / ".bashrc",
            "export EDITOR=vim\n\n# Vesta managed block: start\n"
            f'claude() {{ "{self.home}/.opai/bin/opai-claude" "$@"; }}\n'
            "# Vesta managed block: end\n",
        )
        untouched_profile = self.home / ".zshrc"
        claude = _write(
            self.home / ".claude" / "CLAUDE.md",
            "<!-- OPai managed block: start -->\n"
            f"Read `{self.home}/.opai/instructions/OPAI.md`.\n"
            "<!-- OPai managed block: end -->\n\n# My notes\n",
        )

        result = install_global_integrations(
            self.project, home=self.home, targets=["shell"], ensure_superpowers=False
        )

        self.assertFalse(files["ps"].exists())
        self.assertTrue((self.new / "bin" / "vesta-claude.ps1").exists())
        self.assertTrue(files["user_tool"].exists())
        text = _read(profile)
        self.assertIn("vesta-claude", text)
        self.assertNotIn("opai-claude", text)
        self.assertIn("export EDITOR=vim", text)
        self.assertFalse(untouched_profile.exists())
        memory = _read(claude)
        self.assertIn("VESTA.md", memory)
        self.assertNotIn("OPAI.md", memory)
        self.assertIn("# My notes", memory)
        self.assertFalse(files["instructions"].exists())
        self.assertIn(str(files["ps"]), result["legacy_files_removed"])

    def test_a_profile_saved_in_the_system_code_page_does_not_stop_the_upgrade(self):
        from vesta import integrations

        self.seed_legacy_files()
        old_block = (
            "# OPai managed block: start\n"
            'function claude { & "%s" @args }\n'
            "# OPai managed block: end\n"
        ) % (self.old / "bin" / "opai-claude.ps1")
        ansi = self.home / "Documents" / "WindowsPowerShell"
        ansi.mkdir(parents=True)
        ansi_profile = ansi / "Microsoft.PowerShell_profile.ps1"
        ansi_profile.write_bytes(("# café\n" + old_block).encode("cp1252"))
        bashrc = _write(
            self.home / ".bashrc",
            "# OPai managed block: start\n"
            f'claude() {{ "{self.home}/.opai/bin/opai-claude" "$@"; }}\n'
            "# OPai managed block: end\n",
        )

        with mock.patch.object(
            integrations.locale, "getpreferredencoding", return_value="cp1252"
        ):
            integrations.install_global_integrations(
                self.project, home=self.home, targets=["shell"], ensure_superpowers=False
            )

        raw = ansi_profile.read_bytes()
        self.assertIn("# café".encode("cp1252"), raw)
        self.assertIn(b"vesta-claude", raw)
        self.assertNotIn(b"opai-claude", raw)
        self.assertIn("vesta-claude", _read(bashrc))

    def test_startup_upgrades_an_old_install_end_to_end(self):
        from vesta.bootstrap import _upgrade_legacy_install

        _write(
            self.old / "global.json",
            '{"targets": ["shell"], "project_root": "%s"}'
            % str(self.project).replace("\\", "\\\\"),
        )
        _write(self.old / "bin" / "opai-claude.ps1", LEGACY_PS_WRAPPER)
        _write(self.old / "models.json", "{}")

        with mock.patch.dict(os.environ, {"OPAI_BRANCH": "dev"}):
            for name in ("VESTA_BRANCH", "VESTA_AGENT_SESSION", "OPAI_AGENT_SESSION"):
                os.environ.pop(name, None)
            with mock.patch.object(legacy, "running_install_paths", return_value=[]):
                report = _upgrade_legacy_install(home=self.home)
            self.assertEqual(os.environ.get("VESTA_BRANCH"), "dev")
            self.assertNotIn("OPAI_BRANCH", os.environ)

        self.assertEqual(report["home"]["status"], "migrated")
        self.assertEqual(report["integrations"]["status"], "upgraded")
        self.assertFalse(self.old.exists())
        self.assertTrue((self.new / "models.json").exists())
        self.assertTrue((self.new / "bin" / "vesta-claude.ps1").exists())
        self.assertFalse((self.new / "bin" / "opai-claude.ps1").exists())
        self.assertFalse(legacy.legacy_global_artifacts_present(self.home))

    def test_startup_writes_nothing_without_a_consent_manifest(self):
        from vesta.integrations import upgrade_legacy_global_install

        wrapper = _write(self.new / "bin" / "opai-claude.ps1", LEGACY_PS_WRAPPER)
        result = upgrade_legacy_global_install(self.home)
        self.assertEqual(result["status"], "not_installed")
        self.assertFalse((self.new / "bin" / "vesta-claude.ps1").exists())
        self.assertFalse((self.new / "global.json").exists())
        # The dead old wrapper goes, so startup stops probing for it.
        self.assertFalse(wrapper.exists())
        self.assertFalse(legacy.legacy_global_artifacts_present(self.home))


GENERATED_BLOCK = (
    "<!-- Vesta managed block: start -->\n# Vesta Active\n"
    "<!-- Vesta managed block: end -->"
)
OLD_MARKER_BLOCK = (
    "<!-- OPai managed block: start -->\n# OPai Active\n"
    "<!-- OPai managed block: end -->"
)


class LegacyProjectFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.home = Path(self._home.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        self._home.cleanup()

    def test_activation_removes_generated_legacy_files_and_keeps_user_ones(self):
        from vesta.integrations import activate_project
        from vestahub.context_engine import managed_ignore_lines

        cline = _write(
            self.root / ".clinerules" / "opai.md",
            OLD_MARKER_BLOCK
            + "\n\nFor Cline: prefer OPai local-first routing before model escalation.\n",
        )
        cursor = _write(
            self.root / ".cursor" / "rules" / "opai.mdc",
            "---\ndescription: Vesta local-first, cost-aware routing and safety "
            "policy\nalwaysApply: true\n---\n" + GENERATED_BLOCK + "\n",
        )
        ignore = _write(
            self.root / ".opaiignore",
            "\n".join(
                legacy.legacy_spelling(line)
                for line in [*AI_IGNORE_PATTERNS, "", *managed_ignore_lines()]
            )
            + "\n",
        )

        result = activate_project(self.root, home=self.home, install_global=False)

        for path in (cline, cursor, ignore):
            self.assertFalse(path.exists(), path)
            self.assertIn(str(path), result["legacy_files_removed"])
        self.assertTrue((self.root / ".clinerules" / "vesta.md").exists())
        self.assertTrue((self.root / ".cursor" / "rules" / "vesta.mdc").exists())

    def _old_project_claude_file(self) -> Path:
        return _write(
            self.root / "CLAUDE.md",
            "<!-- OPai managed block: start -->\n# OPai Active\n"
            "OPai is active; run `opai cockpit` if unsure.\n"
            "<!-- OPai managed block: end -->\n\n# House rules\nTabs, not spaces.\n",
        )

    def test_status_flags_an_old_block_instead_of_calling_it_active(self):
        from vesta.clients import client_integrations_status

        self._old_project_claude_file()
        _write(self.home / ".claude" / "CLAUDE.md", GENERATED_BLOCK + "\n")

        status = client_integrations_status(self.root, self.home)
        claude = next(c for c in status["clients"] if c["id"] == "claude")

        self.assertEqual(claude["status"], "broken")
        self.assertIn("claude", status["summary"]["broken"])
        self.assertIn("repair", claude)

    def test_startup_rewrites_old_blocks_in_known_projects(self):
        from vesta.bootstrap import _upgrade_legacy_install
        from vesta.clients import client_integrations_status

        claude = self._old_project_claude_file()
        recent = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(recent, True))
        agents = _write(recent / "AGENTS.md", OLD_MARKER_BLOCK + "\n")
        _write(
            self.home / ".vesta" / "global.json",
            json.dumps({"targets": [], "project_root": str(self.root)}),
        )
        _write(
            self.home / ".vesta" / "gui_workspaces.json", json.dumps([str(recent)])
        )
        _write(self.home / ".claude" / "CLAUDE.md", GENERATED_BLOCK + "\n")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VESTA_AGENT_SESSION", None)
            os.environ.pop("OPAI_AGENT_SESSION", None)
            with mock.patch.object(legacy, "running_install_paths", return_value=[]):
                report = _upgrade_legacy_install(home=self.home)
                again = _upgrade_legacy_install(home=self.home)

        text = _read(claude)
        self.assertIn("<!-- Vesta managed block: start -->", text)
        self.assertNotIn("OPai", text)
        self.assertNotIn("opai cockpit", text)
        self.assertEqual(text.count("Tabs, not spaces."), 1)
        self.assertNotIn("OPai", _read(agents))
        self.assertIn(str(claude), report["project_blocks"])
        self.assertEqual(again["legacy_project_blocks"], [])
        status = client_integrations_status(self.root, self.home)
        self.assertEqual(
            next(c for c in status["clients"] if c["id"] == "claude")["status"],
            "active",
        )

    def test_an_agent_session_never_rewrites_project_files(self):
        from vesta.bootstrap import _upgrade_legacy_install

        claude = self._old_project_claude_file()
        _write(
            self.home / ".vesta" / "global.json",
            json.dumps({"targets": [], "project_root": str(self.root)}),
        )

        with mock.patch.dict(os.environ, {"VESTA_AGENT_SESSION": "1"}):
            with mock.patch.object(legacy, "running_install_paths", return_value=[]):
                _upgrade_legacy_install(home=self.home)

        self.assertIn("OPai managed block", _read(claude))

    def test_uninstall_removes_old_name_files_of_a_project_never_reopened(self):
        from vesta.integrations import uninstall_vesta

        cursor = _write(
            self.root / ".cursor" / "rules" / "opai.mdc",
            "---\ndescription: Vesta local-first, cost-aware routing and safety "
            "policy\nalwaysApply: true\n---\n" + GENERATED_BLOCK + "\n",
        )
        activation = _write(self.root / ".opaihub" / "activation.json", "{}")
        mine = _write(self.root / ".opaihub" / "ledger.jsonl", "{}\n")

        planned = uninstall_vesta(self.root, home=self.home, dry_run=True)
        self.assertIn(str(cursor), planned["planned_path_removals"])
        self.assertIn(str(activation), planned["planned_path_removals"])
        self.assertTrue(cursor.exists())

        uninstall_vesta(self.root, home=self.home, dry_run=False)

        self.assertFalse(cursor.exists())
        self.assertFalse(activation.exists())
        self.assertTrue(mine.exists())

    def test_user_edited_legacy_files_are_kept(self):
        cline = _write(
            self.root / ".clinerules" / "opai.md",
            GENERATED_BLOCK + "\n\nAlways write tests first.\n",
        )
        ignore = _write(
            self.root / ".opaiignore",
            "# OPai context-slimming rules\n.git/\nsecrets/\n",
        )
        plain = _write(self.root / ".cursor" / "rules" / "opai.mdc", "my own rule\n")

        removed = legacy.remove_legacy_project_artifacts(
            self.root, ignore_lines=AI_IGNORE_PATTERNS
        )

        self.assertEqual(removed, [])
        for path in (cline, ignore, plain):
            self.assertTrue(path.exists(), path)


class LegacyNamesLiveInOneModuleTests(unittest.TestCase):
    """The old brand's names are spelled only in vesta/legacy.py."""

    NEEDLES = (
        ".opaihub",
        '".opai"',
        "OPAI_",
        "OPai/free-model-api",
        "OPai managed block",
        "OPai context-slimming",
        "end OPai rules",
        "OPAI.md",
        ".opaiignore",
    )

    def test_no_other_runtime_module_spells_a_legacy_name(self):
        offenders = []
        for package in ("vesta", "vestahub", "opcoding"):
            for path in sorted((REPO / package).rglob("*.py")):
                if path.name == "legacy.py" and path.parent.name == "vesta":
                    continue
                with path.open("rb") as handle:
                    tokens = list(tokenize.tokenize(handle.readline))
                kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
                for token in tokens:
                    if token.type in kinds and any(
                        needle.strip('"') in token.string for needle in self.NEEDLES
                    ):
                        offenders.append(f"{path.relative_to(REPO)}:{token.start[0]}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
