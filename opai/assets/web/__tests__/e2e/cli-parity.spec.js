import { test, expect } from "@playwright/test";

import { openApp } from "./helpers/app.js";
import { pythonCanImport, runCli } from "./helpers/cli.js";


test("opai help exposes the primary product surfaces", () => {
  const result = runCli(["--help"]);
  expect(result.status).toBe(0);
  expect(result.stdout).toContain("ask");
  expect(result.stdout).toContain("gui");
  expect(result.stdout).toContain("models");
  expect(result.stdout).toContain("savings");
});

test("ask help supports the model, mode, and JSON parity flags", () => {
  const result = runCli(["ask", "--help"]);
  expect(result.status).toBe(0);
  expect(result.stdout).toContain("--model");
  expect(result.stdout).toContain("--mode");
  expect(result.stdout).toContain("--json");
});

test("headless GUI state is machine-readable and Safe Auto by default", () => {
  test.fail(
    !pythonCanImport("yaml"),
    "BUG-QA-011: clean Python without PyYAML parses YAML registries as JSON and crashes",
  );
  const result = runCli(["gui", "--once", "--project", "<project>"]);
  expect(
    result.status,
    `headless GUI command failed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
  ).toBe(0);
  const payload = JSON.parse(result.stdout);
  expect(payload.effective_mode || payload.mode).toBe("safe-auto");
  expect(payload).toHaveProperty("available_models");
});

test("CLI output never leaks unrelated environment secrets", () => {
  const secret = "sk-opai-e2e-never-print-this";
  const result = runCli(["gui", "--once", "--project", "<project>"], { env: { OPAI_TEST_SECRET: secret } });
  expect(result.stdout).not.toContain(secret);
  expect(result.stderr).not.toContain(secret);
});

test("Inspector CLI mirror only emits flags supported by opai ask", async ({ page }) => {
  await openApp(page);
  await page.selectOption("#modelSel", "account:codex:gpt-5.5");
  await page.selectOption("#modeSel", "plan");
  const mirror = await page.locator("#cliMirrorCmd").innerText();
  const help = runCli(["ask", "--help"]).stdout;
  expect(mirror).toContain("opai ask");
  expect(help).toContain("--model");
  expect(help).toContain("--mode");
});
