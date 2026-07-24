"""GitHub Copilot account connector.

OPai already routes through the user's logged-in ``claude`` and ``codex`` CLIs;
this suite covers the third connector, ``copilot`` (the GitHub Copilot CLI). It
asserts detection (auth file *or* GH token env), the non-interactive command the
runner builds for each mode, model expansion in the picker, the proxy/app_state
dispatch, and that nothing here ever launches a real (paid) CLI.

Everything is faked: ``_which`` is mocked, ``_hidden_run`` is patched, and the
GH token env vars are scrubbed so an ambient token on the CI runner can't leak
into a result.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, isolated_home, make_repo
from opaihub import accounts
from opaihub.accounts import (
    ACCOUNT_SPECS,
    COPILOT_MODELS,
    AccountRunner,
    account_models,
    list_connected_accounts,
    runner_for_account,
)
from opaihub.gui_pipeline import handle_gui_message
from opaihub.ledger import EVENT_MODEL_CALL, read_events
from opaihub.proxy import SUPPORTED_AGENTS, proxy_run

# Env vars the copilot detector treats as a "connected" signal. Tests scrub
# these so a real token on the developer's box (or a CI runner) can't make an
# "isolated" home look authenticated.
_TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_API_KEY")


@contextlib.contextmanager
def _no_token_env():
    """Remove every GH/Copilot token env var for the duration of the block."""
    with mock.patch.dict(os.environ, {}, clear=False):
        for name in _TOKEN_ENV:
            os.environ.pop(name, None)
        yield


def _connect_copilot(home: Path) -> Path:
    """Drop the plaintext config file the CLI writes when a keychain is absent."""
    auth = home / ".copilot" / "config.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text("{}", encoding="utf-8")
    return auth


def _copilot_runner(model: str | None = None) -> AccountRunner:
    return AccountRunner("copilot", "/usr/bin/copilot", model=model)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
class CopilotDetectionTests(unittest.TestCase):
    def test_copilot_is_a_registered_account_spec(self):
        ids = {spec["id"] for spec in ACCOUNT_SPECS}
        self.assertIn("copilot", ids)
        spec = next(s for s in ACCOUNT_SPECS if s["id"] == "copilot")
        self.assertEqual(spec["cli"], "copilot")
        self.assertEqual(spec["vendor"], "GitHub Copilot CLI")
        self.assertIn(".copilot/config.json", spec["auth_files"])

    def test_config_json_means_connected(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertTrue(copilot["connected"])
            self.assertTrue(copilot["authenticated"])
            self.assertTrue(copilot["cli_present"])

    def test_gh_token_env_means_authenticated(self):
        with isolated_home() as home, _no_token_env():
            os.environ["GH_TOKEN"] = "x-not-a-real-token"
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertTrue(copilot["authenticated"])
            self.assertTrue(copilot["connected"])

    def test_github_token_env_means_authenticated(self):
        with isolated_home() as home, _no_token_env():
            os.environ["GITHUB_TOKEN"] = "x-not-a-real-token"
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertTrue(copilot["connected"])

    def test_cli_missing_is_not_connected_even_when_authed(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value=None):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertTrue(copilot["authenticated"])
            self.assertFalse(copilot["cli_present"])
            self.assertFalse(copilot["connected"])

    def test_empty_home_no_env_is_not_connected(self):
        with isolated_home() as home, _no_token_env():
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertFalse(copilot["authenticated"])
            self.assertFalse(copilot["connected"])

    def test_login_hint_mentions_copilot(self):
        spec = next(s for s in ACCOUNT_SPECS if s["id"] == "copilot")
        self.assertIn("copilot", spec["login_hint"].lower())

    def test_detection_never_reads_token_value(self):
        # Presence of the env var is enough; OPai must not touch the secret.
        with isolated_home() as home, _no_token_env():
            os.environ["GH_TOKEN"] = "super-secret-value"
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                found = list_connected_accounts(home)
            copilot = next(a for a in found if a["id"] == "copilot")
            self.assertNotIn("super-secret-value", repr(copilot))


# --------------------------------------------------------------------------- #
# Picker / account_models
# --------------------------------------------------------------------------- #
class CopilotModelPickerTests(unittest.TestCase):
    def test_copilot_expands_to_all_models_when_connected(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                options = account_models(home)
            copilot_opts = [o for o in options if o["provider"] == "copilot"]
            self.assertEqual(len(copilot_opts), len(COPILOT_MODELS))
            self.assertGreater(len(copilot_opts), 1)

    def test_copilot_option_ids_are_namespaced(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                options = account_models(home)
            for opt in (o for o in options if o["provider"] == "copilot"):
                self.assertTrue(opt["id"].startswith("account:copilot:"))
                self.assertEqual(opt["id"], f"account:copilot:{opt['model']}")

    def test_copilot_options_are_paid_and_have_speed(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                options = account_models(home)
            for opt in (o for o in options if o["provider"] == "copilot"):
                self.assertTrue(opt["paid"])
                self.assertEqual(opt["kind"], "account")
                self.assertTrue(opt["available"])
                self.assertIn("speed", opt)

    def test_copilot_absent_from_picker_when_not_connected(self):
        with isolated_home() as home, _no_token_env():
            with mock.patch.object(accounts, "_which", return_value=None):
                options = account_models(home)
            self.assertEqual([o for o in options if o["provider"] == "copilot"], [])

    def test_include_unavailable_lists_copilot_as_disabled(self):
        with isolated_home() as home, _no_token_env():
            with mock.patch.object(accounts, "_which", return_value=None):
                options = account_models(home, include_unavailable=True)
            copilot_opts = [o for o in options if o["provider"] == "copilot"]
            self.assertEqual(len(copilot_opts), len(COPILOT_MODELS))
            for opt in copilot_opts:
                self.assertFalse(opt["available"])
                self.assertIsNotNone(opt["disabled_reason"])


# --------------------------------------------------------------------------- #
# Command construction (the heart of the connector)
# --------------------------------------------------------------------------- #
class CopilotBuildCommandTests(unittest.TestCase):
    def test_scoped_permission_probe_requires_every_bounded_cli_flag(self):
        full_help = _FakeProc(
            stdout=(
                "--available-tools --allow-tool --deny-tool --add-dir "
                "-C, --cwd"
            )
        )
        legacy_help = _FakeProc(stdout="--allow-all-tools --no-ask-user")

        self.assertTrue(
            accounts._copilot_supports_scoped_permissions(
                {"id": "copilot", "cli_path": "/usr/bin/copilot"},
                run=lambda argv: full_help,
            )
        )
        self.assertFalse(
            accounts._copilot_supports_scoped_permissions(
                {"id": "copilot", "cli_path": "/usr/bin/copilot"},
                run=lambda argv: legacy_help,
            )
        )

    def test_ask_mode_is_read_only(self):
        runner = _copilot_runner(model="gpt-5.4")
        cmd = runner.build_command("refactor the parser", mode="ask")
        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("-s", cmd)
        self.assertIn("--no-ask-user", cmd)
        # The read-only guard rides in the prompt, which the CLI takes via -p.
        self.assertEqual(cmd[-2], "-p")
        self.assertIn("Do not modify files", cmd[-1])
        self.assertIn("refactor the parser", cmd[-1])

    def test_safe_auto_command_uses_only_workspace_scoped_edit_tools(self):
        runner = _copilot_runner()
        root = Path("/work/repo").resolve()
        cmd = runner.build_command(
            "ship it", mode="safe-auto", project_root=root
        )
        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("--available-tools=view,grep,glob,edit", cmd)
        self.assertIn("--allow-tool=edit", cmd)
        self.assertIn("-C", cmd)
        self.assertIn(str(root), cmd)
        self.assertNotIn("Do not modify files", cmd[-1])

    def test_full_auto_command_never_enables_unbounded_tools(self):
        runner = _copilot_runner()
        cmd = runner.build_command("build a feature", mode="full-auto")
        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("--available-tools=view,grep,glob,edit", cmd)
        self.assertIn("--allow-tool=edit", cmd)
        self.assertNotIn("Do not modify files", cmd[-1])

    def test_model_flag_is_passed_through(self):
        runner = _copilot_runner(model="claude-sonnet-4.6")
        cmd = runner.build_command("hi", mode="ask")
        self.assertIn("--model=claude-sonnet-4.6", cmd)

    def test_model_flag_omitted_when_unset(self):
        runner = _copilot_runner(model=None)
        cmd = runner.build_command("hi", mode="ask")
        self.assertFalse(any(a.startswith("--model") for a in cmd))

    def test_prompt_is_always_the_final_argument(self):
        # `-p` consumes the next token as the prompt, so the prompt must be last
        # and immediately preceded by `-p` in every mode.
        runner = _copilot_runner(model="gpt-5.4")
        for mode in ("ask", "plan", "approve-edits", "safe-auto", "full-auto"):
            with self.subTest(mode=mode):
                cmd = runner.build_command("do the thing", mode=mode)
                self.assertEqual(cmd[-2], "-p")
                self.assertIn("do the thing", cmd[-1])

    def test_plan_mode_is_read_only(self):
        runner = _copilot_runner()
        cmd = runner.build_command("plan a migration", mode="plan")
        self.assertNotIn("--allow-all-tools", cmd)
        self.assertIn("Do not modify files", cmd[-1])

    def test_command_starts_with_the_resolved_cli_path(self):
        runner = _copilot_runner()
        cmd = runner.build_command("hello", mode="ask")
        self.assertEqual(cmd[0], "/usr/bin/copilot")

    def test_unknown_account_still_raises(self):
        runner = AccountRunner("nope", "/usr/bin/nope")
        with self.assertRaises(ValueError):
            runner.build_command("x")


# --------------------------------------------------------------------------- #
# runner_for_account
# --------------------------------------------------------------------------- #
class CopilotRunnerFactoryTests(unittest.TestCase):
    def test_runner_built_when_connected(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                runner = runner_for_account("copilot", home=home)
            self.assertIsNotNone(runner)
            self.assertEqual(runner.account_id, "copilot")  # type: ignore[union-attr]

    def test_none_when_not_connected(self):
        with isolated_home() as home, _no_token_env():
            with mock.patch.object(accounts, "_which", return_value=None):
                runner = runner_for_account("copilot", home=home)
            self.assertIsNone(runner)

    def test_model_is_passed_through(self):
        with isolated_home() as home, _no_token_env():
            _connect_copilot(home)
            with mock.patch.object(accounts, "_which", return_value="/usr/bin/copilot"):
                runner = runner_for_account("copilot", model="gpt-5.4", home=home)
            self.assertIsNotNone(runner)
            self.assertEqual(runner.model, "gpt-5.4")  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# complete(): drives the CLI but never launches it for real
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


class CopilotCompleteTests(unittest.TestCase):
    def test_returns_plain_text_response(self):
        runner = _copilot_runner(model="gpt-5.4")
        with mock.patch.object(
            accounts, "_hidden_run", return_value=_FakeProc(stdout="the answer\n")
        ) as run:
            result = runner.complete("question", mode="ask")
        self.assertEqual(result["text"], "the answer")
        self.assertIsNone(result["cost"])
        # The prompt actually went to the copilot CLI, last arg after -p.
        sent_cmd = run.call_args.args[0]
        self.assertEqual(sent_cmd[0], "/usr/bin/copilot")
        self.assertEqual(sent_cmd[-2], "-p")

    def test_timeout_is_reported_cleanly(self):
        runner = _copilot_runner()
        with mock.patch.object(
            accounts,
            "_hidden_run",
            side_effect=subprocess.TimeoutExpired(cmd="copilot", timeout=1),
        ):
            result = runner.complete("question", mode="ask")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["text"], "")

    def test_falls_back_to_stderr_when_stdout_empty(self):
        runner = _copilot_runner()
        with mock.patch.object(
            accounts, "_hidden_run", return_value=_FakeProc(stdout="", stderr="boom")
        ):
            result = runner.complete("question", mode="ask")
        self.assertEqual(result["text"], "boom")


# --------------------------------------------------------------------------- #
# Proxy + app_state dispatch
# --------------------------------------------------------------------------- #
def _model_calls(root: Path) -> list[dict]:
    return [e for e in read_events(root) if e.get("event_type") == EVENT_MODEL_CALL]


class CopilotProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_copilot_is_a_supported_agent(self):
        self.assertIn("copilot", SUPPORTED_AGENTS)

    def test_proxy_routes_and_records_copilot(self):
        fake = FakeAccountRunner(account_id="copilot", model="gpt-5.4", text="done")
        result = proxy_run(
            self.root, "summarize my changes", agent="copilot", mode="ask", runner=fake
        )
        self.assertEqual(result["status"], "answered_by_account")
        self.assertTrue(result["captured"])
        self.assertEqual(result["agent"], "copilot")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(_model_calls(self.root)), 1)

    def test_proxy_gate_blocks_destructive_copilot_before_spend(self):
        fake = FakeAccountRunner(account_id="copilot", text="should not run")
        result = proxy_run(
            self.root,
            "rm -rf the whole project",
            agent="copilot",
            mode="ask",
            runner=fake,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(len(_model_calls(self.root)), 0)


class CopilotAppStateAskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ask_routes_account_copilot_choice(self):
        from opai import app_state as A

        fake = FakeAccountRunner(account_id="copilot", model="gpt-5.4", text="hello")
        result = A.ask(
            self.root,
            "explain this repo",
            "account:copilot:gpt-5.4",
            account_runner=fake,
        )
        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["provider"], "copilot")
        self.assertEqual(len(fake.calls), 1)

    def test_copilot_edit_request_fails_before_runner_launch(self):
        from opai import app_state as A

        fake = FakeAccountRunner(account_id="copilot", text="should not run")
        result = A.ask(
            self.root,
            "Fix app.py",
            "account:copilot:gpt-5.4",
            allow_edits=True,
            mode="safe-auto",
            account_runner=fake,
        )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["provider"], "copilot")
        self.assertEqual(result["capability"], "edit_files")
        self.assertEqual(fake.calls, [])
        self.assertEqual(read_events(self.root), [])

    def test_scoped_capable_copilot_can_run_an_edit_request(self):
        from opai import app_state as A

        fake = FakeAccountRunner(account_id="copilot", text="edited")
        fake.supports_scoped_editing = lambda: True
        result = A.ask(
            self.root,
            "Fix app.py",
            "account:copilot:gpt-5.4",
            allow_edits=True,
            mode="safe-auto",
            account_runner=fake,
        )

        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(fake.calls[0]["allow_edits"])

    def test_gui_copilot_edit_mismatch_is_blocked_without_receipt_or_spend(self):
        fake = FakeAccountRunner(account_id="copilot", text="should not run")
        result = handle_gui_message(
            self.root,
            "Fix app.py",
            model_id="account:copilot:gpt-5.4",
            mode="safe-auto",
            account_runner=fake,
        )

        self.assertEqual(result["status"], "capability_mismatch")
        self.assertEqual(result["receipt"], {})
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["workflow"]["phase"], "blocked")
        self.assertEqual(fake.calls, [])
        terminal_events = [
            event
            for event in read_events(self.root)
            if event.get("event_type") == "completion_verdict"
        ]
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(terminal_events[0]["verdict"], "blocked")
        self.assertEqual(terminal_events[0]["reason_code"], "capability_mismatch")


if __name__ == "__main__":
    unittest.main()
