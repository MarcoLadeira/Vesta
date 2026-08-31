import { test, expect } from "@playwright/test";

import { openApp, openNav } from "./helpers/app.js";

// The Composer Redesign keeps every capability of the old composer but changes
// the information architecture: one quiet toolbar with mode/model popovers and
// on-demand context, plus three switchable directions. These specs assert the
// new surfaces and the behaviour that must be preserved (send gate, path-only
// context, send↔stop) unchanged.

test("the buttons are the summary; nothing restates them underneath", async ({ page }) => {
  // There was a line under the composer reading "<Mode> · <Model> · local".
  // Both halves were already named by the two buttons a few pixels above it,
  // so it was a third copy of state the user could already see.
  await openApp(page);
  await expect(page.locator("#composerSummary")).toHaveCount(0);
  await expect(page.locator("#modeBtnLabel")).toHaveText("Auto");

  // Change the mode through its popover — no duplicate control anywhere.
  await page.locator("#modeBtn").click();
  await page.getByRole("menuitemradio", { name: /^Plan/ }).click();
  await expect(page.locator("#modeBtnLabel")).toHaveText("Plan");
  // The redesign drives the real (hidden) mode control, so the pipeline is unchanged.
  await expect(page.locator("#modeSel")).toHaveValue("plan");

  // Pick a paid cloud model — summary drops "local".
  await page.locator("#modelBtn").click();
  await page.getByRole("menuitemradio", { name: /Claude · Opus/ }).click();
  await expect(page.locator("#modelBtnLabel")).toHaveText("Claude");
  await expect(page.locator("#modelSel")).toHaveValue("account:claude:opus");

  // A local model brings "local" back.
  await page.locator("#modelBtn").click();
  await page.getByRole("menuitemradio", { name: /Qwen 2.5 Coder/ }).click();
  await expect(page.locator("#modelBtnLabel")).toHaveText("Local");
});

test("the mode menu is Claude Code's, in OPai's rows", async ({ page }) => {
  await openApp(page);
  await page.locator("#modeBtn").click();
  const menu = page.locator("#modePop");
  await expect(menu.locator(".cpop-head")).toHaveText("Mode");
  await expect(menu.locator(".cpop-title")).toHaveText([
    "Auto",
    "Manual",
    "Accept edits",
    "Plan",
    "Bypass permissions",
  ]);
  await expect(menu.locator(".cpop-desc")).toHaveText([
    "OPai handles permission decisions",
    "Always ask before making changes",
    "Automatically accept all file edits",
    "Create a plan before making changes",
    "Run everything, including pushes, without asking",
  ]);
  // The four graded modes are numbered; Bypass is not, and sits below a
  // separator — it is not the next rung on the ladder.
  await expect(menu.locator(".cpop-meta")).toHaveText(["1", "2", "3", "4"]);
  await expect(menu.locator(".cpop-sep")).toHaveCount(1);
});

test("the number keys pick a mode, and Bypass has none", async ({ page }) => {
  await openApp(page);
  await page.locator("#modeBtn").click();
  await page.locator("#modePop").press("3");
  await expect(page.locator("#modeSel")).toHaveValue("auto-edits");
  await expect(page.locator("#modeBtnLabel")).toHaveText("Accept edits");

  await page.locator("#modeBtn").click();
  await page.locator("#modePop").press("5");
  await expect(page.locator("#modeSel")).toHaveValue("auto-edits");
});

test("Attach files and Add a folder use the native picker results", async ({ page }) => {
  await openApp(page, {
    contextPickedFiles: ["index.html", "app.js"],
    contextPickedFolders: ["styles/"],
  });

  await page.locator("#ctxBtn").click();
  await page.getByRole("menuitem", { name: "Attach files…" }).click();
  await expect(page.locator("#contextHints")).toContainText("@index.html");
  await expect(page.locator("#contextHints")).toContainText("@app.js");

  await page.locator("#ctxBtn").click();
  await page.getByRole("menuitem", { name: "Add a folder…" }).click();
  await expect(page.locator("#contextHints")).toContainText("@styles/");
  expect(await page.evaluate(() => window.__mock.contextFilePicks)).toBe(1);
  expect(await page.evaluate(() => window.__mock.contextFolderPicks)).toBe(1);
});

test("edit modes are disabled when the selected CLI lacks scoped edit controls", async ({ page }) => {
  await openApp(page, {
    boot: {
      models: [
        {
          id: "account:copilot:gpt-5.4",
          label: "Copilot · GPT-5.4",
          kind: "account",
          group: "copilot",
          provider: "copilot",
          available: true,
          healthy: true,
          repo_editing: false,
        },
      ],
      selectedModel: "account:copilot:gpt-5.4",
    },
  });

  await page.locator("#modeBtn").click();
  const menu = page.locator("#modePop");
  await expect(menu).toContainText("Update this provider CLI to enable scoped edits");
  await expect(menu.locator('[data-id="ask"]')).toHaveCount(0);
  await expect(menu.locator('[data-id="plan"]')).toBeEnabled();
  await expect(menu.locator('[data-id="safe-auto"]')).toBeDisabled();
  await expect(menu.locator('[data-id="approve-edits"]')).toBeDisabled();
  await expect(menu.locator('[data-id="auto-edits"]')).toBeDisabled();
  await expect(menu.locator('[data-id="full-auto"]')).toBeDisabled();
});

test("model popover shows working models, balances, and explains removals", async ({ page }) => {
  // Redesigned picker contract: the Auto card is pinned on top; only
  // configured models appear (unconfigured ones live in Manage models);
  // a configured-but-failing model is shown disabled with its reason; an
  // out-of-credit model is REMOVED with an explanatory note; models with a
  // known balance show the exact remaining amount.
  await openApp(page, {
    boot: {
      models: [
        { id: "account:claude:opus", label: "OPai · Powerful mode", kind: "account", group: "claude", provider: "claude", available: true, healthy: true, balance: { provider: "claude", displayName: "Claude", status: "ok", amount: 85, currency: "EUR", percent: 100, source: "manual" } },
        { id: "free:gemini:gemini-3.1-flash-lite", label: "Gemini · 3.1 Flash-Lite (free tier)", kind: "free", group: "free", provider: "gemini", available: true, healthy: false, health_reason: "Recently unavailable — OPai will retry it automatically." },
        { id: "free:groq:openai/gpt-oss-120b", label: "Groq · GPT-OSS 120B (free tier)", kind: "free", group: "free", provider: "groq", available: false, disabled_reason: "Set GROQ_API_KEY to enable Groq" },
        { id: "free:kimi:kimi-k2.6", label: "Kimi · K2.6 (free tier)", kind: "free", group: "free", provider: "kimi", available: false, out_of_credit: true, disabled_reason: "Kimi (Moonshot) is out of credit.", balance: { provider: "kimi", displayName: "Kimi (Moonshot)", status: "out", amount: 0, currency: "USD", percent: 0, source: "provider" } },
        { id: "ollama:qwen2.5-coder", label: "Qwen 2.5 Coder · local", kind: "local", group: "local", provider: "ollama", available: true, healthy: true },
        { id: "auto", label: "OPai · Auto mode", kind: "auto", group: "routing" },
      ],
      selectedModel: "auto",
    },
  });
  await page.locator("#modelBtn").click();
  const menu = page.locator("#modelPop");
  // Auto stays first and still says why, but as a row like every other one:
  // "Recommended" sits in the slot each model uses for its provider, instead
  // of a bordered card with a pill and a sentence of explanation.
  const auto = menu.getByRole("menuitemradio", { name: /Auto/ }).first();
  await expect(auto).toBeVisible();
  await expect(auto.locator(".cpop-auto-tag")).toHaveText("Recommended");
  await expect(auto.locator(".cpop-desc")).toHaveCount(0);
  // A working model with a known balance shows the exact remaining amount.
  await expect(menu.getByRole("menuitemradio", { name: /Powerful/ })).toContainText("€85.00 left");
  // A configured-but-failing model is shown disabled with the reason.
  const gemini = menu.getByRole("menuitemradio", { name: /Gemini/ });
  await expect(gemini).toBeDisabled();
  await expect(gemini).toContainText("Recently unavailable");
  // Unconfigured models never appear in selection — only in Manage models.
  await expect(menu.getByRole("menuitemradio", { name: /Groq/ })).toHaveCount(0);
  // Out-of-credit models are removed AND their absence is explained.
  await expect(menu.getByRole("menuitemradio", { name: /Kimi/ })).toHaveCount(0);
  await expect(menu.locator("[data-credit-note]")).toContainText("Kimi (Moonshot)");
  await expect(menu.locator("[data-credit-note]")).toContainText("out of credit");
  // The "Keep work on this machine" toggle is gone. It set the model to Auto
  // when off and did nothing when on — a duplicate of the Auto row above it
  // half the time, and a no-op the rest.
  await expect(menu.getByRole("menuitemcheckbox")).toHaveCount(0);
  // Provider setup lives behind one footer action, out of the selection list.
  const manageModels = menu.getByRole("menuitem", { name: "Manage models" });
  await expect(manageModels).toBeVisible();
  await manageModels.click();
  await expect(page.locator("#view-settings")).toBeVisible();
  await expect(page.locator('.settings-pane[data-pane="models"]')).toHaveClass(/active/);
  await expect(page.locator('.settings-rail-item[data-rail-target="models"]')).toHaveAttribute("aria-current", "page");
});

test("Ctrl+M opens the model picker from outside Chat", async ({ page }) => {
  await openApp(page);
  await openNav(page, "Prompt Library");
  await expect(page.locator("#view-prompts")).toBeVisible();

  await page.keyboard.press("Control+m");

  await expect(page.locator("#view-chat")).toBeVisible();
  await expect(page.locator("#modelPop")).toBeVisible();
  await expect(page.locator("#modelBtn")).toHaveAttribute("aria-expanded", "true");
});

test("context is added on demand and sent as a path-only reference", async ({ page }) => {
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.locator("#ctxPathDraft").fill("src/router.py");
  await page.locator("#ctxPathDraft").press("Enter");
  await expect(page.locator("#contextHints")).toContainText("@src/router.py");

  await page.locator("#input").fill("Explain this route");
  // Survives navigation away and back.
  await openNav(page, "Settings");
  await openNav(page, "Chat");
  await expect(page.locator("#input")).toHaveValue("Explain this route");
  await page.getByRole("button", { name: "Send prompt" }).click();
  expect(await page.evaluate(() => window.__mock.lastRequest.contextHints)).toEqual(["src/router.py"]);
  expect(await page.evaluate(() => window.__mock.lastRequest.text)).toBe(
    "Repository context references:\n@src/router.py\n\nExplain this route",
  );
});

test("Use this repository targets the active workspace, not its parent repo", async ({ page }) => {
  await openApp(page, {
    boot: {
      workspace: {
        root: "/sandbox/generated-app",
        repo_root: "/sandbox",
        name: "sandbox",
        label: "sandbox/generated-app",
        build_app: true,
        build_app_name: "generated-app",
      },
    },
  });
  await page.locator("#ctxBtn").click();
  const useWorkspace = page.getByRole("menuitem", { name: /Use this repository/ });
  await expect(useWorkspace).toContainText("sandbox/generated-app");
  await expect(useWorkspace.locator(".cpop-meta")).toHaveCSS("text-overflow", "ellipsis");
  await useWorkspace.click();
  await expect(page.locator("#contextHints")).toContainText("@./");
});

test("the context path input rejects Windows absolute paths", async ({ page }) => {
  await openApp(page);
  await page.locator("#ctxBtn").click();
  await page.locator("#ctxPathDraft").fill("C:\\Users\\Frist\\secret.txt");
  await page.locator("#ctxPathDraft").press("Enter");
  await expect(page.locator("#contextHints")).toBeEmpty();
});

test("dropped files add path-only context and never read the file payload", async ({ page }) => {
  await openApp(page);
  await page.locator(".composer").evaluate((composer) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["private source must not be read"], "src/components/Composer.jsx"));
    composer.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  await expect(page.locator("#contextHints")).toContainText("@src/components/Composer.jsx");
  await page.locator("#input").fill("Review the component");
  await page.getByRole("button", { name: "Send prompt" }).click();
  expect(await page.evaluate(() => window.__mock.lastRequest.contextHints)).toEqual(["src/components/Composer.jsx"]);
  expect(await page.evaluate(() => JSON.stringify(window.__mock.lastRequest))).not.toContain("private source");
});

test("disabled send explains an unconfigured account and links to Settings", async ({ page }) => {
  await openApp(page, {
    boot: { accounts: [{ id: "claude", label: "Claude", connected: false, authenticated: false }] },
  });
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.locator("#composerReason")).toContainText("Connect Claude before sending");
  await page.getByRole("button", { name: "Open Settings" }).click();
  await expect(page.locator("#view-settings")).toBeVisible();
});

test("an empty prompt blocks sending without being told off for it", async ({ page }) => {
  // Blocking and explaining are separate. The disabled Send button already
  // says an empty box cannot be sent; the sentence under the composer said it
  // again, permanently, before the user had done anything. Collapsing the two
  // is what re-enabled Send on an empty prompt while this was being written.
  await openApp(page);
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.locator("#composerReason")).toBeEmpty();
  await page.locator("#input").fill("Check the project setup");
  await expect(page.locator("#send")).toBeEnabled();
});

test("a warning that needs an action still appears", async ({ page }) => {
  // Only the noise went. A reason the user has to act on is still shown.
  await openApp(page, { boot: { accounts: [{ id: "claude", connected: false }] } });
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await page.locator("#input").fill("do the thing");
  await expect(page.locator("#composerReason")).toContainText("Connect");
  await expect(page.locator("#composerReason")).toHaveAttribute("data-tone", "warning");
});

test("an empty prompt never offers connection settings for an already connected account", async ({ page }) => {
  await openApp(page);
  await page.locator("#modelSel").selectOption("account:claude:opus");
  await expect(page.locator("#send")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Open Settings" })).toHaveCount(0);
});

test("Shift+Enter adds a line while Enter sends", async ({ page }) => {
  await openApp(page);
  await page.locator("#input").fill("first");
  await page.locator("#input").press("Shift+Enter");
  await page.locator("#input").pressSequentially("second");
  await expect(page.locator("#input")).toHaveValue("first\nsecond");
  await page.locator("#input").press("Enter");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});

test("Send changes to Stop immediately and Stop cancels the active request", async ({ page }) => {
  await openApp(page);
  await page.locator("#input").fill("Investigate the router");
  await page.getByRole("button", { name: "Send prompt" }).click();
  await expect(page.locator("#send")).toHaveAccessibleName("Stop generation");
  await page.locator("#send").click();
  expect(await page.evaluate(() => window.__mock.cancelCount)).toBe(1);
  await expect(page.getByRole("button", { name: "Send prompt" })).toBeVisible();
});

test("the composer direction can be switched between the three designs in Settings", async ({ page }) => {
  await openApp(page);
  const composer = page.locator("#composer");
  await expect(composer).toHaveAttribute("data-composer-style", "toolbar");

  await openNav(page, "Settings");
  await page.locator('[data-rail-target="appearance"]').click();
  const seg = page.locator('[data-composer-style-key]');
  await seg.getByRole("button", { name: "Single line" }).click();
  await openNav(page, "Chat");
  await expect(composer).toHaveAttribute("data-composer-style", "single");

  await openNav(page, "Settings");
  await page.locator('[data-rail-target="appearance"]').click();
  await page.locator('[data-composer-style-key]').getByRole("button", { name: "Command bar" }).click();
  await openNav(page, "Chat");
  await expect(composer).toHaveAttribute("data-composer-style", "command");
  await expect(page.locator("#composerTokens")).toBeVisible();

  // The persisted preference reaches the backend.
  const saved = await page.evaluate(() => window.__mock.savedPrefs);
  expect(saved).toContainEqual(["composer_style", "single"]);
  expect(saved).toContainEqual(["composer_style", "command"]);
});
