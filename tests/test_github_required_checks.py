"""Fail-closed consumption of the repository's required GitHub checks."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from opaihub.github_workflow import GitHubAdapter


HEAD = "a" * 40
MOVED_HEAD = "b" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"
TRUSTED_COMMIT = "c" * 40
TRUSTED_TREE = "d" * 40
TRUSTED_BLOB = "e" * 40


def _completed(argv: list[str], payload: object, returncode: int = 0):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(argv, returncode, stdout, "")


def _check(
    name: str,
    *,
    conclusion: str = "success",
    app: str = "github-actions",
    head_sha: str = HEAD,
    run_id: int = 101,
) -> dict[str, Any]:
    return {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": (
            f"https://github.test/acme/repo/actions/runs/{run_id}/job/{run_id + 1}"
        ),
        "app": {"slug": app},
        "started_at": "2026-08-09T10:00:00Z",
        "completed_at": "2026-08-09T10:01:00Z",
    }


class _StrictGitHub:
    def __init__(
        self,
        checks: list[dict[str, Any]],
        *,
        heads: list[str] | None = None,
        run_overrides: dict[int, dict[str, Any]] | None = None,
        trusted_manifest: dict[str, Any] | None = None,
    ) -> None:
        self.checks = checks
        self.heads = list(heads or [HEAD, HEAD])
        self.run_overrides = run_overrides or {}
        self.trusted_manifest = trusted_manifest
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: Any):
        self.calls.append(argv)
        if argv[1:3] == ["pr", "view"]:
            head = self.heads.pop(0) if self.heads else HEAD
            return _completed(argv, {"headRefOid": head})
        if argv[1:3] == ["api", "repos/{owner}/{repo}"]:
            return _completed(argv, {"default_branch": "main"})
        if argv[1] == "api" and "/git/ref/heads/main" in argv[2]:
            return _completed(argv, {"object": {"sha": TRUSTED_COMMIT}})
        if argv[1] == "api" and f"/git/commits/{TRUSTED_COMMIT}" in argv[2]:
            return _completed(argv, {"tree": {"sha": TRUSTED_TREE}})
        if argv[1] == "api" and f"/git/trees/{TRUSTED_TREE}" in argv[2]:
            tree = []
            if self.trusted_manifest is not None:
                tree.append(
                    {
                        "path": ".github/required-checks.json",
                        "type": "blob",
                        "sha": TRUSTED_BLOB,
                    }
                )
            return _completed(argv, {"truncated": False, "tree": tree})
        if argv[1] == "api" and f"/git/blobs/{TRUSTED_BLOB}" in argv[2]:
            raw = json.dumps(self.trusted_manifest).encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            return _completed(
                argv,
                {
                    "encoding": "base64",
                    "content": "\n".join(
                        encoded[index : index + 60]
                        for index in range(0, len(encoded), 60)
                    ),
                },
            )
        if (
            argv[1] == "api"
            and "/commits/" in argv[2]
            and "/git/commits/" not in argv[2]
        ):
            return _completed(
                argv,
                {"total_count": len(self.checks), "check_runs": self.checks},
            )
        if argv[1] == "api" and "/actions/runs/" in argv[2]:
            run_id = int(argv[2].rsplit("/", 1)[-1])
            payload = {
                "id": run_id,
                "head_sha": HEAD,
                "path": WORKFLOW_PATH,
                "name": "Vesta CI (hosted)",
                "event": "pull_request",
            }
            payload.update(self.run_overrides.get(run_id, {}))
            return _completed(argv, payload)
        raise AssertionError(argv)


class RequiredCheckConsumerTests(unittest.TestCase):
    def _adapter(
        self,
        required: list[str | dict[str, Any]],
        checks: list[dict[str, Any]],
        trusted_required: list[str | dict[str, Any]] | None = None,
        **fake_kwargs: Any,
    ) -> tuple[GitHubAdapter, _StrictGitHub, tempfile.TemporaryDirectory[str]]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        manifest = {
            "schema_version": 1,
            "workflow": WORKFLOW_PATH,
            "required_checks": required,
        }
        path = root / ".github" / "required-checks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        trusted_manifest = dict(manifest)
        if trusted_required is not None:
            trusted_manifest["required_checks"] = trusted_required
        fake = _StrictGitHub(checks, trusted_manifest=trusted_manifest, **fake_kwargs)
        return GitHubAdapter(root, run=fake), fake, tmp

    def test_all_exact_manifest_checks_from_current_trusted_workflow_pass(self):
        adapter, fake, tmp = self._adapter(
            ["required-a", "required-b"],
            [_check("required-a", run_id=101), _check("required-b", run_id=102)],
        )
        self.addCleanup(tmp.cleanup)

        checks = adapter._load_pr_checks(7)

        self.assertEqual(
            [item["name"] for item in checks], ["required-a", "required-b"]
        )
        self.assertEqual(adapter._check_state(checks), "passed")
        self.assertTrue(all(item["head_sha"] == HEAD for item in checks))
        self.assertTrue(all(item["app"] == "github-actions" for item in checks))
        self.assertTrue(
            any("/commits/" + HEAD + "/check-runs" in call[2] for call in fake.calls)
        )

    def test_missing_required_name_stays_pending_and_extra_success_is_ignored(self):
        adapter, _fake, tmp = self._adapter(
            ["required-a", "required-b"],
            [_check("required-a"), _check("similar-but-not-required", run_id=202)],
        )
        self.addCleanup(tmp.cleanup)

        checks = adapter._load_pr_checks(7)

        self.assertEqual(
            [item["name"] for item in checks], ["required-a", "required-b"]
        )
        self.assertEqual(checks[1]["bucket"], "missing")
        self.assertEqual(adapter._check_state(checks), "pending")

    def test_skipped_and_neutral_required_checks_are_failures(self):
        for conclusion in ("skipped", "neutral"):
            with self.subTest(conclusion=conclusion):
                adapter, _fake, tmp = self._adapter(
                    ["required-a"], [_check("required-a", conclusion=conclusion)]
                )
                try:
                    checks = adapter._load_pr_checks(7)
                    self.assertEqual(adapter._check_state(checks), "failed")
                finally:
                    tmp.cleanup()

    def test_untrusted_app_or_wrong_workflow_cannot_satisfy_required_name(self):
        cases = (
            ({"app": "third-party"}, {}, "untrusted"),
            ({}, {101: {"path": ".github/workflows/other.yml"}}, "wrong_workflow"),
            ({}, {101: {"event": "workflow_dispatch"}}, "untrusted_event"),
        )
        for check_overrides, run_overrides, expected_bucket in cases:
            with self.subTest(expected_bucket=expected_bucket):
                check = _check(
                    "required-a", app=check_overrides.get("app", "github-actions")
                )
                adapter, _fake, tmp = self._adapter(
                    ["required-a"], [check], run_overrides=run_overrides
                )
                try:
                    checks = adapter._load_pr_checks(7)
                    self.assertEqual(checks[0]["bucket"], expected_bucket)
                    self.assertEqual(adapter._check_state(checks), "failed")
                finally:
                    tmp.cleanup()

    def test_stale_check_sha_and_duplicate_required_name_fail_closed(self):
        stale, _fake, stale_tmp = self._adapter(
            ["required-a"], [_check("required-a", head_sha=MOVED_HEAD)]
        )
        duplicate, _fake, duplicate_tmp = self._adapter(
            ["required-a"],
            [_check("required-a", run_id=101), _check("required-a", run_id=102)],
        )
        self.addCleanup(stale_tmp.cleanup)
        self.addCleanup(duplicate_tmp.cleanup)

        stale_checks = stale._load_pr_checks(7)
        duplicate_checks = duplicate._load_pr_checks(7)

        self.assertEqual(stale_checks[0]["bucket"], "stale")
        self.assertEqual(stale._check_state(stale_checks), "failed")
        self.assertEqual(duplicate_checks[0]["bucket"], "duplicate")
        self.assertEqual(duplicate._check_state(duplicate_checks), "failed")

    def test_head_move_while_loading_invalidates_prior_evidence(self):
        adapter, _fake, tmp = self._adapter(
            ["required-a"],
            [_check("required-a")],
            heads=[HEAD, MOVED_HEAD],
        )
        self.addCleanup(tmp.cleanup)

        checks = adapter._load_pr_checks(7)

        self.assertEqual(checks[0]["bucket"], "head_moved")
        self.assertEqual(adapter._check_state(checks), "pending")

    def test_object_contract_can_pin_each_check_to_a_workflow_name(self):
        adapter, _fake, tmp = self._adapter(
            [
                {
                    "name": "required-a",
                    "workflow": "Expected workflow name",
                    "trusted_app": "github-actions",
                    "events": ["pull_request"],
                }
            ],
            [_check("required-a")],
            run_overrides={101: {"name": "Unexpected workflow name"}},
        )
        self.addCleanup(tmp.cleanup)

        checks = adapter._load_pr_checks(7)

        self.assertEqual(checks[0]["bucket"], "wrong_workflow")
        self.assertEqual(adapter._check_state(checks), "failed")

    def test_candidate_checkout_cannot_shrink_the_trusted_required_set(self):
        adapter, _fake, tmp = self._adapter(
            ["required-a"],
            [_check("required-a")],
            trusted_required=["required-a", "required-b"],
        )
        self.addCleanup(tmp.cleanup)

        checks = adapter._load_pr_checks(7)

        self.assertEqual(
            [item["name"] for item in checks], ["required-a", "required-b"]
        )
        self.assertEqual(checks[1]["bucket"], "missing")
        self.assertEqual(adapter._check_state(checks), "pending")

    def test_candidate_cannot_delete_manifest_to_restore_legacy_checks(self):
        adapter, _fake, tmp = self._adapter(
            ["required-a", "required-b"],
            [_check("required-a")],
        )
        self.addCleanup(tmp.cleanup)
        local_manifest = Path(tmp.name) / ".github" / "required-checks.json"
        local_manifest.unlink()
        (Path(tmp.name) / ".git").mkdir()

        checks = adapter._load_pr_checks(7)

        self.assertEqual(
            [item["name"] for item in checks], ["required-a", "required-b"]
        )
        self.assertEqual(checks[1]["bucket"], "missing")


if __name__ == "__main__":
    unittest.main()
