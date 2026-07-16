"""Reproducible RC preflight, dry-run isolation, and rollback (#32).

Every test is hermetic: git and the test gate are injected, so nothing here
touches the real repository, runs the real suite, or reaches the network.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from opaihub import release_preflight as rp


def _fake_git(*, dirty=(), tags=()):
    def runner(root, args):
        args = list(args)
        if args[:1] == ["status"]:
            out = "".join(f" M {name}\n" for name in dirty)
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[:1] == ["tag"]:
            want = args[-1]
            out = want + "\n" if want in tags else ""
            return subprocess.CompletedProcess(args, 0, out, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return runner


def _write_release_repo(
    root: Path, *, version="0.2.0a2", stage="alpha.2", changelog=None
):
    (root / "opai").mkdir(parents=True, exist_ok=True)
    (root / "opaihub").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "opai"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "opai" / "__init__.py").write_text(
        f'__version__ = "{version}"\n__release_stage__ = "{stage}"\n', encoding="utf-8"
    )
    (root / "opaihub" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    if changelog is None:
        changelog = (
            "# Changelog\n\n## 0.2.0 Alpha.2\n\n"
            "A real set of notes describing what shipped in this release.\n"
        )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "LICENSE").write_text("X" * 200, encoding="utf-8")
    (root / "README.md").write_text("# OPai\n\nA real readme.\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text(
        "# Contributing\n\nGuidelines.\n", encoding="utf-8"
    )


def _ctx(root, **kw):
    kw.setdefault("git", _fake_git())
    kw.setdefault("now", lambda: "2026-07-21T00:00:00+00:00")
    return rp.ReleaseContext(root=root, **kw)


class HeadingDerivationTests(unittest.TestCase):
    def test_prerelease_and_final_headings(self):
        self.assertEqual(
            rp.changelog_heading_for("0.2.0a2", "alpha.2"), "0.2.0 Alpha.2"
        )
        self.assertEqual(rp.changelog_heading_for("1.0.0", "stable"), "1.0.0")
        self.assertEqual(rp.changelog_heading_for("1.0.0", ""), "1.0.0")

    def test_mismatched_stage_returns_none(self):
        self.assertIsNone(rp.changelog_heading_for("0.2.0a2", "beta.1"))
        self.assertIsNone(rp.changelog_heading_for("not-a-version", "alpha.1"))


class PreflightSuccessTests(unittest.TestCase):
    def test_clean_release_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(_ctx(root))
        self.assertTrue(readiness.ready, [c.to_dict() for c in readiness.blockers])
        self.assertEqual(readiness.version, "0.2.0a2")
        by_id = {c.id: c for c in readiness.checks}
        for check_id in (
            "clean_tree",
            "version_consistency",
            "changelog",
            "license",
            "required_docs",
        ):
            self.assertEqual(by_id[check_id].status, rp.PASS, check_id)
        # Tests + artifacts are skipped (not requested / no manifest), not failed.
        self.assertEqual(by_id["tests"].status, rp.SKIP)
        self.assertEqual(by_id["artifacts"].status, rp.SKIP)


class PreflightBlockerTests(unittest.TestCase):
    """One fixture per blocker — each must flip readiness to blocked."""

    def _blockers(self, root, **kw):
        return {c.id for c in rp.run_preflight(_ctx(root, **kw)).blockers}

    def test_dirty_tree_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            blockers = self._blockers(root, git=_fake_git(dirty=["opai/x.py"]))
        self.assertIn("clean_tree", blockers)

    def test_version_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            (root / "opaihub" / "__init__.py").write_text(
                '__version__ = "9.9.9"\n', encoding="utf-8"
            )
            self.assertIn("version_consistency", self._blockers(root))

    def test_missing_changelog_entry_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(
                root, changelog="# Changelog\n\n## 0.1.0 Pre-Alpha\n\nold stuff here.\n"
            )
            self.assertIn("changelog", self._blockers(root))

    def test_empty_changelog_section_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(
                root,
                changelog="# Changelog\n\n## 0.2.0 Alpha.2\n\n## 0.1.0 Pre-Alpha\n\nold.\n",
            )
            self.assertIn("changelog", self._blockers(root))

    def test_missing_license_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            (root / "LICENSE").write_text("", encoding="utf-8")
            self.assertIn("license", self._blockers(root))

    def test_missing_required_doc_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            (root / "CONTRIBUTING.md").unlink()
            self.assertIn("required_docs", self._blockers(root))

    def test_existing_tag_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            self.assertIn(
                "release_tag", self._blockers(root, git=_fake_git(tags=["v0.2.0a2"]))
            )

    def test_failed_test_gate_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            blockers = self._blockers(
                root, run_tests=True, tests=lambda r: (False, "3 failures")
            )
        self.assertIn("tests", blockers)

    def test_passing_test_gate_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(root, run_tests=True, tests=lambda r: (True, "GREEN"))
            )
        self.assertTrue(readiness.ready)
        self.assertEqual({c.id: c.status for c in readiness.checks}["tests"], rp.PASS)


class ArtifactChecksumTests(unittest.TestCase):
    def _manifest(self, tmp: Path, *, signed=True, corrupt=False, missing=False):
        art = tmp / "OPai-setup.exe"
        art.write_bytes(b"artifact-bytes")
        digest = rp.sha256_of(art)
        if corrupt:
            digest = "0" * 64
        if missing:
            art.unlink()
        manifest = tmp / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "artifacts": [
                        {"path": "OPai-setup.exe", "sha256": digest, "signed": signed}
                    ]
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_valid_signed_artifact_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            readiness = rp.run_preflight(_ctx(root, artifacts_manifest=manifest))
        self.assertTrue(readiness.ready)
        self.assertEqual(
            {c.id: c.status for c in readiness.checks}["artifacts"], rp.PASS
        )

    def test_checksum_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, corrupt=True)
            blockers = {
                c.id
                for c in rp.run_preflight(
                    _ctx(root, artifacts_manifest=manifest)
                ).blockers
            }
        self.assertIn("artifacts", blockers)

    def test_unsigned_artifact_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, signed=False)
            blockers = {
                c.id
                for c in rp.run_preflight(
                    _ctx(root, artifacts_manifest=manifest)
                ).blockers
            }
        self.assertIn("artifacts", blockers)

    def test_missing_artifact_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, missing=True)
            blockers = {
                c.id
                for c in rp.run_preflight(
                    _ctx(root, artifacts_manifest=manifest)
                ).blockers
            }
        self.assertIn("artifacts", blockers)


class DryRunIsolationTests(unittest.TestCase):
    def test_dry_run_disables_and_refuses_every_publish_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(Path(tmp), dry_run=True)
            steps = rp.plan_publish(ctx)
            self.assertEqual([s.name for s in steps], list(rp.PUBLISH_STEPS))
            self.assertTrue(all(not s.enabled for s in steps))
            for step in rp.PUBLISH_STEPS:
                with self.assertRaises(rp.ReleaseDryRunError):
                    rp.execute_publish_step(ctx, step)

    def test_isolation_proof_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = rp.prove_dry_run_isolation(_ctx(Path(tmp), dry_run=True))
        self.assertTrue(proof["isolated"])
        self.assertTrue(proof["publish_steps_disabled"])
        self.assertTrue(proof["network_blocked"])
        self.assertEqual(proof["publish_steps_refused"], list(rp.PUBLISH_STEPS))

    def test_deny_network_blocks_sockets_and_restores_after(self):
        import socket

        with rp.deny_network():
            with self.assertRaises(rp.ReleaseError):
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Restored afterwards.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.close()

    def test_publish_is_refused_even_outside_dry_run(self):
        # Real publishing needs the release host + credentials, never preflight.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(Path(tmp), dry_run=False)
            with self.assertRaises(rp.ReleaseError):
                rp.execute_publish_step(ctx, "create_tag")


class RollbackTests(unittest.TestCase):
    def test_rollback_restores_previous_artifacts_and_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            release_root = tmp / "install"
            release_root.mkdir()
            (release_root / "OPai.bin").write_bytes(b"CURRENT v2")
            pointer = release_root / "active.json"
            pointer.write_text(json.dumps({"version": "0.2.0a2"}), encoding="utf-8")

            staged = tmp / "previous"
            staged.mkdir()
            (staged / "OPai.bin").write_bytes(b"PREVIOUS v1")
            manifest = staged / "manifest.json"
            manifest.write_text(
                json.dumps({"version": "0.2.0a1", "artifacts": [{"path": "OPai.bin"}]}),
                encoding="utf-8",
            )

            result = rp.perform_rollback(
                release_root=release_root,
                previous_manifest=manifest,
                pointer_file=pointer,
            )
            self.assertEqual(result["version"], "0.2.0a1")
            self.assertEqual((release_root / "OPai.bin").read_bytes(), b"PREVIOUS v1")
            self.assertEqual(json.loads(pointer.read_text())["version"], "0.2.0a1")

    def test_rollback_never_touches_user_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            release_root = tmp / "install"
            release_root.mkdir()
            (release_root / "OPai.bin").write_bytes(b"CURRENT")
            # User state lives under the install root but must be preserved.
            user_state = release_root / ".opaihub"
            user_state.mkdir()
            ledger = user_state / "ledger.jsonl"
            ledger.write_bytes(b"precious user data")

            staged = tmp / "previous"
            staged.mkdir()
            (staged / "OPai.bin").write_bytes(b"PREVIOUS")
            # A malicious/buggy manifest that tries to overwrite user state.
            (staged / ".opaihub").mkdir()
            (staged / ".opaihub" / "ledger.jsonl").write_bytes(b"attacker data")
            manifest = staged / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "artifacts": [
                            {"path": "OPai.bin"},
                            {"path": ".opaihub/ledger.jsonl"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(rp.ReleaseError):
                rp.perform_rollback(
                    release_root=release_root,
                    previous_manifest=manifest,
                    pointer_file=release_root / "active.json",
                    user_state_dirs=[user_state],
                )
            # The user's data is byte-for-byte intact.
            self.assertEqual(ledger.read_bytes(), b"precious user data")

    def test_rollback_plan_is_inspectable_and_preserves_user_state(self):
        plan = rp.rollback_plan(
            from_version="0.2.0a2",
            to_version="0.2.0a1",
            previous_manifest={
                "version": "0.2.0a1",
                "artifacts": [{"path": "OPai.bin"}],
            },
        )
        self.assertTrue(plan["preserves_user_state"])
        self.assertEqual(plan["to_version"], "0.2.0a1")
        self.assertIn("OPai.bin", plan["artifacts"])


class ReportTests(unittest.TestCase):
    def test_json_and_markdown_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(_ctx(root))
        payload = json.loads(rp.render_report_json(readiness))
        self.assertEqual(payload["kind"], "opai_rc_preflight")
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["totals"]["blockers"], 0)
        md = rp.render_report_markdown(readiness)
        self.assertIn("READY", md)
        self.assertIn("| Check | Result | Detail |", md)

    def test_blocked_report_lists_blockers_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(_ctx(root, git=_fake_git(dirty=["x.py"])))
        md = rp.render_report_markdown(readiness)
        self.assertIn("BLOCKED", md)
        self.assertIn("## Blockers", md)

    def test_sanitized_evidence_drops_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(root, git=_fake_git(dirty=["secret_local_file.py"]))
            )
        evidence = rp.sanitized_evidence(readiness)
        blob = json.dumps(evidence)
        self.assertNotIn("secret_local_file.py", blob)
        self.assertIn("clean_tree", blob)  # the check id still recorded


class CliTests(unittest.TestCase):
    """The `opai release` CLI is the one documented command (#32 AC)."""

    def _run(self, argv):
        import contextlib
        import io

        from opai.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_preflight_on_a_clean_fixture_repo_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            # A real git checkout so the clean-tree check passes honestly.
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)  # nosec B603 B607
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)  # nosec B603 B607
            subprocess.run(  # nosec B603 B607
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.email=t@t",
                    "-c",
                    "user.name=t",
                    "commit",
                    "-qm",
                    "init",
                ],
                check=True,
            )
            code, out = self._run(
                ["release", "preflight", "--project", str(root), "--format", "json"]
            )
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["version"], "0.2.0a2")

    def test_preflight_blocks_and_writes_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(
                root, changelog="# Changelog\n\n## 0.1.0 Pre-Alpha\n\nold.\n"
            )
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)  # nosec B603 B607
            evidence = root / "evidence.json"
            code, out = self._run(
                [
                    "release",
                    "preflight",
                    "--project",
                    str(root),
                    "--format",
                    "json",
                    "--out",
                    str(evidence),
                ]
            )
            self.assertEqual(code, 1, out)  # blocked -> non-zero exit
            self.assertTrue(evidence.is_file())
            archived = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertIn("changelog", archived["blockers"])
            # Sanitized: check evidence blobs are stripped.
            self.assertTrue(all("evidence" not in c for c in archived["checks"]))

    def test_dry_run_proof_cli_exits_zero_and_proves_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(["release", "dry-run-proof", "--project", tmp])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["isolated"])

    def test_rollback_plan_cli_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "prev.json"
            manifest.write_text(
                json.dumps({"version": "0.1.0", "artifacts": [{"path": "a.bin"}]}),
                encoding="utf-8",
            )
            code, out = self._run(
                [
                    "release",
                    "rollback",
                    "--project",
                    tmp,
                    "--previous-manifest",
                    str(manifest),
                    "--to",
                    "0.1.0",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["plan"]["to_version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
