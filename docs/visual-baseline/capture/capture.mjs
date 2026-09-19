// Visual baseline capture for the Vesta web GUI (re-capture against current main).
//
// Serves the worktree root over a local static server, boots the real web UI
// (vesta/assets/web/index.html) in Chromium WITHOUT Qt by injecting the same
// mock bridge the e2e suite uses (vesta/assets/web/__tests__/e2e/mock-bridge.js),
// then drives UI states exactly the way the specs do and records screenshots
// (PNG) and short workflow recordings (.webm).
//
// Run from the worktree root:  node docs/visual-baseline/capture/capture.mjs
// Requires: a node_modules with playwright reachable from the worktree (the
// script resolves playwright from the enclosing main checkout when the
// worktree lives at <main>/.worktrees/<name>) + Playwright chromium/ffmpeg.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..", ".."); // worktree root
const OUT = path.join(ROOT, "docs", "visual-baseline");
const PORT = 8124;
const APP_URL = `http://127.0.0.1:${PORT}/vesta/assets/web/index.html`;
const MOCK = fs.readFileSync(
  path.join(ROOT, "vesta/assets/web/__tests__/e2e/mock-bridge.js"),
  "utf8",
);
const VIEWPORT = { width: 1440, height: 900 };

// Resolve playwright: prefer the worktree, fall back to the enclosing main
// checkout (worktrees live at <main>/.worktrees/<name>).
function resolvePlaywright() {
  const candidates = [ROOT, path.resolve(ROOT, "..", "..")];
  for (const base of candidates) {
    try {
      const req = createRequire(path.join(base, "package.json"));
      return req("playwright");
    } catch { /* try next */ }
  }
  throw new Error("playwright not resolvable from worktree or main checkout");
}
const { chromium } = resolvePlaywright();

const COMMIT = execSync("git rev-parse HEAD", { cwd: ROOT }).toString().trim();

const {
  fullScenario,
  DISCONNECTED_ACCOUNTS,
} = await import(
  pathToFileURL(
    path.join(ROOT, "vesta/assets/web/__tests__/e2e/helpers/fixtures.js"),
  ).href
);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".ico": "image/x-icon",
};

function startServer() {
  const server = http.createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
    const filePath = path.join(ROOT, path.normalize(urlPath));
    if (!filePath.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream" });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(PORT, "127.0.0.1", () => resolve(server)));
}

const manifest = [];
const failures = [];

async function shot(page, meta) {
  const target = path.join(OUT, meta.file);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  try {
    await page.screenshot({ path: target });
    manifest.push({
      id: meta.id, file: meta.file, type: "screenshot", area: meta.area,
      state: meta.state, trigger: meta.trigger, expected: meta.expected,
      observed: meta.observed || "captured — see image",
      sourceAreas: meta.sourceAreas, commit: COMMIT,
      capturedAt: new Date().toISOString(),
    });
    console.log("shot  ok  " + meta.id);
  } catch (error) {
    failures.push({ id: meta.id, error: String(error).slice(0, 300) });
    console.log("shot  FAIL " + meta.id + " :: " + String(error).slice(0, 160));
  }
}

let browser;
const tmpVideo = fs.mkdtempSync(path.join(os.tmpdir(), "vesta-vb-"));

async function newPage(overrides = {}, video = false) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    recordVideo: video ? { dir: tmpVideo, size: VIEWPORT } : undefined,
  });
  const page = await context.newPage();
  await page.addInitScript((scenario) => {
    window.__VESTA_TEST_SCENARIO__ = scenario;
  }, fullScenario(overrides));
  await page.addInitScript({ content: MOCK });
  await page.goto(APP_URL);
  await page.waitForSelector("#input");
  await page.waitForFunction(
    () => Boolean(window.__vesta) && document.querySelector("#modelSel")?.options.length > 0,
    null, { timeout: 20000 },
  );
  return { context, page };
}

// --- mock bridge drivers (mirroring e2e helpers/app.js) ---------------------
const sendPrompt = async (page, text) => {
  await page.fill("#input", text);
  await page.getByRole("button", { name: "Send" }).click();
  await page.waitForSelector(".gen-stop", { timeout: 8000 });
  return page.evaluate(() => window.__mock.reqId());
};
const emitToken = (page, id, text) =>
  page.evaluate(({ id, text }) => window.__mock.emitToken(id, text), { id, text });
const emitReply = (page, id, result) =>
  page.evaluate(({ id, result }) => window.__mock.emitReply(id, result), { id, result });
const emitActivity = (page, id, event) =>
  page.evaluate(({ id, event }) => window.__mock.emitActivity(id, event), { id, event });
const confirmCancel = (page, id) =>
  page.evaluate((id) => window.__mock.confirmCancel(id), id);
const emitObjective = (page, payload) =>
  page.evaluate((p) => window.__mock.emitObjective(p), payload);

async function openSettingsRail(page, id) {
  const inSettings = await page.locator("#view-settings").isVisible().catch(() => false);
  if (!inSettings) {
    await page.locator("#headerSettings").click();
    await page.waitForTimeout(400);
  }
  if (id) {
    await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
    await page
      .waitForSelector(`.settings-pane[data-pane="${id}"].active`, { timeout: 5000 })
      .catch(() => {});
  }
  await page.waitForTimeout(300);
}

async function backToChat(page) {
  if (await page.locator("#view-settings").isVisible().catch(() => false)) {
    await page.locator("#settingsBack").click();
    await page.waitForTimeout(250);
  }
}

const WEB = "vesta/assets/web";
const SRC = {
  shell: [`${WEB}/index.html`, `${WEB}/app.js`, `${WEB}/styles.css`, `${WEB}/theme.js`],
  composer: [`${WEB}/index.html`, `${WEB}/composer.js`, `${WEB}/app.js`],
  execution: [`${WEB}/app.js`, `${WEB}/activity.js`, `${WEB}/message-state.js`, `${WEB}/run-result.js`],
  settings: [`${WEB}/settings.js`, `${WEB}/styles.css`],
  providers: [`${WEB}/settings.js`],
  changes: [`${WEB}/app.js`, `${WEB}/run-result.js`],
  errors: [`${WEB}/app.js`, `${WEB}/run-result.js`],
  empty: [`${WEB}/app.js`, `${WEB}/index.html`, `${WEB}/onboarding.js`],
  agents: [`${WEB}/agents-team.js`, `${WEB}/agents-workspace.js`, `${WEB}/app.js`],
};

// --- capture sessions --------------------------------------------------------

async function sessionShell() {
  const { context, page } = await newPage();
  try {
    await shot(page, {
      id: "SHELL-LAUNCH-001", file: "screens/shell/SHELL-LAUNCH-001-fresh-launch.png",
      area: "shell", state: "fresh launch, Vesta branding, empty hero, sidebar recents, provider status strip",
      trigger: "app boot with mock bridge default scenario",
      expected: "header-brand 'Vesta', hero #empty, composer, #statusLine spend line, #acct provider states, sidebar conversations",
      sourceAreas: SRC.shell,
    });
    await page.fill("#input", "Explain how Vesta routes a request to a local model first");
    await shot(page, {
      id: "COMPOSER-TYPED-001", file: "screens/composer/COMPOSER-TYPED-001-text-entered.png",
      area: "composer", state: "prompt text typed, not yet sent",
      trigger: "type into #input", expected: "text visible in composer, Send enabled",
      sourceAreas: SRC.composer,
    });
    await page.fill("#input", "");
    await page.locator("#modelBtn").click();
    await page.waitForSelector("#modelPop:not([hidden])", { timeout: 5000 });
    await shot(page, {
      id: "SHELL-MODEL-POP-002", file: "screens/shell/SHELL-MODEL-POP-002-model-selector-open.png",
      area: "shell", state: "model selector popover open",
      trigger: "click #modelBtn", expected: "model list with account/free/local/auto groups and badges",
      sourceAreas: SRC.shell,
    });
    await page.keyboard.press("Escape");
    await page.locator("#modeBtn").click();
    await page.waitForSelector("#modePop:not([hidden])", { timeout: 5000 });
    await shot(page, {
      id: "SHELL-MODE-POP-003", file: "screens/shell/SHELL-MODE-POP-003-run-mode-selector-open.png",
      area: "shell", state: "run-mode selector popover open",
      trigger: "click #modeBtn", expected: "Ask / Plan / Approve Edits / Safe Auto / Auto-Accept Edits / Full Auto",
      sourceAreas: SRC.shell,
    });
    await page.keyboard.press("Escape");
    // composer settings menu
    try {
      await page.locator("#moreBtn").click();
      await page.waitForTimeout(300);
      await shot(page, {
        id: "COMPOSER-MORE-002", file: "screens/composer/COMPOSER-MORE-002-composer-settings-menu.png",
        area: "composer", state: "composer settings menu open",
        trigger: "click #moreBtn", expected: "menu with composer/task-mode options",
        sourceAreas: SRC.composer,
      });
      await page.keyboard.press("Escape");
    } catch (e) { failures.push({ id: "COMPOSER-MORE-002", error: String(e).slice(0, 200) }); }
    // command palette
    await page.keyboard.press("Control+k");
    await page.waitForTimeout(300);
    await shot(page, {
      id: "SHELL-PALETTE-004", file: "screens/shell/SHELL-PALETTE-004-command-palette.png",
      area: "shell", state: "command palette open",
      trigger: "Ctrl+K", expected: "#palette overlay with command list",
      sourceAreas: SRC.shell,
    });
    await page.keyboard.press("Escape");
  } finally { await context.close(); }
}

async function sessionExecution() {
  const { context, page } = await newPage();
  try {
    const id = await sendPrompt(page, "Summarize my changes");
    await emitActivity(page, id, { id: id + ":connect", type: "provider_request", status: "success", title: "Connected to Claude · claude-opus", requestId: id, channel: "status" });
    await emitActivity(page, id, { id: id + ":stream", type: "streaming", status: "running", title: "Streaming response", detail: "240 chars · 00:01", requestId: id });
    for (const chunk of ["Vesta keeps every step visible. ", "This run was routed through ", "the connected Claude account ", "after the local-first checks ", "found no safe deterministic tool. "]) {
      await emitToken(page, id, chunk);
      await page.waitForTimeout(120);
    }
    await shot(page, {
      id: "EXEC-STREAMING-001", file: "screens/execution/EXEC-STREAMING-001-streaming-in-progress.png",
      area: "execution", state: "assistant response streaming, Stop control active, work log running",
      trigger: "send prompt, emit tokens + activity events without reply",
      expected: "partial markdown text, streaming status, composer shows Stop",
      sourceAreas: SRC.execution,
    });
    await emitActivity(page, id, { id: id + ":stream", type: "streaming", status: "success", title: "Response received", detail: "300 chars", requestId: id, durationMs: 1234 });
    await emitReply(page, id, { status: "answered", answer: "", receipt: { estimated_actual_usd: 0.0021, confidence: "actual" } });
    await page.waitForTimeout(500);
    await shot(page, {
      id: "EXEC-COMPLETE-002", file: "screens/execution/EXEC-COMPLETE-002-run-completed.png",
      area: "execution", state: "completed run with answer and collapsed turn summary",
      trigger: "emitReply status=answered with receipt",
      expected: "final message, turn summary row, Send restored",
      sourceAreas: SRC.execution,
    });
    try {
      await page.locator(".turn-summary:not([open]) > .ts-row .ts-verdict").first().click();
      await page.waitForTimeout(400);
      await shot(page, {
        id: "EXEC-TURN-DETAILS-003", file: "screens/execution/EXEC-TURN-DETAILS-003-turn-summary-expanded.png",
        area: "execution", state: "turn diagnostics expanded (evidence/receipt behind disclosure)",
        trigger: "click .ts-verdict on the turn summary",
        expected: "expanded diagnostics incl. cost receipt",
        sourceAreas: SRC.execution,
      });
    } catch (e) { failures.push({ id: "EXEC-TURN-DETAILS-003", error: String(e).slice(0, 200) }); }

    // stop / cancel flow
    const id2 = await sendPrompt(page, "Refactor the settings page into modules");
    await emitToken(page, id2, "Working through the settings module…");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    await shot(page, {
      id: "EXEC-STOP-PENDING-004", file: "screens/execution/EXEC-STOP-PENDING-004-stop-requested-unconfirmed.png",
      area: "execution", state: "stop requested, backend confirmation pending",
      trigger: "press Escape during streaming, before confirmCancel",
      expected: "intermediate stopping presentation (waits for backend confirm)",
      sourceAreas: SRC.execution,
    });
    await confirmCancel(page, id2);
    await page.waitForSelector(".stopped-card", { timeout: 5000 });
    await page.waitForTimeout(300);
    await shot(page, {
      id: "EXEC-STOPPED-005", file: "screens/execution/EXEC-STOPPED-005-stopped-card.png",
      area: "execution", state: "cancellation confirmed, stopped card shown",
      trigger: "confirmCancel after Escape",
      expected: ".stopped-card terminal presentation with edit/retry affordances",
      sourceAreas: SRC.execution,
    });
  } finally { await context.close(); }
}

const COMMAND = "gh issue view 219 --repo MarcoLadeira/OPai";

async function sessionApprovals() {
  {
    const { context, page } = await newPage({
      commandApproval: { command: COMMAND, reason: "Run any command is blocked in Safe Auto." },
    });
    try {
      await page.fill("#input", "Fetch issue 219 for me");
      await page.getByRole("button", { name: "Send" }).click();
      await page.waitForSelector(".approval-card.command-approval", { timeout: 5000 });
      await page.waitForTimeout(250);
      await shot(page, {
        id: "PERMISSIONS-CMD-APPROVAL-001", file: "screens/permissions/PERMISSIONS-CMD-APPROVAL-001-command-approval-card.png",
        area: "permissions", state: "command blocked by run-mode policy, approval card with exact command",
        trigger: "send prompt; mock replies needs_command_approval",
        expected: "approval card shows command verbatim with Approve once / Deny",
        sourceAreas: SRC.composer.concat(`${WEB}/app.js`),
      });
      await page.locator('.approval-card.command-approval [data-ap="approve"]').click();
      await page.waitForTimeout(600);
      await shot(page, {
        id: "PERMISSIONS-CMD-APPROVED-002", file: "screens/permissions/PERMISSIONS-CMD-APPROVED-002-command-approved.png",
        area: "permissions", state: "approval granted, re-run completed",
        trigger: "click Approve once",
        expected: "card state flips to Approved, answer message appended",
        sourceAreas: SRC.composer,
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newPage({
      editApproval: { files: ["vestahub/provider_tools.py", "docs/PLAN.md"], approvedAnswer: "Edits applied." },
    });
    try {
      await page.fill("#input", "Fix the bug in provider_tools");
      await page.getByRole("button", { name: "Send" }).click();
      await page.waitForSelector(".approval-card.edit-approval", { timeout: 5000 });
      await page.waitForTimeout(250);
      await shot(page, {
        id: "PERMISSIONS-EDIT-APPROVAL-003", file: "screens/permissions/PERMISSIONS-EDIT-APPROVAL-003-edit-approval-card.png",
        area: "permissions", state: "file edits gated, approval card lists exact files",
        trigger: "send prompt; mock replies needs_edit_approval",
        expected: "card lists file paths verbatim with Allow edits once / Deny",
        sourceAreas: SRC.composer,
      });
    } finally { await context.close(); }
  }
}

async function sessionChanges() {
  const { context, page } = await newPage();
  try {
    const id = await sendPrompt(page, "Add a permission");
    await emitReply(page, id, {
      status: "answered",
      answer: "Here's the proposed change.",
      agent_policy: { mode: "implement", label: "Implement" },
      changed_files: [" M app.py", " M configs/permissions.yaml"],
      workflow: {
        mode: "implement", phase: "reviewing_diff", tests_status: "passed",
        diff_review: {
          summary: { files: 2, pending: 2, risky: 1, additions: 2, deletions: 1 },
          files: [
            { path: "app.py", decision: "pending", additions: 1, deletions: 1,
              hunks: [{ old_start: 4, old_count: 1, new_start: 4, new_count: 1, heading: "route", lines: ["-old", "+new"] }] },
            { path: "configs/permissions.yaml", decision: "pending", additions: 1, deletions: 0,
              risky: true, risk_reasons: ["permissions"],
              hunks: [{ old_start: 3, old_count: 1, new_start: 3, new_count: 2, heading: "", lines: [" existing", "+  new_perm: true"] }] },
          ],
        },
      },
      receipt: {},
    });
    await page.waitForSelector(".changeset-card", { timeout: 5000 });
    await page.waitForTimeout(300);
    await shot(page, {
      id: "CHANGES-CHANGESET-001", file: "screens/changes/CHANGES-CHANGESET-001-proposed-changeset.png",
      area: "changes", state: "proposed changeset pending review (2 files, 1 risky)",
      trigger: "reply with workflow.diff_review payload (phase reviewing_diff)",
      expected: "changeset card lists every file with approve/reject",
      sourceAreas: SRC.changes,
    });
    try {
      const toggles = page.locator(".changeset-card summary");
      if (await toggles.count()) { await toggles.first().click(); await page.waitForTimeout(250); }
      await shot(page, {
        id: "CHANGES-DIFF-FILE-002", file: "screens/changes/CHANGES-DIFF-FILE-002-file-diff-expanded.png",
        area: "changes", state: "per-file diff hunks expanded",
        trigger: "expand first file entry in the changeset card",
        expected: "diff hunks with +/- lines",
        sourceAreas: SRC.changes,
      });
    } catch (e) { failures.push({ id: "CHANGES-DIFF-FILE-002", error: String(e).slice(0, 200) }); }
  } finally { await context.close(); }
}

async function sessionErrors() {
  const { context, page } = await newPage();
  try {
    const id = await sendPrompt(page, "Summarize my changes");
    await emitReply(page, id, { status: "account_error", answer: "Network unavailable. Check your connection." });
    await page.waitForSelector(".error-card", { timeout: 5000 });
    await page.waitForTimeout(250);
    await shot(page, {
      id: "ERRORS-ACCOUNT-001", file: "screens/errors/ERRORS-ACCOUNT-001-provider-error-card.png",
      area: "errors", state: "recoverable provider error with retry / switch model",
      trigger: "reply status=account_error",
      expected: "one .error-card with human Vesta-language message and Retry affordance",
      sourceAreas: SRC.errors,
    });
    const id2 = await sendPrompt(page, "Try again");
    await emitReply(page, id2, { status: "account_not_connected", answer: "Connect the selected account first." });
    await page.waitForTimeout(400);
    await shot(page, {
      id: "ERRORS-NOT-CONNECTED-002", file: "screens/errors/ERRORS-NOT-CONNECTED-002-account-not-connected.png",
      area: "errors", state: "second error: selected account not connected",
      trigger: "reply status=account_not_connected",
      expected: "error card prompting to connect an account",
      sourceAreas: SRC.errors,
    });
  } finally { await context.close(); }
}

// Canonical settings rail pages in the current UI (settings.js sections).
const RAIL_PAGES = [
  ["general", "SETTINGS-GENERAL-001", "settings", "general / overview page"],
  ["appearance", "SETTINGS-APPEARANCE-002", "settings", "appearance page (theme/density)"],
  ["models", "SETTINGS-MODELS-003", "settings", "models & routing page"],
  ["agents", "SETTINGS-AGENTS-004", "agents", "agents settings page (team defaults)"],
  ["usage", "SETTINGS-USAGE-005", "settings", "model usage + cost firewall page"],
  ["workspace", "SETTINGS-WORKSPACE-006", "settings", "workspace page"],
  ["connections", "PROVIDERS-CONNECTED-001", "providers", "integrations page: providers & connections, all accounts connected"],
  ["safety", "PERMISSIONS-SAFETY-001", "permissions", "safety page: permissions + privacy"],
  ["advanced", "SETTINGS-ADVANCED-007", "settings", "advanced page with tools & insights"],
];

async function sessionSettings() {
  const { context, page } = await newPage();
  try {
    for (const [rail, id, area, state] of RAIL_PAGES) {
      await openSettingsRail(page, rail);
      await shot(page, {
        id, file: `screens/${area}/${id}-${rail}.png`,
        area, state: `settings rail page "${rail}" — ${state}`,
        trigger: `click settings rail item ${rail}`,
        expected: "page content visible, rail marks the active item",
        sourceAreas: area === "providers" ? SRC.providers : SRC.settings,
      });
    }
    // insight dashboards: advanced → tools & insights
    await openSettingsRail(page, "advanced");
    const tools = page.locator("details[data-settings-tools]:visible");
    if ((await tools.count()) && !(await tools.first().evaluate((n) => n.open))) {
      await tools.first().locator("summary").click();
      await page.waitForTimeout(250);
    }
    await shot(page, {
      id: "SETTINGS-TOOLS-EXPANDED-008", file: "screens/settings/SETTINGS-TOOLS-EXPANDED-008-tools-insights-expanded.png",
      area: "settings", state: "advanced page with Tools & insights disclosure open",
      trigger: "expand details[data-settings-tools]",
      expected: "links to Money Saved / Cost Firewall / Context Waste / Benchmark dashboards",
      sourceAreas: SRC.settings,
    });
    for (const [go, id, area, state] of [
      ["home", "SETTINGS-DASH-MONEY-SAVED-009", "settings", "Money Saved insight dashboard"],
      ["firewall", "SETTINGS-DASH-COST-FIREWALL-010", "settings", "Cost Firewall insight dashboard"],
    ]) {
      try {
        await page.locator(`[data-go-view="${go}"]:visible`).first().click();
        await page.waitForTimeout(500);
        await shot(page, {
          id, file: `screens/${area}/${id}-${go}.png`,
          area, state, trigger: `Settings → Advanced → Tools & insights → ${go}`,
          expected: "dashboard view rendered from mock dashboard payload",
          sourceAreas: SRC.settings,
        });
      } catch (e) { failures.push({ id, error: String(e).slice(0, 200) }); }
      await openSettingsRail(page, "advanced");
      const t2 = page.locator("details[data-settings-tools]:visible");
      if ((await t2.count()) && !(await t2.first().evaluate((n) => n.open))) {
        await t2.first().locator("summary").click();
        await page.waitForTimeout(200);
      }
    }
    // agents rail dashboards
    await openSettingsRail(page, "agents");
    for (const [go, id, state] of [
      ["agents", "AGENTS-READINESS-001", "Agent Readiness dashboard (capture status per client)"],
      ["workflows", "AGENTS-WORKFLOWS-002", "Guarded workflows dashboard"],
      ["proof", "AGENTS-PROOF-003", "Proof Bundle dashboard"],
    ]) {
      try {
        await page.locator(`[data-go-view="${go}"]:visible`).first().click();
        await page.waitForTimeout(500);
        await shot(page, {
          id, file: `screens/agents/${id}-${go}.png`,
          area: "agents", state, trigger: `Settings → Agents → ${go}`,
          expected: "dashboard view rendered from mock dashboard payload",
          sourceAreas: SRC.settings,
        });
      } catch (e) { failures.push({ id, error: String(e).slice(0, 200) }); }
      await openSettingsRail(page, "agents");
    }
  } finally { await context.close(); }
}

async function sessionProvidersDisconnected() {
  const { context, page } = await newPage({
    boot: { accounts: DISCONNECTED_ACCOUNTS },
    settings: { accounts: DISCONNECTED_ACCOUNTS },
  });
  try {
    await openSettingsRail(page, "connections");
    await shot(page, {
      id: "PROVIDERS-DISCONNECTED-001", file: "screens/providers/PROVIDERS-DISCONNECTED-001-none-connected.png",
      area: "providers", state: "all provider accounts disconnected",
      trigger: "settings.accounts with connected=false for every account",
      expected: "connect prompts instead of connected badges",
      sourceAreas: SRC.providers,
    });
  } finally { await context.close(); }
}

async function sessionEmptyStates() {
  {
    const { context, page } = await newPage({ boot: { conversations: [], recents: [] } });
    try {
      await shot(page, {
        id: "EMPTY-SIDEBAR-001", file: "screens/empty-states/EMPTY-SIDEBAR-001-no-conversations.png",
        area: "empty-states", state: "sidebar with zero saved conversations",
        trigger: "boot payload with conversations=[] recents=[]",
        expected: "honest empty sidebar, no phantom rows",
        sourceAreas: SRC.empty,
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newPage({ boot: { prefs: { onboardingSeen: false } } });
    try {
      await page.waitForSelector("#onboarding", { timeout: 5000 });
      await page.waitForTimeout(400);
      await shot(page, {
        id: "EMPTY-ONBOARDING-001", file: "screens/empty-states/EMPTY-ONBOARDING-001-first-run-step1.png",
        area: "empty-states", state: "first-run onboarding overlay, step 1 of 3 (connect a provider)",
        trigger: "boot prefs.onboardingSeen=false",
        expected: "onboarding tour card over the shell",
        sourceAreas: SRC.empty,
      });
      await page.locator('#onboarding .ob-footer [data-ob="next"]').click();
      await page.waitForTimeout(400);
      await shot(page, {
        id: "EMPTY-ONBOARDING-002", file: "screens/empty-states/EMPTY-ONBOARDING-002-first-run-step2.png",
        area: "empty-states", state: "first-run onboarding overlay, step 2 of 3 (pick a model)",
        trigger: "click Next on step 1",
        expected: "step 2 with model picker",
        sourceAreas: SRC.empty,
      });
      await page.locator('#onboarding .ob-footer [data-ob="next"]').click();
      await page.waitForTimeout(400);
      await shot(page, {
        id: "EMPTY-ONBOARDING-003", file: "screens/empty-states/EMPTY-ONBOARDING-003-first-run-step3.png",
        area: "empty-states", state: "first-run onboarding overlay, step 3 of 3 (first task opt-in)",
        trigger: "click Next on step 2",
        expected: "step 3 with labelled first-task opt-in",
        sourceAreas: SRC.empty,
      });
    } finally { await context.close(); }
  }
}

// --- Agents / AI Team surface -------------------------------------------------

const TEAM_OBJECTIVE = {
  objective_id: "team-1", objective: "Build the authentication flow", status: "running",
  revision: 1, cost_usd: "0.12", cost_complete: true, allowed_actions: [],
  assignments: [
    { assignment_id: "alex", name: "api", display_name: "Alex", avatar_index: 0, title: "Implementing authentication", status: "running", activity: "Added token validation in auth.ts", changed_files: ["src/auth.ts"], worktree: "/workers/alex", allowed_actions: ["stop"] },
    { assignment_id: "sam", name: "review", display_name: "Sam", avatar_index: 1, title: "Reviewing security", role: "reviewer", status: "running", depends_on: ["alex"], activity: "Waiting for the authentication changes", allowed_actions: [] },
    { assignment_id: "taylor", name: "tests", display_name: "Taylor", avatar_index: 2, title: "Checking sign-in behavior", status: "completed", activity: "Finished the sign-in checks", verification: { passed: true }, allowed_actions: [] },
  ],
  integration: { status: "pending" },
  timeline: [
    { sequence: 1, assignment_id: "alex", kind: "activity", occurred_at: "2026-09-13T10:24:00Z", activity: "Added token validation in auth.ts" },
    { sequence: 2, assignment_id: "sam", kind: "claimed", occurred_at: "2026-09-13T10:25:00Z" },
    { sequence: 3, assignment_id: "sam", kind: "activity", occurred_at: "2026-09-13T10:26:00Z", activity: "Found an issue with refresh-token expiry" },
    { sequence: 4, assignment_id: "alex", kind: "activity", occurred_at: "2026-09-13T10:27:00Z", activity: "Fixed refresh-token expiry in auth.ts" },
    { sequence: 5, assignment_id: "taylor", kind: "activity", occurred_at: "2026-09-13T10:28:00Z", activity: "Ran the sign-in checks" },
    { sequence: 6, assignment_id: "taylor", kind: "assignment-finished", status: "completed", occurred_at: "2026-09-13T10:29:00Z", verification_summary: "28/28 tests passed" },
  ],
};

async function sessionAgents() {
  // Team mode toggle on the composer
  {
    const { context, page } = await newPage();
    try {
      await page.locator("#teamModeBtn").click();
      await page.waitForTimeout(300);
      await shot(page, {
        id: "AGENTS-TEAM-TOGGLE-001", file: "screens/agents/AGENTS-TEAM-TOGGLE-001-team-toggle-menu.png",
        area: "agents", state: "composer Team toggle menu open (Enable AI Team)",
        trigger: "click #teamModeBtn",
        expected: "team mode menu / popover near the composer",
        sourceAreas: SRC.agents,
      });
      await page.keyboard.press("Escape");
    } finally { await context.close(); }
  }
  // Team running: enable team, send objective, emit objective payload
  {
    const { context, page } = await newPage();
    try {
      const enableBtn = page.getByRole("button", { name: "Enable AI Team", exact: true });
      if (await enableBtn.count()) {
        await enableBtn.click();
        await page.waitForTimeout(300);
      } else {
        await page.locator("#teamModeBtn").click();
        await page.waitForTimeout(300);
        const opt = page.getByRole("menuitem", { name: /team/i }).first();
        if (await opt.count()) await opt.click();
      }
      const id = await sendPrompt(page, TEAM_OBJECTIVE.objective);
      await emitObjective(page, { requestId: id, objective: TEAM_OBJECTIVE, workspaceRoot: "/demo" });
      await page.waitForSelector(".team-roster .team-person", { timeout: 6000 });
      await page.waitForTimeout(400);
      await shot(page, {
        id: "AGENTS-TEAM-PANEL-002", file: "screens/agents/AGENTS-TEAM-PANEL-002-team-running.png",
        area: "agents", state: "AI Team running: roster of 3 agents + activity feed beside chat",
        trigger: "Enable AI Team, send objective, emitObjective with 3 assignments",
        expected: "complementary 'AI Team' panel with roster (Alex/Sam/Taylor) and team feed",
        sourceAreas: SRC.agents,
      });
      // select an agent for the detail drawer
      try {
        await page.locator('.team-roster [data-team-select="sam"]').click();
        await page.waitForTimeout(400);
        await shot(page, {
          id: "AGENTS-TEAM-DETAIL-003", file: "screens/agents/AGENTS-TEAM-DETAIL-003-agent-detail.png",
          area: "agents", state: "agent detail drawer for a selected teammate",
          trigger: "click roster entry 'Sam'",
          expected: "detail panel with collaboration links and recorded work",
          sourceAreas: SRC.agents,
        });
      } catch (e) { failures.push({ id: "AGENTS-TEAM-DETAIL-003", error: String(e).slice(0, 200) }); }
    } finally { await context.close(); }
  }
  // Agents workspace via the header Agents button
  {
    const { context, page } = await newPage({
      dashboards: { agents: { objectives: [TEAM_OBJECTIVE], cards: [] } },
    });
    try {
      await page.locator("#headerAgents").click();
      await page.waitForTimeout(600);
      await shot(page, {
        id: "AGENTS-WORKSPACE-004", file: "screens/agents/AGENTS-WORKSPACE-004-header-agents-button.png",
        area: "agents", state: "Agents workspace opened from the header Agents button",
        trigger: "click #headerAgents",
        expected: "agents workspace view with objective list",
        sourceAreas: SRC.agents,
      });
    } finally { await context.close(); }
  }
}

// --- recordings ---------------------------------------------------------------

async function record(name, overrides, actions, meta) {
  const { context, page } = await newPage(overrides, true);
  try {
    await actions(page);
    await page.waitForTimeout(500);
  } catch (error) {
    failures.push({ id: meta.id, error: String(error).slice(0, 300) });
  }
  const video = page.video();
  await context.close();
  if (video) {
    const target = path.join(OUT, "recordings", name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    try {
      await video.saveAs(target);
      manifest.push({
        id: meta.id, file: "recordings/" + name, type: "recording", area: meta.area,
        state: meta.state, trigger: meta.trigger, expected: meta.expected,
        observed: "captured — see video", sourceAreas: meta.sourceAreas,
        commit: COMMIT, capturedAt: new Date().toISOString(),
      });
      console.log("rec   ok  " + meta.id);
    } catch (error) {
      failures.push({ id: meta.id, error: "video save: " + String(error).slice(0, 200) });
    }
  } else {
    failures.push({ id: meta.id, error: "no video produced" });
  }
}

async function sessionRecordings() {
  await record("01-open-ready.webm", {}, async (page) => {
    await page.waitForTimeout(2500);
    await page.locator("#modelBtn").click();
    await page.waitForTimeout(1200);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(800);
  }, {
    id: "REC-OPEN-READY-001", area: "shell",
    state: "cold open to ready, model selector opened and dismissed",
    trigger: "boot + click #modelBtn", expected: "app becomes ready, popover opens",
    sourceAreas: SRC.shell,
  });

  await record("02-send-stream-complete.webm", {}, async (page) => {
    const id = await sendPrompt(page, "Summarize my changes");
    for (const chunk of ["Looking at the workspace… ", "Found 3 changed files. ", "Summarizing the diff ", "and the test results. "]) {
      await emitToken(page, id, chunk);
      await page.waitForTimeout(500);
    }
    await emitActivity(page, id, { id: id + ":stream", type: "streaming", status: "success", title: "Response received", detail: "180 chars", requestId: id, durationMs: 2100 });
    await emitReply(page, id, { status: "answered", answer: "", receipt: { estimated_actual_usd: 0.0021, confidence: "actual" } });
    await page.waitForTimeout(1500);
  }, {
    id: "REC-SEND-COMPLETE-002", area: "execution",
    state: "send → streaming tokens → completed answer",
    trigger: "send prompt, emit tokens with pauses, then reply",
    expected: "live token rendering then terminal state",
    sourceAreas: SRC.execution,
  });

  await record("03-stop-cancel.webm", {}, async (page) => {
    const id = await sendPrompt(page, "Refactor the settings page into modules");
    await emitToken(page, id, "Splitting settings.js…");
    await page.waitForTimeout(900);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(1200);
    await confirmCancel(page, id);
    await page.waitForSelector(".stopped-card", { timeout: 5000 });
    await page.waitForTimeout(1500);
  }, {
    id: "REC-STOP-CANCEL-003", area: "execution",
    state: "stop requested mid-stream, confirmed, stopped card",
    trigger: "Escape during streaming + confirmCancel",
    expected: "Stop control, pending state, terminal stopped card",
    sourceAreas: SRC.execution,
  });

  await record("04-settings-tour.webm", {}, async (page) => {
    for (const rail of ["general", "appearance", "models", "agents", "usage", "connections", "safety", "advanced"]) {
      await openSettingsRail(page, rail);
      await page.waitForTimeout(900);
    }
    await page.locator("#settingsBack").click();
    await page.waitForTimeout(600);
  }, {
    id: "REC-SETTINGS-TOUR-004", area: "settings",
    state: "walk through every settings rail page",
    trigger: "click each rail item",
    expected: "each page renders, active rail item follows",
    sourceAreas: SRC.settings,
  });

  await record("05-command-approval.webm", {
    commandApproval: { command: COMMAND, reason: "Run any command is blocked in Safe Auto." },
  }, async (page) => {
    await page.fill("#input", "Fetch issue 219 for me");
    await page.waitForTimeout(600);
    await page.getByRole("button", { name: "Send" }).click();
    await page.waitForSelector(".approval-card.command-approval", { timeout: 5000 });
    await page.waitForTimeout(1800);
    await page.locator('.approval-card.command-approval [data-ap="approve"]').click();
    await page.waitForTimeout(2000);
  }, {
    id: "REC-CMD-APPROVAL-005", area: "permissions",
    state: "blocked command → approval card → approve → completion",
    trigger: "send gated prompt, click Approve once",
    expected: "card appears, state flips to Approved, answer lands",
    sourceAreas: SRC.composer,
  });
}

// --- main ---------------------------------------------------------------------

async function main() {
  const server = await startServer();
  browser = await chromium.launch();
  const t0 = Date.now();
  const sessions = [
    ["shell", sessionShell],
    ["execution", sessionExecution],
    ["approvals", sessionApprovals],
    ["changes", sessionChanges],
    ["errors", sessionErrors],
    ["settings", sessionSettings],
    ["providers-disconnected", sessionProvidersDisconnected],
    ["empty-states", sessionEmptyStates],
    ["agents", sessionAgents],
    ["recordings", sessionRecordings],
  ];
  for (const [name, fn] of sessions) {
    console.log("== session: " + name);
    try { await fn(); } catch (error) {
      failures.push({ id: "session:" + name, error: String(error).slice(0, 300) });
      console.log("session FAIL " + name + " :: " + String(error).slice(0, 200));
    }
  }
  await browser.close();
  server.close();

  for (const entry of manifest) {
    const p = path.join(OUT, entry.file);
    if (fs.existsSync(p)) {
      const bytes = fs.statSync(p).size;
      entry.observed += ` (file ${(bytes / 1024).toFixed(0)} KiB)`;
      entry.bytes = bytes;
    } else {
      entry.observed = "FILE MISSING AFTER CAPTURE";
    }
  }
  const manifestDoc = {
    generatedAt: new Date().toISOString(),
    durationMs: Date.now() - t0,
    commit: COMMIT,
    branch: "audit/vesta-visual-baseline-2026-09-19",
    captureMethod: "Playwright Chromium (no Qt) + injected mock bridge (vesta/assets/web/__tests__/e2e/mock-bridge.js); static server on 127.0.0.1:8124; viewport 1440x900",
    artifacts: manifest,
    captureFailures: failures,
    coverageNote: [
      "NOT captured: the Qt/PySide6 desktop wrapper (window chrome, native menus, OS dialogs) — PySide6 is not installed in the capture environment; the web UI is the real production UI rendered in QtWebEngine.",
      "NOT captured: real provider streaming/latency — all tokens, replies, errors and approvals come from the mock bridge.",
      "NOT captured: states the mock bridge cannot produce (native file pickers' OS UI, actual update downloads, real keychain prompts).",
      "Provider login OAuth redirect flow and connection doctor refresh were not exercised visually.",
      failures.length ? "Some captures failed; see captureFailures." : "No capture failures.",
    ].join("\n"),
  };
  fs.writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifestDoc, null, 2));
  console.log(`done: ${manifest.length} artifacts, ${failures.length} failures, ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  fs.rmSync(tmpVideo, { recursive: true, force: true });
}

main().catch((e) => { console.error(e); process.exit(1); });
