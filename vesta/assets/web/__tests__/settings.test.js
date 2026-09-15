import { afterEach, beforeEach, describe, expect, it } from "vitest";

// settings.js is a browser UMD module; el() builds DOM. Vitest runs in the node
// environment (no jsdom), so we inject a tiny fake document that records how el
// routes each attribute — the point is to prove el() never turns a dynamic
// value into markup (text -> textContent/createTextNode, not innerHTML).

function makeNode(tag) {
  return {
    tag,
    className: "",
    textContent: "",
    innerHTML: "",
    attrs: {},
    dataset: {},
    listeners: {},
    children: [],
    setAttribute(k, v) {
      this.attrs[k] = v;
    },
    addEventListener(type, fn) {
      this.listeners[type] = fn;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
  };
}

let VestaSettings;

beforeEach(async () => {
  globalThis.document = {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ tag: "#text", text }),
  };
  // Import fresh so the module binds to our injected document lazily via _doc().
  VestaSettings = (await import("../settings.js")).default;
});

afterEach(() => {
  delete globalThis.document;
});

describe("el() DOM builder (#236)", () => {
  it("routes class/className to className", () => {
    expect(VestaSettings.el("div", { class: "a b" }).className).toBe("a b");
    expect(VestaSettings.el("div", { className: "c" }).className).toBe("c");
  });

  it("puts dynamic text in textContent, never innerHTML", () => {
    const node = VestaSettings.el("span", { text: "<img src=x onerror=alert(1)>" });
    expect(node.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(node.innerHTML).toBe("");
    expect(node.children).toHaveLength(0); // no elements minted from the value
  });

  it("appends string children as text nodes (escaped by the DOM), not markup", () => {
    const node = VestaSettings.el("div", null, ["<b>hi</b>", 42]);
    expect(node.innerHTML).toBe("");
    expect(node.children).toEqual([
      { tag: "#text", text: "<b>hi</b>" },
      { tag: "#text", text: "42" },
    ]);
  });

  it("appends element children as-is", () => {
    const child = VestaSettings.el("span", { text: "x" });
    const parent = VestaSettings.el("div", null, [child]);
    expect(parent.children[0]).toBe(child);
  });

  it("wires on* handlers via addEventListener", () => {
    let clicked = 0;
    const node = VestaSettings.el("button", { onClick: () => (clicked += 1) });
    expect(typeof node.listeners.click).toBe("function");
    node.listeners.click();
    expect(clicked).toBe(1);
  });

  it("sets dataset entries and plain attributes", () => {
    const node = VestaSettings.el("div", {
      dataset: { railTarget: "providers" },
      "aria-label": "Providers",
    });
    expect(node.dataset.railTarget).toBe("providers");
    expect(node.attrs["aria-label"]).toBe("Providers");
  });

  it("treats true as a bare attribute and skips null/false", () => {
    const node = VestaSettings.el("input", { required: true, disabled: false, value: null });
    expect(node.attrs.required).toBe("");
    expect("disabled" in node.attrs).toBe(false);
    expect("value" in node.attrs).toBe(false);
  });

  it("uses html only for the explicit escape hatch", () => {
    const node = VestaSettings.el("div", { html: "<b>trusted</b>" });
    expect(node.innerHTML).toBe("<b>trusted</b>");
  });
});

describe("doctorSummary (#237)", () => {
  it("reports all-good when every connection is verified or merely detected", () => {
    const s = VestaSettings.doctorSummary(["verified", "verified", "detected"]);
    expect(s.attention).toBe(0);
    expect(s.text).toBe("All 3 connections look good");
  });

  it("counts failed, degraded, missing-CLI, and unconfigured as attention", () => {
    const s = VestaSettings.doctorSummary(["verified", "failed", "not_installed", "not_configured", "degraded"]);
    expect(s.attention).toBe(4);
    expect(s.text).toBe("4 of 5 connections need attention");
  });

  it("is honest about an empty provider list", () => {
    expect(VestaSettings.doctorSummary([]).text).toBe("No providers detected yet");
  });
});

describe("section registry (#236)", () => {
  it("exposes the target taxonomy in order", () => {
    expect(VestaSettings.sections.map((s) => s.id)).toEqual([
      "overview",
      "providers",
      "balance",
      "models",
      "firewall",
      "usage",
      "permissions",
      // Prompt Library and the Insights dashboards left the sidebar and land
      // here, so Settings is where you go looking for them now.
      "tools",
      "privacy",
      "appearance",
      "about",
    ]);
  });

  it("every section has a title, keywords, and a render function", () => {
    for (const section of VestaSettings.sections) {
      expect(section.title).toBeTruthy();
      expect(section.keywords).toBeTruthy();
      expect(typeof section.render).toBe("function");
    }
  });
});

describe("Appearance response preferences", () => {
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const ctx = { esc, state: { boot: {} } };
  const section = () => VestaSettings.sections.find((s) => s.id === "appearance");

  it("renders a distinct Compact, Balanced, Detailed response density control", () => {
    const html = section().render({ prefs: { response_density: "detailed" } }, ctx);
    expect(html).toContain('data-appearance-key="response_density"');
    expect(html).toMatch(/data-value="detailed"[^>]*aria-pressed="true"/);
    expect(html).toContain(">Balanced<");
  });

  it("reads composer style from either persisted snake_case or boot camelCase", () => {
    const raw = section().render({ prefs: { composer_style: "single" } }, ctx);
    const boot = section().render({ prefs: { composerStyle: "command" } }, ctx);
    expect(raw).toMatch(/data-value="single"[^>]*aria-pressed="true"/);
    expect(boot).toMatch(/data-value="command"[^>]*aria-pressed="true"/);
  });
});

describe("Credits & Balance section", () => {
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const ctx = { esc, state: { boot: {} } };
  const section = () => VestaSettings.sections.find((s) => s.id === "balance");
  const sample = () => ({
    providerBalances: [
      { provider: "claude", displayName: "Claude", status: "ok", amount: 85, currency: "EUR", percent: 100, source: "manual", supportsLiveBalance: false, checkedAt: null, rechargeHint: "x", configured: true },
      { provider: "kimi", displayName: "Kimi (Moonshot)", status: "out", amount: 0, currency: "USD", percent: 0, source: "observed", supportsLiveBalance: true, checkedAt: null, rechargeHint: "Top up at platform.moonshot.ai (Billing).", configured: true },
      { provider: "gemini", displayName: "Gemini", status: "unknown", amount: null, currency: "USD", percent: null, source: "none", supportsLiveBalance: false, checkedAt: null, rechargeHint: "x", configured: true },
      { provider: "codex", displayName: "Codex (OpenAI)", status: "unknown", amount: null, currency: "USD", percent: null, source: "none", supportsLiveBalance: false, checkedAt: null, rechargeHint: "x", configured: true, kind: "account" },
      { provider: "groq", displayName: "Groq", status: "unknown", amount: null, currency: "USD", percent: null, source: "none", supportsLiveBalance: true, checkedAt: null, rechargeHint: "x", configured: true, kind: "free" },
    ],
  });

  it("renders one card per provider with the exact amount and a progress bar", () => {
    const html = section().render(sample(), ctx);
    expect(html).toContain("€85.00");
    expect(html).toContain('data-balance-provider="claude"');
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="100"');
  });

  it("shows the out-of-credit state with its recharge hint", () => {
    const html = section().render(sample(), ctx);
    expect(html).toContain("Out of credit");
    expect(html).toContain("balance-track out");
    expect(html).toContain("Top up at platform.moonshot.ai");
  });

  it("never invents a number, and tells apart the three real 'no amount' cases", () => {
    const html = section().render(sample(), ctx);
    // A free-tier API with no balance API and nothing entered.
    expect(html).toContain("Not tracked");
    // A subscription/account provider — there is no spendable balance to meter.
    expect(html).toContain("No credit balance");
    expect(html).toContain("Model Usage");
    // A provider with a live balance API that just hasn't been probed yet.
    expect(html).toContain("Not checked yet");
    expect(html).not.toContain(">Unknown<");
  });

  it("offers manual entry and a live refresh action", () => {
    const html = section().render(sample(), ctx);
    expect(html).toContain('data-save-balance="claude"');
    expect(html).toContain('id="balanceRefresh"');
  });

  it("renders nothing when the payload has no balances (older backend)", () => {
    expect(section().render({}, ctx)).toBe("");
  });
});

describe("About page: update status (mandatory-update system)", () => {
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const ctx = { esc, state: { boot: {} } };
  const section = () => VestaSettings.sections.find((s) => s.id === "about");
  const base = {
    version: "0.2.1a1",
    release_stage: "alpha.1",
    release_identity: {
      application_version: "0.2.1a1",
      release_channel: "alpha",
      release_stage: "alpha.1",
      build_id: "cccccccccccccccccccccccccccccccccccccccc",
      platform: "windows",
      architecture: "x86_64",
      install_type: "windows_msix",
    },
  };

  it("shows the exact tested build and artifact identity", () => {
    const html = section().render({ about: base }, ctx);
    expect(html).toContain("cccccccccccccccccccccccccccccccccccccccc");
    expect(html).toContain("windows · x86_64 · windows msix");
  });

  it("shows an up-to-date state with a Check for updates button", () => {
    const html = section().render(
      { about: { ...base, update: { operation: { state: "up_to_date" }, policy: {} } } },
      ctx
    );
    expect(html).toContain('data-update-status="up_to_date"');
    expect(html).toContain("latest version");
    expect(html).toContain('id="settingsCheckUpdate"');
  });

  it("shows canonical available target metadata without Git-update copy", () => {
    const html = section().render(
      {
        about: {
          ...base,
          update: {
            operation: {
              state: "available",
              candidate: { version: "0.3.0", channel: "stable" },
            },
            policy: {},
          },
        },
      },
      ctx
    );
    expect(html).toContain('data-update-status="available"');
    expect(html).toContain("0.3.0");
    expect(html).toContain('id="settingsCheckUpdate"');
    expect(html).not.toContain("fast-forward");
    expect(html).not.toContain('id="settingsApplyUpdate"');
  });

  it("never claims up to date when the check itself failed", () => {
    const html = section().render(
      { about: { ...base, update: { operation: { state: "unavailable", safe_diagnostic: "You may be offline." }, policy: {} } } },
      ctx
    );
    expect(html).toContain('data-update-status="unavailable"');
    expect(html).toContain("You may be offline.");
    expect(html).not.toContain('data-update-status="up_to_date"');
  });

  it("renders automatic download and install-on-quit as app-wide policy controls", () => {
    const html = section().render(
      {
        about: {
          ...base,
          update: {
            operation: { state: "up_to_date" },
            policy: { automatic_downloads: true, automatic_install_on_quit: false },
          },
        },
      },
      ctx
    );
    expect(html).toContain('data-update-policy="automatic_downloads"');
    expect(html).toContain('data-update-policy="automatic_install_on_quit"');
    expect(html).toContain("active work is never interrupted silently");
  });

  it("disables install-on-quit opt-in until automatic downloads are enabled", () => {
    const html = section().render(
      {
        about: {
          ...base,
          update: {
            operation: { state: "up_to_date" },
            policy: { automatic_downloads: false, automatic_install_on_quit: false },
          },
        },
      },
      ctx
    );
    expect(html).toMatch(/data-value="on"[^>]*disabled aria-disabled="true"/);
  });

  it("degrades gracefully when the payload predates the update field", () => {
    const html = section().render({ about: base }, ctx);
    expect(html).toContain('id="settingsCheckUpdate"');
    expect(html).not.toContain("undefined");
  });
});

describe("Model Usage section", () => {
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const ctx = { esc, state: { boot: {} } };
  const section = () => VestaSettings.sections.find((s) => s.id === "usage");

  const live = {
    provider: "gemini", displayName: "Gemini", kind: "free", configured: true, status: "live",
    window: { type: "daily", label: "Daily requests (free tier)", seconds: 86400, metric: "requests" },
    official: { available: true, source: "provider", metric: "requests", limit: 1500, remaining: 1230, used: 270, percent: 18, resetsAt: 4102444800, resetsInSeconds: 7200, observedAt: 4102437600, stale: false },
    vestaTracked: { calls: 270, tokens: 120000, tasks: 33, windowLabel: "Today" },
    detail: "Reported by the provider on your recent calls.", checkUrl: "https://aistudio.google.com", supportsRefresh: true,
  };
  const claude = {
    provider: "claude", displayName: "Claude", kind: "account", configured: true, status: "unavailable",
    window: { type: "rolling", label: "5-hour session window", seconds: 18000, metric: "session" },
    official: { available: false },
    // 18 days old (relative to real wall-clock, since fmtAgo compares against
    // Date.now()): the real-world case that motivated switching the tracked
    // count to all-time — a window-bound count would show zero here even
    // though there's real historical activity.
    vestaTracked: { calls: 12, tokens: 48000, tasks: 4, windowLabel: "All time via Vesta", lastUsedAt: Date.now() / 1000 - 18 * 24 * 3600 },
    detail: "Claude subscriptions meter a rolling 5-hour session window. Vesta's own count below only includes messages sent through Vesta's chat — not the claude CLI used directly.",
    checkUrl: "https://claude.ai/settings/usage", supportsRefresh: false,
  };
  const credit = {
    provider: "kimi", displayName: "Kimi (Moonshot)", kind: "free", configured: true, status: "live",
    window: { type: "balance", label: "Prepaid credit", seconds: null, metric: "credit" },
    official: { available: false, metric: "credit", remaining: 8.42, currency: "USD", percent: null },
    vestaTracked: { calls: 5, tokens: 9000, tasks: 2, windowLabel: "All time" },
    detail: "Prepaid credit remaining, reported by the provider.", checkUrl: "https://platform.moonshot.ai", supportsRefresh: true,
  };

  it("renders a real progress bar and reset countdown for a live-limit provider", () => {
    const html = section().render({ providerUsage: [live] }, ctx);
    expect(html).toContain('data-usage-provider="gemini"');
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="18"');
    expect(html).toContain("18% used"); // the headline stat
    expect(html).toContain("270 / 1,500 requests"); // the subtext figures
    expect(html).toContain('data-usage-resets-at="4102444800"');
    expect(html).toContain("2 hr 0 min"); // 7200s countdown
  });

  it("shows an honest no-usage-API state (no fake bar) for account providers, with the official link", () => {
    const html = section().render({ providerUsage: [claude] }, ctx);
    expect(html).toContain("5-hour session window");
    expect(html).toContain("No usage API");
    expect(html).not.toContain('role="progressbar"');
    // The official link uses the app's external-link convention (data-ext,
    // routed through the native bridge), never a plain target=_blank — a
    // direct navigation is blocked by the page's CSP and silently no-ops.
    expect(html).toContain('data-ext="1"');
    expect(html).not.toContain("target=\"_blank\"");
    expect(html).toContain("claude.ai/settings/usage");
    // With no official figure, Vesta's own tracked count becomes the headline
    // stat — same size/weight as a real number — clearly labelled, never
    // presented as the provider's number.
    expect(html).toContain('class="usage2-headline tracked"');
    expect(html).toContain("12 calls tracked");
    // All-time, not window-bound (Claude's rolling 5hr window almost never
    // has Vesta-routed activity in it), with a "last used" freshness readout
    // so 18-day-old activity never masquerades as fresh.
    expect(html).toContain("All time via Vesta");
    expect(html).toMatch(/last used \d+ d ago/);
    // The clarification that Vesta only counts its own routing, not the bare
    // CLI, is surfaced so the count is never mistaken for real Claude usage.
    expect(html).toContain("not the claude CLI used directly");
  });

  it("gives the no-official-usage headline the exact same size/weight as a real percentage or credit figure", () => {
    const barHtml = section().render({ providerUsage: [live] }, ctx);
    const creditHtml = section().render({ providerUsage: [credit] }, ctx);
    const trackedHtml = section().render({ providerUsage: [claude] }, ctx);
    // Every headline uses the same base class regardless of data source —
    // only a tone modifier (credit/tracked) may differ, never the scale.
    for (const html of [barHtml, creditHtml, trackedHtml]) {
      expect(html).toMatch(/class="usage2-headline\b[^"]*"/);
    }
  });

  it("shows remaining prepaid credit without inventing a percentage", () => {
    const html = section().render({ providerUsage: [credit] }, ctx);
    expect(html).toContain("8.42 USD left");
    expect(html).not.toContain('aria-valuenow'); // no bar without a known limit
  });

  it("never confuses Vesta-tracked counts with the official figure", () => {
    const html = section().render({ providerUsage: [live] }, ctx);
    expect(html).toContain("Vesta tracked");
    expect(html).toContain("270 calls");
  });

  it("renders a discoverable empty state when nothing is connected", () => {
    const html = section().render({ providerUsage: [] }, ctx);
    expect(html).toContain("Model Usage");
    expect(html).toContain("No providers connected yet");
    expect(html).not.toContain("undefined");
  });

  it("degrades gracefully when the payload predates providerUsage", () => {
    const html = section().render({}, ctx);
    expect(html).toContain("Model Usage");
    expect(html).not.toContain("undefined");
  });
});
