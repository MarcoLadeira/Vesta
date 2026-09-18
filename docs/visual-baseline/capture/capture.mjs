// Visual baseline capture for the OPai web GUI.
//
// Serves the worktree root over a local static server, boots the real web UI
// (opai/assets/web/index.html) in Chromium WITHOUT Qt by injecting the same
// mock bridge the e2e suite uses (opai/assets/web/__tests__/e2e/mock-bridge.js),
// then drives UI states exactly the way the specs do and records screenshots
// (PNG) and short workflow recordings (.webm).
//
// Run from anywhere:  node docs/visual-baseline/capture/capture.mjs
// Requires: repo node_modules (playwright) + Playwright chromium/ffmpeg.

import { chromium } from "playwright";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import {
  fullScenario,
  DISCONNECTED_ACCOUNTS,
} from "../../../opai/assets/web/__tests__/e2e/helpers/fixtures.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..", ".."); // worktree root
const OUT = path.join(ROOT, "docs", "visual-baseline");
const COMMIT = "8192bf0fff41f7b4776a5957892b86b7500325b5";
const PORT = 8123;
const APP_URL = `http://127.0.0.1:${PORT}/opai/assets/web/index.html`;
const MOCK = fs.readFileSync(
  path.join(ROOT, "opai/assets/web/__tests__/e2e/mock-bridge.js"),
  "utf8",
);
const VIEWPORT = { width: 1440, height: 900 };

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
const tmpVideo = fs.mkdtempSync(path.join(os.tmpdir(), "opai-vb-"));

async function newPage(overrides = {}, video = false) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    recordVideo: video ? { dir: tmpVideo, size: VIEWPORT } : undefined,
  });
  const page = await context.newPage();
  await page.addInitScript((scenario) => {
    window.__OPAI_TEST_SCENARIO__ = scenario;
  }, fullScenario(overrides));
  await page.addInitScript({ content: MOCK });
  await page.goto(APP_URL);
  await page.waitForSelector("#input");
  await page.waitForFunction(
    () => Boolean(window.__opai) && document.querySelector("#modelSel")?.options.length > 0,
    null, { timeout: 20000 },
  );
  return { context, page };
}

// --- mock bridge drivers (mirroring e2e helpers/app.js) ---------------------
const evalMock = (page, fn, arg) => page.evaluate(fn, arg);
const sendPrompt = async (page, text) => {
  await page.fill("#input", text);
  await page.getByRole("button", { name: "Send" }).click();
  await page.waitForSelector(".gen-stop", { timeout: 8000 });
  return page.evaluate(() => window.__mock.reqId());
};
const emitToken = (page, id, text) =>
  evalMock(page, ({ id, text }) => window.__mock.emitToken(id, text), { id, text });
const emitReply = (page, id, result) =>
  evalMock(page, ({ id, result }) => window.__mock.emitReply(id, result), { id, result });
const emitActivity = (page, id, event) =>
  evalMock(page, ({ id, event }) => window.__mock.emitActivity(id, event), { id, event });
const confirmCancel = (page, id) =>
  evalMock(page, (id) => window.__mock.confirmCancel(id), id);

async function openSettingsRail(page, id) {
  await page.locator("#headerSettings").click();
  if (id) await page.locator(`.settings-rail-item[data-rail-target="${id}"]`).click();
  await page.waitForTimeout(250);
}

const SRC = {
  shell: ["opai/assets/web/index.html", "opai/assets/web/app.js", "opai/assets/web/styles.css"],
  composer: ["opai/assets/web/index.html", "opai/assets/web/composer.js", "opai/assets/web/app.js"],
  execution: ["opai/assets/web/app.js", "opai/assets/web/activity.js", "opai/assets/web/message-state.js", "opai/assets/web/run-result.js"],
  settings: ["opai/assets/web/settings.js", "opai/assets/web/styles.css"],
  providers: ["opai/assets/web/settings.js"],
  changes: ["opai/assets/web/app.js", "opai/assets/web/run-result.js"],
  errors: ["opai/assets/web/app.js", "opai/assets/web/run-result.js"],
  empty: ["opai/assets/web/app.js", "opai/assets/web/index.html", "opai/assets/web/onboarding.js"],
};

// --- capture sessions --------------------------------------------------------

async function sessionShell() {
  const { context, page } = await newPage();
  try {
    await shot(page, {
      id: "SHELL-LAUNCH-001", file: "screens/shell/SHELL-LAUNCH-001-fresh-launch.png",
      area: "shell", state: "fresh launch, empty conversation, sidebar lists saved conversations",
      trigger: "app boot with mock bridge default scenario",
      expected: "branded empty state, composer, header, status strip, sidebar recents",
      sourceAreas: SRC.shell,
    });
    await page.fill("#input", "Explain how OPai routes a request to a local model first");
    await shot(page, {
      id: "COMPOSER-TYPED-001", file: "screens/composer/COMPOSER-TYPED-001-text-entered.png",
      area: "composer", state: "prompt text typed, not yet sent",
      trigger: "type into #input", expected: "text visible in composer, Send enabled",
      sourceAreas: SRC.composer,
    });
    await page.fill("#input", "");
    await page.locator("#modelBtn").click();
    await page.waitForSelector("#modelPop", { state: "visible", timeout: 5000 });
    await shot(page, {
      id: "SHELL-MODEL-POP-002", file: "screens/shell/SHELL-MODEL-POP-002-model-selector-open.png",
      area: "shell", state: "model selector popover open",
      trigger: "click #modelBtn", expected: "model list with account/free/local/auto groups and badges",
      sourceAreas: SRC.shell,
    });
    await page.keyboard.press("Escape");
    await page.locator("#modeBtn").click();
    await page.waitForSelector("#modePop", { state: "visible", timeout: 5000 });
    await shot(page, {
      id: "SHELL-MODE-POP-003", file: "screens/shell/SHELL-MODE-POP-003-run-mode-selector-open.png",
      area: "shell", state: "run-mode selector popover open",
      trigger: "click #modeBtn", expected: "Ask / Plan / Approve Edits / Safe Auto / Auto-Accept Edits / Full Auto",
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
    for (const chunk of ["OPai keeps every step visible. ", "This run was routed through ", "the connected Claude account ", "after the local-first checks ", "found no safe deterministic tool. "]) {
      await emitToken(page, id, chunk);
      await page.waitForTimeout(120);
    }
    await shot(page, {
      id: "EXEC-STREAMING-001", file: "screens/execution/EXEC-STREAMING-001-streaming-in-progress.png",
      area: "execution", state: "assistant response streaming, Stop control active, activity line running",
      trigger: "send prompt, emit tokens + activity events without reply",
      expected: "partial markdown text, streaming status, composer shows Stop",
      sourceAreas: SRC.execution,
    });
    await emitActivity(page, id, { id: id + ":stream", type: "streaming", status: "success", title: "Response received", detail: "300 chars", requestId: id, durationMs: 1234 });
    await emitReply(page, id, { status: "answered", answer: "", receipt: { estimated_actual_usd: 0.0021, confidence: "actual" } });
    await page.waitForTimeout(400);
    await shot(page, {
      id: "EXEC-COMPLETE-002", file: "screens/execution/EXEC-COMPLETE-002-run-completed.png",
      area: "execution", state: "completed run with answer and collapsed turn summary",
      trigger: "emitReply status=answered with receipt",
      expected: "final message, turn summary row, Send restored",
      sourceAreas: SRC.execution,
    });
    // expand the turn summary disclosure for a diagnostics shot
    try {
      await page.locator(".turn-summary:not([open]) > .ts-row .ts-verdict").first().click();
      await page.waitForTimeout(300);
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
    await page.waitForTimeout(250);
    await shot(page, {
      id: "EXEC-STOP-PENDING-004", file: "screens/execution/EXEC-STOP-PENDING-004-stop-requested-unconfirmed.png",
      area: "execution", state: "stop requested, backend confirmation pending",
      trigger: "press Escape during streaming, before confirmCancel",
      expected: "intermediate stopping presentation (waits for backend confirm per #380)",
      sourceAreas: SRC.execution,
    });
    await confirmCancel(page, id2);
    await page.waitForSelector(".stopped-card", { timeout: 5000 });
    await page.waitForTimeout(200);
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
  // command approval
  {
    const { context, page } = await newPage({
      commandApproval: { command: COMMAND, reason: "Run any command is blocked in Safe Auto." },
    });
    try {
      await page.fill("#input", "Fetch issue 219 for me");
      await page.getByRole("button", { name: "Send" }).click();
      await page.waitForSelector(".approval-card.command-approval", { timeout: 5000 });
      await page.waitForTimeout(200);
      await shot(page, {
        id: "PERMISSIONS-CMD-APPROVAL-001", file: "screens/permissions/PERMISSIONS-CMD-APPROVAL-001-command-approval-card.png",
        area: "permissions", state: "command blocked by run-mode policy, approval card with exact command",
        trigger: "send prompt; mock replies needs_command_approval",
        expected: "approval card shows command verbatim with Approve once / Deny",
        sourceAreas: SRC.composer.concat("opai/assets/web/app.js"),
      });
      await page.locator('.approval-card.command-approval [data-ap="approve"]').click();
      await page.waitForTimeout(500);
      await shot(page, {
        id: "PERMISSIONS-CMD-APPROVED-002", file: "screens/permissions/PERMISSIONS-CMD-APPROVED-002-command-approved.png",
        area: "permissions", state: "approval granted, re-run completed",
        trigger: "click Approve once",
        expected: "card state flips to Approved, answer message appended",
        sourceAreas: SRC.composer,
      });
    } finally { await context.close(); }
  }
  // edit approval
  {
    const { context, page } = await newPage({
      editApproval: { files: ["opaihub/provider_tools.py", "docs/PLAN.md"], approvedAnswer: "Edits applied." },
    });
    try {
      await page.fill("#input", "Fix the bug in provider_tools");
      await page.getByRole("button", { name: "Send" }).click();
      await page.waitForSelector(".approval-card.edit-approval", { timeout: 5000 });
      await page.waitForTimeout(200);
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
      if (await toggles.count()) { await toggles.first().click(); await page.waitForTimeout(200); }
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
    await page.waitForTimeout(200);
    await shot(page, {
      id: "ERRORS-ACCOUNT-001", file: "screens/errors/ERRORS-ACCOUNT-001-provider-error-card.png",
      area: "errors", state: "recoverable provider error with retry / switch model",
      trigger: "reply status=account_error",
      expected: "one .error-card with message and Retry affordance",
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

const RAIL_PAGES = [
  ["overview", "SETTINGS-OVERVIEW-001", "settings", "settings overview: status card + quick tiles"],
  ["providers", "PROVIDERS-CONNECTED-001", "providers", "providers & connections, all accounts connected"],
  ["models", "SETTINGS-MODELS-001", "settings", "models & routing page"],
  ["firewall", "SETTINGS-FIREWALL-001", "settings", "cost firewall page with budget caps and panic mode"],
  ["usage", "SETTINGS-USAGE-001", "settings", "model usage page: one provider per state (unavailable/live/balance/not configured)"],
  ["permissions", "PERMISSIONS-SAFETY-001", "permissions", "permissions & safety page with per-mode permission summaries"],
  ["privacy", "SETTINGS-PRIVACY-001", "settings", "privacy & data page"],
  ["appearance", "SETTINGS-APPEARANCE-001", "settings", "appearance page (density/theme)"],
  ["tools", "SETTINGS-TOOLS-001", "settings", "tools & insights page: prompt library + 7 insight dashboards"],
  ["about", "SETTINGS-ABOUT-001", "settings", "about page with asset build identity and update controls"],
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
        expected: "page content visible in #settingsPage, rail marks aria-current",
        sourceAreas: area === "providers" ? SRC.providers : SRC.settings,
      });
    }
    // insights dashboards via Tools & Insights
    await openSettingsRail(page, "tools");
    for (const [go, id, area, state] of [
      ["home", "SETTINGS-DASH-MONEY-SAVED-001", "settings", "Money Saved insight dashboard"],
      ["agents", "AGENTS-READINESS-001", "agents", "Agents readiness dashboard (capture status per client)"],
      ["firewall", "SETTINGS-DASH-COST-FIREWALL-001", "settings", "Cost Firewall insight dashboard"],
      ["workflows", "AGENTS-WORKFLOWS-001", "agents", "Guarded workflows dashboard"],
    ]) {
      try {
        await page.locator(`[data-go-view="${go}"]`).click();
        await page.waitForTimeout(400);
        await shot(page, {
          id, file: `screens/${area}/${id}-${go}.png`,
          area, state, trigger: `Settings → Tools & Insights → ${go}`,
          expected: "dashboard view rendered from mock dashboard payload",
          sourceAreas: SRC.settings,
        });
      } catch (e) { failures.push({ id, error: String(e).slice(0, 200) }); }
      await openSettingsRail(page, "tools");
    }
  } finally { await context.close(); }
}

async function sessionProvidersDisconnected() {
  const { context, page } = await newPage({
    boot: { accounts: DISCONNECTED_ACCOUNTS },
    settings: { accounts: DISCONNECTED_ACCOUNTS },
  });
  try {
    await openSettingsRail(page, "providers");
    await shot(page, {
      id: "PROVIDERS-DISCONNECTED-001", file: "screens/providers/PROVIDERS-DISCONNECTED-001-none-connected.png",
      area: "providers", state: "all provider accounts disconnected",
      trigger: "settings.providers with connected=false for every account",
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
      await page.waitForTimeout(600);
      await shot(page, {
        id: "EMPTY-ONBOARDING-001", file: "screens/empty-states/EMPTY-ONBOARDING-001-first-run-tour.png",
        area: "empty-states", state: "first-run onboarding overlay",
        trigger: "boot prefs.onboardingSeen=false",
        expected: "onboarding tour card over the shell",
        sourceAreas: SRC.empty,
      });
    } finally { await context.close(); }
  }
}

// --- recordings --------------------------------------------------------------

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
    for (const rail of ["overview", "providers", "models", "firewall", "usage", "permissions", "privacy", "appearance", "about"]) {
      await openSettingsRail(page, rail);
      await page.waitForTimeout(900);
    }
  }, {
    id: "REC-SETTINGS-TOUR-004", area: "settings",
    state: "walk through every settings rail page",
    trigger: "click each rail item",
    expected: "each page renders, rail aria-current follows",
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

// --- main --------------------------------------------------------------------

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

  // verify artifacts on disk
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
    branch: "audit/vesta-visual-baseline",
    captureMethod: "Playwright Chromium (no Qt) + injected mock bridge (opai/assets/web/__tests__/e2e/mock-bridge.js); static server on 127.0.0.1:8123; viewport 1440x900",
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
