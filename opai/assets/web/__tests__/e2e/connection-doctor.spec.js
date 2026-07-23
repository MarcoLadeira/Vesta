import { test, expect } from "@playwright/test";

import { finishRequest, openApp, sendPrompt, openSettings } from "./helpers/app.js";


const doctorEntries = [
  {
    providerId: "claude", displayName: "Claude", kind: "account", health: "failed",
    authStatus: "expired", credentialSourceLabel: "Subscription sign-in",
    cliInstalled: true, cliVersion: "Claude Code 9.8.7", lastCheckedAt: 1700000000000,
    lastError: "Session expired.", lastErrorCode: "AUTH_EXPIRED",
    safeDiagnostic: "The saved session is no longer accepted.",
    envOverridesRemoved: ["ANTHROPIC_API_KEY", "CLAUDECODE"],
    recoveryActions: ["sign_in", "test_connection", "disconnect"],
    loginSupported: true, detected: true,
  },
  {
    providerId: "groq", displayName: "Groq", kind: "api", health: "detected",
    authStatus: "detected", credentialSourceLabel: "Environment variable",
    credentialEnvironmentName: "GROQ_API_KEY", cliInstalled: null, cliVersion: "",
    safeDiagnostic: "API credential detected; use Test connection to verify it.",
    envOverridesRemoved: [], recoveryActions: ["test_connection"],
    loginSupported: false, detected: true,
  },
];


test("Connection Doctor shows complete safe provider evidence in one view", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: {}, firewall: {}, permissions: [], accounts: [], credentials: [],
      models: [], usage: [], about: {}, connectionDoctor: doctorEntries,
    },
  });
  await openSettings(page, "providers");

  const doctor = page.getByRole("region", { name: "Connection Doctor" });
  const claude = doctor.locator('[data-doctor-provider="claude"]');
  const groq = doctor.locator('[data-doctor-provider="groq"]');
  await expect(claude).toContainText("Failed");
  await expect(claude).toContainText("Subscription sign-in");
  await expect(claude).toContainText("Claude Code 9.8.7");
  await expect(claude).toContainText("ANTHROPIC_API_KEY");
  await expect(claude).toContainText("CLAUDECODE");
  await expect(claude).toContainText("Session expired.");
  await expect(claude.getByRole("button", { name: "Sign in to Claude" })).toBeVisible();
  await expect(groq).toContainText("Environment variable");
  await expect(groq).toContainText("GROQ_API_KEY");
  await expect(doctor).not.toContainText("must-never-render");
});


test("Settings guided sign-in updates health without sending a prompt", async ({ page }) => {
  await openApp(page, {
    settings: {
      prefs: {}, firewall: {}, permissions: [], accounts: [], credentials: [],
      models: [], usage: [], about: {}, connectionDoctor: doctorEntries,
    },
    loginResponses: {
      claude: {
        provider: "claude", signedIn: true, status: "signed_in",
        message: "Claude sign-in verified.",
        connection: { authStatus: "connected", safeDiagnostic: "Sign-in verified locally." },
      },
    },
  });
  await openSettings(page, "providers");

  await page.getByRole("button", { name: "Sign in to Claude" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.providerLogins.map((x) => x.provider))).toEqual(["claude"]);
  await expect(page.locator('[data-doctor-provider="claude"]')).toContainText("Verified");
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(0);
});


test("Connection Doctor fills CLI versions from the non-blocking refresh", async ({ page }) => {
  const withoutVersion = doctorEntries.map((item) => ({ ...item, cliVersion: "" }));
  await openApp(page, {
    settings: {
      prefs: {}, firewall: {}, permissions: [], accounts: [], credentials: [],
      models: [], usage: [], about: {}, connectionDoctor: withoutVersion,
    },
    refreshedDoctorEntries: [{ providerId: "claude", cliVersion: "Claude Code 9.8.7" }],
  });
  await openSettings(page, "providers");

  await expect(page.locator('[data-doctor-provider="claude"] [data-doctor-cli]')).toHaveText("Claude Code 9.8.7");
});


test("auth error guided sign-in retries the exact failed request once", async ({ page }) => {
  await openApp(page, {
    loginResponses: {
      claude: {
        provider: "claude", signedIn: true, status: "signed_in",
        message: "Claude sign-in verified.",
        connection: { authStatus: "connected" },
      },
    },
  });
  const id = await sendPrompt(page, "resume this exact task");
  const first = await page.evaluate(() => window.__mock.lastRequest);
  await finishRequest(page, id, {
    status: "failed",
    error: {
      code: "AUTH_MISSING", provider: "claude",
      title: "No Claude sign-in was found.",
      userMessage: "Sign in before retrying.",
      recoveryActions: ["reconnect", "open_settings"],
    },
  });

  await page.getByRole("button", { name: "Sign in to Claude" }).click();
  await expect.poll(() => page.evaluate(() => window.__mock.sendCount)).toBe(2);
  const retried = await page.evaluate(() => window.__mock.lastRequest);
  expect(retried.text).toBe(first.text);
  expect(retried.model).toBe(first.model);
  expect(retried.mode).toBe(first.mode);

  await page.evaluate(() => {
    const login = window.__mock.providerLogins[0];
    window.__mock.emitProviderLogin(login.requestId, {
      provider: "claude", signedIn: true, status: "signed_in",
    });
  });
  await page.waitForTimeout(50);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});


test("failed guided sign-in never retries the failed request", async ({ page }) => {
  await openApp(page, {
    loginResponses: {
      claude: {
        provider: "claude", signedIn: false, status: "not_verified",
        message: "No usable sign-in was detected.",
      },
    },
  });
  const id = await sendPrompt(page, "do not duplicate me");
  await finishRequest(page, id, {
    status: "failed",
    error: {
      code: "AUTH_MISSING", provider: "claude", title: "Sign-in required",
      userMessage: "Connect Claude.", recoveryActions: ["reconnect"],
    },
  });

  await page.getByRole("button", { name: "Sign in to Claude" }).click();
  await page.waitForTimeout(100);
  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(1);
});


test("late sign-in completion never replaces a newer conversation request", async ({ page }) => {
  await openApp(page, { deferProviderLogin: true });
  const failedId = await sendPrompt(page, "old failed task");
  await finishRequest(page, failedId, {
    status: "failed",
    error: {
      code: "AUTH_MISSING", provider: "claude", title: "Sign-in required",
      userMessage: "Connect Claude.", recoveryActions: ["reconnect"],
    },
  });
  await page.getByRole("button", { name: "Sign in to Claude" }).click();

  const newerId = await sendPrompt(page, "newer task");
  await finishRequest(page, newerId, { status: "answered_locally", answer: "new answer" });
  await page.evaluate(() => {
    const login = window.__mock.providerLogins[0];
    window.__mock.emitProviderLogin(login.requestId, {
      provider: "claude", signedIn: true, status: "signed_in",
    });
  });
  await page.waitForTimeout(50);

  expect(await page.evaluate(() => window.__mock.sendCount)).toBe(2);
});
