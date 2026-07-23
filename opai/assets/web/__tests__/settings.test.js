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

let OPaiSettings;

beforeEach(async () => {
  globalThis.document = {
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => ({ tag: "#text", text }),
  };
  // Import fresh so the module binds to our injected document lazily via _doc().
  OPaiSettings = (await import("../settings.js")).default;
});

afterEach(() => {
  delete globalThis.document;
});

describe("el() DOM builder (#236)", () => {
  it("routes class/className to className", () => {
    expect(OPaiSettings.el("div", { class: "a b" }).className).toBe("a b");
    expect(OPaiSettings.el("div", { className: "c" }).className).toBe("c");
  });

  it("puts dynamic text in textContent, never innerHTML", () => {
    const node = OPaiSettings.el("span", { text: "<img src=x onerror=alert(1)>" });
    expect(node.textContent).toBe("<img src=x onerror=alert(1)>");
    expect(node.innerHTML).toBe("");
    expect(node.children).toHaveLength(0); // no elements minted from the value
  });

  it("appends string children as text nodes (escaped by the DOM), not markup", () => {
    const node = OPaiSettings.el("div", null, ["<b>hi</b>", 42]);
    expect(node.innerHTML).toBe("");
    expect(node.children).toEqual([
      { tag: "#text", text: "<b>hi</b>" },
      { tag: "#text", text: "42" },
    ]);
  });

  it("appends element children as-is", () => {
    const child = OPaiSettings.el("span", { text: "x" });
    const parent = OPaiSettings.el("div", null, [child]);
    expect(parent.children[0]).toBe(child);
  });

  it("wires on* handlers via addEventListener", () => {
    let clicked = 0;
    const node = OPaiSettings.el("button", { onClick: () => (clicked += 1) });
    expect(typeof node.listeners.click).toBe("function");
    node.listeners.click();
    expect(clicked).toBe(1);
  });

  it("sets dataset entries and plain attributes", () => {
    const node = OPaiSettings.el("div", {
      dataset: { railTarget: "providers" },
      "aria-label": "Providers",
    });
    expect(node.dataset.railTarget).toBe("providers");
    expect(node.attrs["aria-label"]).toBe("Providers");
  });

  it("treats true as a bare attribute and skips null/false", () => {
    const node = OPaiSettings.el("input", { required: true, disabled: false, value: null });
    expect(node.attrs.required).toBe("");
    expect("disabled" in node.attrs).toBe(false);
    expect("value" in node.attrs).toBe(false);
  });

  it("uses html only for the explicit escape hatch", () => {
    const node = OPaiSettings.el("div", { html: "<b>trusted</b>" });
    expect(node.innerHTML).toBe("<b>trusted</b>");
  });
});

describe("doctorSummary (#237)", () => {
  it("reports all-good when every connection is verified or merely detected", () => {
    const s = OPaiSettings.doctorSummary(["verified", "verified", "detected"]);
    expect(s.attention).toBe(0);
    expect(s.text).toBe("All 3 connections look good");
  });

  it("counts failed, degraded, missing-CLI, and unconfigured as attention", () => {
    const s = OPaiSettings.doctorSummary(["verified", "failed", "not_installed", "not_configured", "degraded"]);
    expect(s.attention).toBe(4);
    expect(s.text).toBe("4 of 5 connections need attention");
  });

  it("is honest about an empty provider list", () => {
    expect(OPaiSettings.doctorSummary([]).text).toBe("No providers detected yet");
  });
});

describe("section registry (#236)", () => {
  it("exposes the target taxonomy in order", () => {
    expect(OPaiSettings.sections.map((s) => s.id)).toEqual([
      "overview",
      "providers",
      "balance",
      "models",
      "firewall",
      "permissions",
      "privacy",
      "appearance",
      "about",
    ]);
  });

  it("every section has a title, keywords, and a render function", () => {
    for (const section of OPaiSettings.sections) {
      expect(section.title).toBeTruthy();
      expect(section.keywords).toBeTruthy();
      expect(typeof section.render).toBe("function");
    }
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
  const section = () => OPaiSettings.sections.find((s) => s.id === "balance");
  const sample = () => ({
    providerBalances: [
      { provider: "claude", displayName: "Claude", status: "ok", amount: 85, currency: "EUR", percent: 100, source: "manual", supportsLiveBalance: false, checkedAt: null, rechargeHint: "x", configured: true },
      { provider: "kimi", displayName: "Kimi (Moonshot)", status: "out", amount: 0, currency: "USD", percent: 0, source: "observed", supportsLiveBalance: true, checkedAt: null, rechargeHint: "Top up at platform.moonshot.ai (Billing).", configured: true },
      { provider: "gemini", displayName: "Gemini", status: "unknown", amount: null, currency: "USD", percent: null, source: "none", supportsLiveBalance: false, checkedAt: null, rechargeHint: "x", configured: true },
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

  it("never invents a number for an unknown balance", () => {
    const html = section().render(sample(), ctx);
    expect(html).toContain("Unknown");
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
  const section = () => OPaiSettings.sections.find((s) => s.id === "about");
  const base = { version: "0.2.1a1", release_stage: "alpha.1" };

  it("shows an up-to-date state with a Check for updates button", () => {
    const html = section().render(
      { about: { ...base, update: { checked: true, up_to_date: true } } },
      ctx
    );
    expect(html).toContain('data-update-status="up-to-date"');
    expect(html).toContain("latest version");
    expect(html).toContain('id="settingsCheckUpdate"');
  });

  it("shows an available state with the target version and an Update now button", () => {
    const html = section().render(
      {
        about: {
          ...base,
          update: { checked: true, up_to_date: false, latest_version: "0.3.0", commits_behind: 5, branch: "main" },
        },
      },
      ctx
    );
    expect(html).toContain('data-update-status="available"');
    expect(html).toContain("0.3.0");
    expect(html).toContain("5 changes behind");
    expect(html).toContain('id="settingsApplyUpdate"');
  });

  it("never claims up to date when the check itself failed", () => {
    const html = section().render(
      { about: { ...base, update: { checked: false, reason: "You may be offline." } } },
      ctx
    );
    expect(html).toContain('data-update-status="unknown"');
    expect(html).toContain("You may be offline.");
    expect(html).not.toContain('data-update-status="up-to-date"');
  });

  it("degrades gracefully when the payload predates the update field", () => {
    const html = section().render({ about: base }, ctx);
    expect(html).toContain('id="settingsCheckUpdate"');
    expect(html).not.toContain("undefined");
  });
});
