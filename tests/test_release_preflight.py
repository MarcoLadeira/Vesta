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
from unittest import mock

from opai.release_identity import (
    derive_project_release,
    render_documentation_projection,
)
from opaihub import release_preflight as rp


CANDIDATE_SHA = "1" * 40
OTHER_SHA = "2" * 40
REPOSITORY = "MarcoLadeira/OPai"
DESKTOP_WORKFLOW = ".github/workflows/desktop-artifacts.yml"
RUN_ID = "621001"
RUN_ATTEMPT = "2"
RELEASE_TAG = "v0.2.0a2"


def _fake_git(*, dirty=(), tags=(), head=CANDIDATE_SHA):
    def runner(root, args):
        args = list(args)
        if args[:1] == ["status"]:
            out = "".join(f" M {name}\n" for name in dirty)
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[:1] == ["tag"]:
            want = args[-1]
            out = want + "\n" if want in tags else ""
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[:2] == ["cat-file", "-t"]:
            tag = args[-1]
            return subprocess.CompletedProcess(
                args, 0 if tag in tags else 1, "tag\n" if tag in tags else "", ""
            )
        if args[:3] == ["rev-list", "-n", "1"]:
            tag = args[-1]
            return subprocess.CompletedProcess(
                args, 0 if tag in tags else 1, f"{head}\n" if tag in tags else "", ""
            )
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(args, 0, f"{head}\n", "")
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
    base, alpha = version.split("a", 1)
    (root / "opai" / "_generated_release.py").write_text(
        '"""Generated fixture."""\n\n'
        f'APPLICATION_VERSION = "{version}"\n'
        'RELEASE_CHANNEL = "alpha"\n'
        f'RELEASE_STAGE = "{stage}"\n'
        f'DISPLAY_NAME = "Vesta {base} Alpha.{alpha}"\n'
        f'PUBLISHED_TAG = "v{version}"\n',
        encoding="utf-8",
    )
    (root / "opai" / "__init__.py").write_text(
        "from ._generated_release import APPLICATION_VERSION, RELEASE_STAGE\n\n"
        "__version__ = APPLICATION_VERSION\n"
        "__release_stage__ = RELEASE_STAGE\n",
        encoding="utf-8",
    )
    (root / "opaihub" / "__init__.py").write_text(
        "from opai._generated_release import APPLICATION_VERSION\n\n"
        "__version__ = APPLICATION_VERSION\n",
        encoding="utf-8",
    )
    if changelog is None:
        changelog = (
            "# Changelog\n\n## 0.2.0 Alpha.2\n\n"
            "A real set of notes describing what shipped in this release.\n"
        )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "LICENSE").write_text("X" * 200, encoding="utf-8")
    release = derive_project_release(version)
    readme_identity = render_documentation_projection(release, surface="README.md")
    install_identity = render_documentation_projection(
        release, surface="docs/INSTALL_PROOF.md"
    )
    site_identity = render_documentation_projection(release, surface="site/index.html")
    (root / "README.md").write_text(f"# Vesta\n\n{readme_identity}\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "INSTALL_PROOF.md").write_text(
        f"# Install proof\n\n{install_identity}\n", encoding="utf-8"
    )
    (root / "site").mkdir()
    (root / "site" / "index.html").write_text(
        f"<!doctype html>\n{site_identity}\n", encoding="utf-8"
    )
    (root / "CONTRIBUTING.md").write_text(
        "# Contributing\n\nGuidelines.\n", encoding="utf-8"
    )


def _ctx(root, **kw):
    kw.setdefault("git", _fake_git())
    kw.setdefault("now", lambda: "2026-07-21T00:00:00+00:00")
    return rp.ReleaseContext(root=root, **kw)


class DefaultGitRunnerTests(unittest.TestCase):
    @mock.patch("opaihub.release_preflight.subprocess.run")
    @mock.patch(
        "opaihub.release_preflight.shutil.which",
        return_value=r"C:\Program Files\Git\cmd\git.exe",
    )
    def test_default_git_resolves_an_absolute_executable(self, _which, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        rp._default_git(Path("repo"), ["status", "--porcelain"])

        command = run.call_args.args[0]
        self.assertEqual(command[0], r"C:\Program Files\Git\cmd\git.exe")

    @mock.patch("opaihub.release_preflight.subprocess.run")
    @mock.patch("opaihub.release_preflight.shutil.which", return_value=None)
    def test_default_git_fails_closed_when_git_is_unavailable(self, _which, run):
        completed = rp._default_git(Path("repo"), ["status", "--porcelain"])

        run.assert_not_called()
        self.assertEqual(completed.returncode, 127)
        self.assertIn("not found", completed.stderr)


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

    def test_qualification_cannot_be_ready_when_tests_and_artifacts_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                )
            )
        by_id = {c.id: c for c in readiness.checks}
        self.assertFalse(readiness.ready)
        self.assertEqual(by_id["tests"].status, rp.SKIP)
        self.assertEqual(by_id["artifacts"].status, rp.SKIP)
        self.assertEqual({c.id for c in readiness.blockers}, {"tests", "artifacts"})


class CandidateIdentityTests(unittest.TestCase):
    def test_qualification_requires_an_explicit_full_candidate_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(root, qualification_required=True, candidate_sha=None)
            )
        by_id = {c.id: c for c in readiness.checks}
        self.assertFalse(readiness.ready)
        self.assertEqual(by_id["candidate_identity"].status, rp.FAIL)
        self.assertIn("candidate_identity", {c.id for c in readiness.blockers})
        payload = readiness.to_dict()
        self.assertIsNone(payload["candidate_sha"])
        self.assertEqual(payload["commit_sha"], CANDIDATE_SHA)

    def test_checked_out_commit_must_match_the_exact_candidate_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                    git=_fake_git(head=OTHER_SHA),
                )
            )
        identity = {c.id: c for c in readiness.checks}["candidate_identity"]
        self.assertEqual(identity.status, rp.FAIL)
        self.assertIn("does not match", identity.detail)
        payload = readiness.to_dict()
        self.assertEqual(payload["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(payload["commit_sha"], OTHER_SHA)

    def test_exact_candidate_identity_is_recorded_in_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                )
            )
        identity = {c.id: c for c in readiness.checks}["candidate_identity"]
        self.assertEqual(identity.status, rp.PASS)
        evidence = rp.sanitized_evidence(readiness)
        self.assertEqual(evidence["candidate_sha"], CANDIDATE_SHA)
        self.assertEqual(evidence["commit_sha"], CANDIDATE_SHA)
        self.assertEqual(
            evidence["release_identity"]["application_version"], readiness.version
        )
        self.assertEqual(evidence["release_identity"]["build_id"], CANDIDATE_SHA)
        self.assertTrue(evidence["qualification_required"])


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
            generated = root / "opai" / "_generated_release.py"
            generated.write_text(
                generated.read_text(encoding="utf-8").replace(
                    'APPLICATION_VERSION = "0.2.0a2"',
                    'APPLICATION_VERSION = "9.9.9"',
                ),
                encoding="utf-8",
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

    def test_strict_unknown_tag_state_is_infrastructure_blocked(self):
        def broken_git(_root, args):
            if list(args)[:1] == ["tag"]:
                return subprocess.CompletedProcess(args, 2, "", "tag lookup failed")
            return _fake_git()(_root, args)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                    git=broken_git,
                )
            )

        release_tag = {check.id: check for check in readiness.checks}["release_tag"]
        self.assertEqual(release_tag.status, rp.FAIL)
        self.assertTrue(release_tag.blocking)
        self.assertEqual(readiness.verdict, "infrastructure_blocked")
        self.assertEqual(readiness.reason, "release_tag_state_unknown")

    def test_source_scope_does_not_treat_a_branch_ref_name_as_the_release_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            with mock.patch.dict(
                rp.os.environ,
                {
                    "GITHUB_REF_NAME": "release/0.2",
                    "GITHUB_REF_TYPE": "branch",
                },
                clear=True,
            ):
                result = rp.check_tag_is_new(
                    rp.ReleaseContext(root=root, source_only=True, git=_fake_git())
                )

        self.assertEqual(result.status, rp.PASS)
        self.assertEqual(result.evidence["tag"], "v0.2.0a2")

    def test_failed_test_gate_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            blockers = self._blockers(
                root,
                run_tests=True,
                tests=lambda _root, _candidate: (False, "3 failures"),
            )
        self.assertIn("tests", blockers)

    def test_passing_test_gate_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    run_tests=True,
                    tests=lambda _root, _candidate: (True, "GREEN"),
                )
            )
        self.assertTrue(readiness.ready)
        self.assertEqual({c.id: c.status for c in readiness.checks}["tests"], rp.PASS)


class TypedTestGateTests(unittest.TestCase):
    def test_default_gate_passes_candidate_and_preserves_ci_local_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(command, **_kwargs):
                self.assertIn("--profile", command)
                self.assertEqual(command[command.index("--profile") + 1], "full")
                self.assertEqual(
                    command[command.index("--candidate-sha") + 1], CANDIDATE_SHA
                )
                manifest = Path(command[command.index("--manifest") + 1])
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "profile": {"name": "full", "component": "all"},
                            "candidate_sha": CANDIDATE_SHA,
                            "commit_sha": CANDIDATE_SHA,
                            "candidate": {
                                "expected_sha": CANDIDATE_SHA,
                                "checked_out_sha": CANDIDATE_SHA,
                                "promotable": True,
                            },
                            "verdict": "security_failed",
                            "reason": "required_security_failed",
                            "classification": "security",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 1, "", "")

            with mock.patch.object(rp.subprocess, "run", side_effect=fake_run):
                result = rp._default_tests(root, CANDIDATE_SHA)

        self.assertEqual(result.verdict, "security_failed")
        self.assertEqual(result.reason, "required_security_failed")
        self.assertEqual(result.classification, "security")
        self.assertEqual(result.candidate_sha, CANDIDATE_SHA)
        self.assertEqual(result.profile, "full")

    def test_default_gate_rejects_a_qualified_result_for_another_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(command, **_kwargs):
                manifest = Path(command[command.index("--manifest") + 1])
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "profile": {"name": "full", "component": "all"},
                            "candidate_sha": CANDIDATE_SHA,
                            "commit_sha": OTHER_SHA,
                            "candidate": {
                                "expected_sha": CANDIDATE_SHA,
                                "checked_out_sha": OTHER_SHA,
                                "promotable": True,
                            },
                            "verdict": "qualified",
                            "reason": "all_required_checks_passed",
                            "classification": "none",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(rp.subprocess, "run", side_effect=fake_run):
                result = rp._default_tests(root, CANDIDATE_SHA)

        self.assertEqual(result.verdict, "infrastructure_blocked")
        self.assertEqual(result.reason, "test_evidence_invalid")

    def test_default_gate_rejects_unknown_terminal_status_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(command, **_kwargs):
                manifest = Path(command[command.index("--manifest") + 1])
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "profile": {"name": "full", "component": "all"},
                            "candidate_sha": CANDIDATE_SHA,
                            "commit_sha": CANDIDATE_SHA,
                            "candidate": {
                                "expected_sha": CANDIDATE_SHA,
                                "checked_out_sha": CANDIDATE_SHA,
                                "promotable": True,
                            },
                            "verdict": "looks_good",
                            "reason": "trust_me",
                            "classification": "probably_fine",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 1, "", "")

            with mock.patch.object(rp.subprocess, "run", side_effect=fake_run):
                result = rp._default_tests(root, CANDIDATE_SHA)

        self.assertEqual(result.verdict, "infrastructure_blocked")
        self.assertEqual(result.reason, "test_evidence_invalid")

    def test_source_scope_preserves_typed_failure_and_leaves_artifacts_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    source_only=True,
                    candidate_sha=CANDIDATE_SHA,
                    run_tests=True,
                    tests=lambda _root, _candidate: rp.TestGateResult(
                        verdict="security_failed",
                        reason="required_security_failed",
                        classification="security",
                        detail="bandit failed",
                        candidate_sha=CANDIDATE_SHA,
                        profile="full",
                    ),
                )
            )

        artifacts = {check.id: check for check in readiness.checks}["artifacts"]
        self.assertEqual(readiness.qualification_scope, "source")
        self.assertEqual(readiness.verdict, "security_failed")
        self.assertEqual(readiness.reason, "required_security_failed")
        self.assertEqual(readiness.classification, "security")
        self.assertFalse(readiness.ready)
        self.assertFalse(readiness.final_release_ready)
        self.assertEqual(artifacts.status, rp.SKIP)
        self.assertFalse(artifacts.blocking)

    def test_successful_source_scope_is_not_final_release_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            readiness = rp.run_preflight(
                _ctx(
                    root,
                    qualification_required=True,
                    source_only=True,
                    candidate_sha=CANDIDATE_SHA,
                    run_tests=True,
                    tests=lambda _root, _candidate: rp.TestGateResult(
                        verdict="qualified",
                        reason="all_required_checks_passed",
                        classification="none",
                        detail="full profile qualified",
                        candidate_sha=CANDIDATE_SHA,
                        profile="full",
                    ),
                )
            )

        payload = readiness.to_dict()
        self.assertTrue(readiness.ready)
        self.assertEqual(payload["verdict"], "qualified")
        self.assertEqual(payload["qualification_scope"], "source")
        self.assertFalse(payload["final_release_ready"])
        self.assertEqual(payload["artifact_qualification"], "pending")


class ArtifactChecksumTests(unittest.TestCase):
    @staticmethod
    def _trusted_verifier(_artifact, _log, report, _ctx):
        """Test double for a platform-native/cryptographic verifier."""

        return (
            report.get("verified") is True
            and report.get("signature_verified") is True
            and report.get("attestation_verified") is True,
            "test-native-and-attestation-verifier",
        )

    def _final_ctx(self, root: Path, **kwargs):
        kwargs.setdefault("repository", REPOSITORY)
        kwargs.setdefault("workflow", DESKTOP_WORKFLOW)
        kwargs.setdefault("run_id", RUN_ID)
        kwargs.setdefault("run_attempt", RUN_ATTEMPT)
        kwargs.setdefault("release_tag", RELEASE_TAG)
        kwargs.setdefault("git", _fake_git(tags=[RELEASE_TAG]))
        return _ctx(root, **kwargs)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> str:
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        return rp.sha256_of(path)

    def _manifest(
        self,
        tmp: Path,
        *,
        candidate_sha=CANDIDATE_SHA,
        commit_sha=CANDIDATE_SHA,
        provenance_sha=CANDIDATE_SHA,
        signature_sha=CANDIDATE_SHA,
        signed=True,
        corrupt=False,
        missing=False,
        structured=True,
        native_artifact_sha=None,
        release_tag=RELEASE_TAG,
    ):
        if not structured:
            art = tmp / "OPai-setup.exe"
            art.write_bytes(b"artifact-bytes")
            digest = rp.sha256_of(art)
            manifest = tmp / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "candidate_sha": candidate_sha,
                        "commit_sha": commit_sha,
                        "artifacts": [
                            {
                                "path": art.name,
                                "sha256": digest,
                                "signed": signed,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return manifest

        common = {
            "schema_version": 1,
            "repository": REPOSITORY,
            "workflow": DESKTOP_WORKFLOW,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "tag": release_tag,
        }
        provider_path = tmp / "provider-qualification.json"
        provider_digest = self._write_json(
            provider_path,
            {
                **common,
                "kind": "opai_provider_qualification",
                "candidate_sha": candidate_sha,
                "verdict": "qualified",
            },
        )

        artifacts: list[dict] = []
        native_evidence: list[dict] = []
        for index, platform_name in enumerate(("windows-latest", "macos-latest")):
            artifact = tmp / f"OPai-{platform_name}.zip"
            artifact.write_bytes(f"artifact-bytes-{platform_name}".encode())
            actual_digest = rp.sha256_of(artifact)
            declared_digest = "0" * 64 if corrupt and index == 0 else actual_digest

            native_path = tmp / f"native-{platform_name}.json"
            native_digest = self._write_json(
                native_path,
                {
                    **common,
                    "kind": "opai_native_qualification",
                    "candidate_sha": provenance_sha,
                    "platform": platform_name,
                    "artifact_sha256": (
                        native_artifact_sha
                        if native_artifact_sha is not None and index == 0
                        else declared_digest
                    ),
                    "verdict": "qualified",
                },
            )
            native_evidence.append(
                {
                    "platform": platform_name,
                    "path": native_path.name,
                    "sha256": native_digest,
                }
            )

            log_path = tmp / f"verification-{platform_name}.log"
            log_path.write_text(
                f"native signature and GitHub attestation verified for {platform_name}\n",
                encoding="utf-8",
            )
            log_digest = rp.sha256_of(log_path)
            report_path = tmp / f"attestation-{platform_name}.json"
            report_digest = self._write_json(
                report_path,
                {
                    **common,
                    "kind": "opai_artifact_verification",
                    "candidate_sha": signature_sha,
                    "platform": platform_name,
                    "artifact_sha256": declared_digest,
                    "verification_log_sha256": log_digest,
                    "native_evidence_sha256": native_digest,
                    "provider_evidence_sha256": provider_digest,
                    "verifier": "gh-attestation+native-signature",
                    "verified": signed,
                    "signature_verified": signed,
                    "attestation_verified": signed,
                },
            )
            artifacts.append(
                {
                    "path": artifact.name,
                    "sha256": declared_digest,
                    "platform": platform_name,
                    "verification_log": {
                        "path": log_path.name,
                        "sha256": log_digest,
                    },
                    "attestation_report": {
                        "path": report_path.name,
                        "sha256": report_digest,
                    },
                }
            )
            if missing and index == 0:
                artifact.unlink()

        manifest = tmp / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "repository": REPOSITORY,
                    "workflow": DESKTOP_WORKFLOW,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "tag": release_tag,
                    "candidate_sha": candidate_sha,
                    "commit_sha": commit_sha,
                    "artifacts": artifacts,
                    "native_evidence": native_evidence,
                    "provider_evidence": {
                        "path": provider_path.name,
                        "sha256": provider_digest,
                    },
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_structural_reports_without_an_authenticated_verifier_stay_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            result = rp.check_artifacts(
                self._final_ctx(
                    root,
                    artifacts_manifest=manifest,
                    candidate_sha=CANDIDATE_SHA,
                )
            )

        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("authenticated artifact verifier is unavailable", result.detail)

    def test_standalone_final_qualification_has_typed_blocked_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            readiness = rp.run_preflight(
                self._final_ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                    run_tests=True,
                    tests=lambda _root, _candidate: rp.TestGateResult(
                        verdict="qualified",
                        reason="all_required_checks_passed",
                        classification="none",
                        detail="all required tests passed",
                        candidate_sha=CANDIDATE_SHA,
                        profile="full",
                    ),
                    artifacts_manifest=manifest,
                )
            )

        self.assertFalse(readiness.ready)
        self.assertFalse(readiness.final_release_ready)
        self.assertEqual(readiness.verdict, "infrastructure_blocked")
        self.assertEqual(readiness.reason, "artifact_verifier_unavailable")
        self.assertEqual(readiness.classification, "infrastructure")

    def test_authenticated_same_run_inventory_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            result = rp.check_artifacts(
                self._final_ctx(
                    root,
                    artifacts_manifest=manifest,
                    candidate_sha=CANDIDATE_SHA,
                    artifact_verifier=self._trusted_verifier,
                )
            )

        self.assertEqual(result.status, rp.PASS, result.detail)

    def test_final_qualification_rejects_noncanonical_tag_for_package_version(self):
        wrong_tag = "v9.9.9"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, release_tag=wrong_tag)
            readiness = rp.run_preflight(
                self._final_ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                    release_tag=wrong_tag,
                    git=_fake_git(tags=[wrong_tag]),
                    run_tests=True,
                    tests=lambda _root, _candidate: rp.TestGateResult(
                        verdict="qualified",
                        reason="all_required_checks_passed",
                        classification="none",
                        detail="all required tests passed",
                        candidate_sha=CANDIDATE_SHA,
                        profile="full",
                    ),
                    artifacts_manifest=manifest,
                    artifact_verifier=self._trusted_verifier,
                )
            )

        release_tag = next(
            check for check in readiness.checks if check.id == "release_tag"
        )
        self.assertEqual(release_tag.status, rp.FAIL)
        self.assertTrue(release_tag.blocking)
        self.assertIn("canonical tag v0.2.0a2", release_tag.detail)
        self.assertFalse(readiness.final_release_ready)

    def test_final_inventory_requires_windows_macos_native_and_provider_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["artifacts"] = [
                item
                for item in value["artifacts"]
                if item["platform"] != "macos-latest"
            ]
            value["native_evidence"] = [
                item
                for item in value["native_evidence"]
                if item["platform"] != "macos-latest"
            ]
            manifest.write_text(json.dumps(value), encoding="utf-8")
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )

        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("missing artifact platform: macos-latest", result.detail)
        self.assertIn("missing native evidence platform: macos-latest", result.detail)

    def test_verification_log_must_exist_and_match_its_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            log = root / value["artifacts"][0]["verification_log"]["path"]
            log.write_text("forged after verification\n", encoding="utf-8")
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )

        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("verification log checksum mismatch", result.detail)

    def test_inline_signature_metadata_is_not_trusted_evidence(self):
        """A dummy plus self-authored booleans must never become release-ready."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, structured=False)
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )

        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("verification log", result.detail)
        self.assertIn("attestation", result.detail)

    def test_exact_candidate_with_tests_and_artifacts_is_qualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root)
            readiness = rp.run_preflight(
                self._final_ctx(
                    root,
                    qualification_required=True,
                    candidate_sha=CANDIDATE_SHA,
                    run_tests=True,
                    tests=lambda _root, _candidate: rp.TestGateResult(
                        verdict="qualified",
                        reason="all_required_checks_passed",
                        classification="none",
                        detail="all required tests passed",
                        candidate_sha=CANDIDATE_SHA,
                        profile="full",
                    ),
                    artifacts_manifest=manifest,
                    artifact_verifier=self._trusted_verifier,
                )
            )
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.blockers, ())
        self.assertEqual(
            {check.status for check in readiness.checks},
            {rp.PASS},
        )

    def test_checksum_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, corrupt=True)
            blockers = {
                c.id
                for c in rp.run_preflight(
                    self._final_ctx(root, artifacts_manifest=manifest)
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
                    self._final_ctx(root, artifacts_manifest=manifest)
                ).blockers
            }
        self.assertIn("artifacts", blockers)

    def test_bare_signed_boolean_is_not_signature_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, structured=False, signed=True)
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )
        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("verification log", result.detail)
        self.assertIn("attestation report", result.detail)

    def test_stale_manifest_candidate_cannot_qualify_a_new_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(
                root, candidate_sha=OTHER_SHA, commit_sha=OTHER_SHA
            )
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )
        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("manifest candidate", result.detail)

    def test_artifact_provenance_must_name_the_source_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, provenance_sha=OTHER_SHA)
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )
        self.assertEqual(result.status, rp.FAIL)
        self.assertIn(
            "native evidence windows-latest candidate mismatch", result.detail
        )

    def test_native_evidence_must_name_the_platform_artifact_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, native_artifact_sha="f" * 64)
            result = rp.check_artifacts(
                self._final_ctx(
                    root,
                    artifacts_manifest=manifest,
                    candidate_sha=CANDIDATE_SHA,
                    artifact_verifier=self._trusted_verifier,
                )
            )

        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("native evidence artifact mismatch", result.detail)

    def test_signature_verification_must_be_bound_to_artifact_and_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, signature_sha=OTHER_SHA)
            result = rp.check_artifacts(
                self._final_ctx(
                    root, artifacts_manifest=manifest, candidate_sha=CANDIDATE_SHA
                )
            )
        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("attestation candidate mismatch", result.detail)

    def test_artifact_path_cannot_escape_the_manifest_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            outside = root.parent / "outside-release-artifact.bin"
            outside.write_bytes(b"outside")
            try:
                manifest = root / "manifest.json"
                digest = rp.sha256_of(outside)
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "candidate_sha": CANDIDATE_SHA,
                            "commit_sha": CANDIDATE_SHA,
                            "artifacts": [
                                {
                                    "path": f"../{outside.name}",
                                    "sha256": digest,
                                    "provenance": {
                                        "schema_version": 1,
                                        "source_candidate_sha": CANDIDATE_SHA,
                                        "artifact_sha256": digest,
                                        "builder": "test",
                                        "platform": "windows-latest",
                                    },
                                    "signature_verification": {
                                        "schema_version": 1,
                                        "verified": True,
                                        "source_candidate_sha": CANDIDATE_SHA,
                                        "artifact_sha256": digest,
                                        "tool": "test-verifier",
                                        "log_sha256": "b" * 64,
                                    },
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                result = rp.check_artifacts(
                    self._final_ctx(
                        root,
                        artifacts_manifest=manifest,
                        candidate_sha=CANDIDATE_SHA,
                    )
                )
            finally:
                outside.unlink(missing_ok=True)
        self.assertEqual(result.status, rp.FAIL)
        self.assertIn("escapes manifest directory", result.detail)

    def test_missing_artifact_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_release_repo(root)
            manifest = self._manifest(root, missing=True)
            blockers = {
                c.id
                for c in rp.run_preflight(
                    self._final_ctx(root, artifacts_manifest=manifest)
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
    """The `vesta release` CLI is the one documented command (#32 AC)."""

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
