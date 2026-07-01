import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";


export function runCli(args, options = {}) {
  const home = mkdtempSync(join(tmpdir(), "opai-e2e-home-"));
  const project = mkdtempSync(join(tmpdir(), "opai-e2e-project-"));
  writeFileSync(join(project, "pyproject.toml"), "[project]\nname='qa-fixture'\nversion='0.0.0'\n");
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    OPAI_NO_SUPERPOWERS: "1",
    ...options.env,
  };
  try {
    const result = spawnSync(options.python || "python", ["-m", "opai", ...args.map((arg) => arg === "<project>" ? project : arg)], {
      cwd: process.cwd(),
      env,
      encoding: "utf8",
      timeout: 30000,
    });
    return { ...result, stdout: result.stdout || "", stderr: result.stderr || "", project };
  } finally {
    rmSync(home, { recursive: true, force: true });
    rmSync(project, { recursive: true, force: true });
  }
}
