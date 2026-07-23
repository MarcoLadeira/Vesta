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
