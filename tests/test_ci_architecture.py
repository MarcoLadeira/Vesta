from __future__ import annotations

import json
import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    value = yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    if not isinstance(value, dict):
        raise AssertionError(f"{name} is not a workflow mapping")
    return value


def _manifest() -> dict:
    return json.loads(
        (ROOT / ".github" / "required-checks.json").read_text(encoding="utf-8")
    )


def _ci_local():
    path = ROOT / "scripts" / "ci_local.py"
    spec = importlib.util.spec_from_file_location("opai_ci_architecture_local", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load ci_local.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CiArchitectureContractTests(unittest.TestCase):
    def test_required_check_manifest_is_versioned_structured_and_unique(self) -> None:
        manifest = _manifest()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            manifest["protected_branches"], ["main", "release/**", "rc/**"]
        )
        checks = manifest["required_checks"]
        names = [check["name"] for check in checks]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(checks), 3)
        for check in checks:
            self.assertEqual(check["workflow"], "OPai CI (hosted)")
            self.assertEqual(check["trusted_app"], "github-actions")
            self.assertTrue(check["job"])
            self.assertTrue(check["component"])
            self.assertIn("candidate-sha", check["required_check_ids"])

    def test_manifest_required_inventory_exactly_matches_local_components(self) -> None:
        ci = _ci_local()
        contracts = _manifest()["component_contracts"]
        declared_components = {
            component
            for components in ci.PROFILE_COMPONENT_STEPS.values()
            for component in components
        }
        self.assertEqual(set(contracts), declared_components)
        for component in declared_components:
            profile = next(
                name
                for name, components in ci.PROFILE_COMPONENT_STEPS.items()
                if component in components
            )
            actual = [
                step.name for step in ci.PROFILE_COMPONENT_STEPS[profile][component]
            ]
            self.assertEqual(contracts[component], actual, component)

        for required in _manifest()["required_checks"]:
            component = required["component"]
            expected = ["candidate-sha", *contracts[component]]
            self.assertEqual(required["required_check_ids"], expected, component)

    def test_manifest_profile_topology_matches_workflow_triggers(self) -> None:
        profiles = _manifest()["profiles"]
        self.assertEqual(
            profiles["fast"]["automatic"],
            [
                "pull_request:main",
                "pull_request:release/**",
                "pull_request:rc/**",
                "merge_group",
                "push:main",
                "push:release/**",
                "push:rc/**",
                "schedule:17 3 * * 1",
            ],
        )
        self.assertEqual(
            profiles["full"],
            {
                "components": ["python", "hostile", "web", "supply-chain"],
                "automatic": ["schedule:17 3 * * 1"],
                "manual": ["workflow_dispatch:full"],
            },
        )
        self.assertEqual(
            profiles["native"],
            {
                "components": ["native"],
                "automatic": ["schedule:17 3 * * 1"],
                "manual": ["workflow_dispatch:full"],
            },
        )
        self.assertEqual(
            profiles["provider-canary"]["automatic"],
            ["schedule:43 4 * * 2"],
        )
        release = profiles["release"]
        self.assertEqual(release["automatic"], [])
        self.assertEqual(
            release["source_preflight"]["automatic"],
            ["push:release/**", "push:rc/**"],
        )
        self.assertEqual(
            release["unsigned_artifact_rehearsal"]["automatic"],
            ["push:tag:v*"],
        )
        self.assertEqual(release["final_artifact_qualification"]["automatic"], [])
        self.assertEqual(
            release["final_artifact_qualification"]["protected_environment"],
            "opai-production-signing",
        )

    def test_hosted_required_jobs_run_exact_components_for_exact_candidate(
        self,
    ) -> None:
        manifest = _manifest()
        workflow = _workflow("ci.yml")
        raw = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        triggers = workflow["on"]
        self.assertIn("pull_request", triggers)
        self.assertIn("merge_group", triggers)
        self.assertIn("main", triggers["pull_request"]["branches"])
        self.assertIn("release/**", triggers["pull_request"]["branches"])
        self.assertIn("rc/**", triggers["pull_request"]["branches"])
        self.assertIn("main", triggers["push"]["branches"])
        self.assertIn("release/**", triggers["push"]["branches"])
        self.assertIn("rc/**", triggers["push"]["branches"])
        self.assertNotIn("pull_request_target", triggers)

        exact_ref = "github.sha"
        source_ref = "github.event.pull_request.head.sha || github.sha"
        for required in manifest["required_checks"]:
            job = workflow["jobs"][required["job"]]
            self.assertEqual(job["name"], required["job_name_template"])
            checkouts = [
                step
                for step in job["steps"]
                if str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            self.assertEqual(len(checkouts), 1)
            self.assertEqual(checkouts[0]["with"]["ref"], "${{ github.sha }}")
            self.assertEqual(checkouts[0]["with"]["persist-credentials"], "false")
            command = "\n".join(str(step.get("run", "")) for step in job["steps"])
            self.assertIn(f"--component {required['component']}", command)
            self.assertIn("--candidate-sha", command)
            self.assertIn(exact_ref, command)
            self.assertIn("--source-sha", command)
            self.assertIn(source_ref, command)

        hostile = workflow["jobs"]["mandatory-hostile-environment"]
        self.assertGreaterEqual(int(hostile["timeout-minutes"]), 60)

        self.assertNotIn("continue-on-error", raw)
        self.assertNotRegex(raw, r"\|\|\s*true")

    def test_all_actions_are_immutable_and_workflow_tokens_are_least_privilege(
        self,
    ) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            value = _workflow(path.name)
            permissions = value.get("permissions", {})
            self.assertNotEqual(permissions, "write-all", path.name)
            for forbidden in ("actions", "checks", "pull-requests"):
                self.assertNotEqual(permissions.get(forbidden), "write", path.name)
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("uses:"):
                    action = stripped.split(":", 1)[1].strip().split(" #", 1)[0]
                    self.assertRegex(
                        action,
                        PINNED_ACTION,
                        f"mutable action in {path.name}: {action}",
                    )

    def test_privileged_workflows_cannot_execute_pull_request_code(self) -> None:
        self_hosted = _workflow("ci-selfhosted.yml")
        self.assertNotIn("pull_request", self_hosted["on"])
        self.assertEqual(self_hosted["permissions"]["actions"], "read")
        health = self_hosted["jobs"]["runner-health"]
        self.assertEqual(health["needs"], "dispatch-guard")
        self.assertEqual(health["runs-on"], "ubuntu-latest")
        self.assertEqual(health["environment"]["name"], "opai-runner-health")
        self.assertEqual(health["environment"]["deployment"], "false")
        health_source = str(health)
        self.assertIn("secrets.OPAI_RUNNER_HEALTH_TOKEN", health_source)
        self.assertNotIn("github.token", health_source)
        trusted = self_hosted["jobs"]["trusted-gate"]
        self.assertEqual(trusted["needs"], "runner-health")
        self.assertIn("github.ref == 'refs/heads/main'", trusted["if"])
        self.assertEqual(trusted["runs-on"], ["self-hosted", "Windows", "X64"])
        checkout = next(
            step
            for step in trusted["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertIn(
            ".venv/",
            (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
        )
        self_hosted_guard = "\n".join(
            str(step.get("run", ""))
            for step in self_hosted["jobs"]["dispatch-guard"]["steps"]
        )
        self.assertIn("REQUESTED_REF", self_hosted_guard)
        self.assertIn("refs/heads/main", self_hosted_guard)
        self.assertIn("exit 2", self_hosted_guard)
        for step in trusted["steps"]:
            if step.get("run"):
                self.assertEqual(step.get("shell"), "pwsh")

        provider = _workflow("provider-canary.yml")
        self.assertNotIn("pull_request", provider["on"])
        self.assertNotIn("push", provider["on"])
        self.assertIn("schedule", provider["on"])
        job = provider["jobs"]["provider-canary"]
        self.assertEqual(job["needs"], "dispatch-guard")
        self.assertEqual(job["environment"]["name"], "opai-provider-canary")
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", str(job.get("env", {})))
        canary_step = next(
            step for step in job["steps"] if "ci_local.py" in str(step.get("run", ""))
        )
        self.assertIn("secrets.MOONSHOT_API_KEY", str(canary_step.get("env", {})))
        provider_guard = "\n".join(
            str(step.get("run", ""))
            for step in provider["jobs"]["dispatch-guard"]["steps"]
        )
        self.assertIn("REQUESTED_REF", provider_guard)
        self.assertIn("refs/heads/main", provider_guard)
        self.assertIn("exit 2", provider_guard)

    def test_native_and_release_prerequisites_are_ordered_before_privilege(
        self,
    ) -> None:
        hosted = _workflow("ci.yml")
        native = hosted["jobs"]["scheduled-native"]
        native_commands = "\n".join(
            str(step.get("run", "")) for step in native["steps"]
        )
        self.assertLess(
            native_commands.index("pip install -r requirements-ci.txt"),
            native_commands.index("--component native"),
        )

        desktop = _workflow("desktop-artifacts.yml")
        provider = desktop["jobs"]["provider-qualification"]
        self.assertIn("build", provider["needs"])
        self.assertNotIn("secrets.", str(provider.get("env", {})))
        canary_step = next(
            step
            for step in provider["steps"]
            if "ci_local.py" in str(step.get("run", ""))
        )
        self.assertIn("secrets.MOONSHOT_API_KEY", str(canary_step.get("env", {})))

        raw = (WORKFLOWS / "desktop-artifacts.yml").read_text(encoding="utf-8")
        self.assertNotIn("git fetch --tags --force", raw)
        self.assertIn("$EVIDENCE_DIR/release-python-evidence.json", raw)
        self.assertIn("${{ env.EVIDENCE_DIR }}", raw)

    def test_release_and_failure_artifacts_are_rerun_safe_and_bounded(self) -> None:
        for name in ("desktop-artifacts.yml", "release-preflight.yml", "ci.yml"):
            raw = (WORKFLOWS / name).read_text(encoding="utf-8")
            for match in re.finditer(r"(?m)^\s+name:\s+([^\n]+run_id[^\n]*)$", raw):
                self.assertIn(
                    "run_attempt",
                    match.group(1),
                    f"artifact is not rerun safe in {name}",
                )
        desktop = (WORKFLOWS / "desktop-artifacts.yml").read_text(encoding="utf-8")
        self.assertIn("tags:", desktop)
        self.assertIn('"v*"', desktop)
        self.assertIn("github.run_attempt", desktop)
        preflight = (WORKFLOWS / "release-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("--qualification-required", preflight)
        self.assertIn("--candidate-sha", preflight)
        self.assertNotIn("if-no-files-found: warn", preflight)

    def test_source_preflight_does_not_claim_final_artifact_qualification(self) -> None:
        workflow = _workflow("release-preflight.yml")
        triggers = workflow["on"]
        self.assertNotIn("tags", triggers["push"])
        self.assertEqual(set(triggers["push"]["branches"]), {"release/**", "rc/**"})
        source = (WORKFLOWS / "release-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("--source-only", source)
        self.assertIn("--run-tests", source)
        self.assertIn("--candidate-sha", source)
        self.assertNotIn("--artifacts", source)
        self.assertNotIn("artifact-manifest.json", source)

    def test_every_uploaded_artifact_is_immutable_required_and_bounded(self) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            workflow = _workflow(path.name)
            for job_name, job in workflow["jobs"].items():
                for step in job.get("steps", []):
                    if not str(step.get("uses", "")).startswith(
                        "actions/upload-artifact@"
                    ):
                        continue
                    options = step.get("with", {})
                    artifact_name = str(options.get("name", ""))
                    self.assertIn(
                        "github.run_id", artifact_name, f"{path.name}:{job_name}"
                    )
                    self.assertIn(
                        "github.run_attempt", artifact_name, f"{path.name}:{job_name}"
                    )
                    self.assertEqual(
                        options.get("if-no-files-found"),
                        "error",
                        f"{path.name}:{job_name}",
                    )
                    self.assertLessEqual(int(options.get("retention-days", "999")), 30)

    def test_release_signing_depends_on_same_run_source_web_native_and_provider(
        self,
    ) -> None:
        workflow = _workflow("desktop-artifacts.yml")
        self.assertIn("push", workflow["on"])
        self.assertIn("v*", workflow["on"]["push"]["tags"])
        sign_needs = set(workflow["jobs"]["sign"]["needs"])
        self.assertEqual(
            sign_needs,
            {
                "build",
                "source-qualification",
                "web-qualification",
                "provider-qualification",
            },
        )
        provider = workflow["jobs"]["provider-qualification"]
        self.assertEqual(provider["environment"]["name"], "opai-provider-canary")
        self.assertIn("refs/heads/main", provider["if"])
        source_commands = "\n".join(
            str(step.get("run", ""))
            for step in workflow["jobs"]["source-qualification"]["steps"]
        )
        self.assertIn("git merge-base --is-ancestor", source_commands)
        self.assertIn("--candidate-sha", source_commands)
        build_commands = "\n".join(
            str(step.get("run", "")) for step in workflow["jobs"]["build"]["steps"]
        )
        self.assertIn("EXPECTED_CANDIDATE_SHA", build_commands)

    def test_untrusted_pr_workflow_has_no_secrets_environment_or_self_hosted_runner(
        self,
    ) -> None:
        workflow = _workflow("ci.yml")
        raw = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", raw)
        for job in workflow["jobs"].values():
            self.assertNotIn("environment", job)
            self.assertNotIn("self-hosted", str(job.get("runs-on", "")))

    def test_browser_failures_have_no_retry_or_raw_trace_upload(self) -> None:
        playwright = (ROOT / "playwright.config.js").read_text(encoding="utf-8")
        self.assertIn("retries: 0", playwright)
        hosted = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("playwright-report/", hosted)
        self.assertNotIn("test-results/", hosted)
        self.assertIn("ci-web-evidence.json", hosted)


if __name__ == "__main__":
    unittest.main()


class ExactShaCheckoutDepthTests(unittest.TestCase):
    """The exact-SHA parent check needs real parents to verify (#621).

    Found the first time hosted CI actually executed, after the repository was
    made public and the billing block lifted: every required job failed with
    `candidate_source_mismatch` before running a single test.

    `actions/checkout` defaults to `fetch-depth: 1`. Git truncates parent
    information at a shallow boundary, so `git show -s --format=%P HEAD`
    returns nothing, the candidate's parent set is empty, and a PR head can
    never be found in it. The check was structurally unpassable on hosted pull
    requests -- and that was invisible for as long as the jobs never started.
    """

    def _workflow(self) -> str:
        return (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

    def test_every_exact_sha_checkout_fetches_its_parents(self):
        import re

        source = self._workflow()
        blocks = re.findall(
            r"uses: actions/checkout@[0-9a-f]{40}\n(?:\s+.*\n)+?(?=\s*-\s|\Z)", source
        )
        self.assertTrue(blocks, "no checkout steps found -- the scan is broken")
        shallow = [
            b
            for b in blocks
            if "ref: ${{ github.sha }}" in b and "fetch-depth" not in b
        ]
        self.assertEqual(
            shallow,
            [],
            "these checkouts pin an exact SHA but keep the default shallow "
            "depth, so the candidate-sha parent check cannot verify a PR head:\n"
            + "\n".join(shallow),
        )

    def test_the_depth_is_at_least_two(self):
        import re

        for depth in re.findall(r"fetch-depth:\s*(\d+)", self._workflow()):
            with self.subTest(depth=depth):
                self.assertGreaterEqual(
                    int(depth), 2, "depth 1 truncates parents; the check needs them"
                )
