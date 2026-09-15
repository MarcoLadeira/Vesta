import { beforeAll, describe, expect, it } from "vitest";
import "../generated-lifecycle.js";
import "../run-result.js";

// Unit coverage for the F16/F4 + F21 front-end fixes. app.js is a classic
// browser script (no exports); it only touches window/document at load for
// event listeners, so a minimal window stub makes it importable in the node
// environment. The internals under test are exposed via the window.__vesta
// test hook.

let vesta;

beforeAll(async () => {
  // VestaIcons is a sibling browser script; stub it so the HTML builders under
  // test (which inline an icon) are callable in the node environment.
  globalThis.window = { addEventListener: () => {}, VestaIcons: { icon: () => "<svg/>" } };
  await import("../app.js");
  vesta = globalThis.window.__vesta;
});

function payload(overrides = {}) {
  return {
    models: [
      { id: "auto", label: "Vesta · Auto mode", kind: "auto", provider: "auto" },
      { id: "account:claude:opus", label: "Claude · Opus", kind: "account", provider: "claude" },
    ],
    selectedModel: "account:claude:opus",
    modes: [
      { id: "ask", label: "Ask" },
      { id: "plan", label: "Plan" },
      { id: "safe-auto", label: "Safe Auto" },
      { id: "full-auto", label: "Full Auto" },
    ],
    prefs: { mode: "safe-auto", focus: "build", format: "concise", showPanel: false },
    ...overrides,
  };
}

describe("applyBootSelection (F16/F4: payload selection is authoritative)", () => {
  it("re-assigns mode/model/focus/format from a fresh payload", () => {
    // Simulate the stale state left over from a workspace where Full Auto was
    // pinned — the exact desync the QA run hit after switching workspaces.
    vesta.state.mode = { id: "full-auto", label: "Full Auto" };
    vesta.state.model = { id: "auto", label: "Vesta · Auto mode", kind: "auto", provider: "auto" };
    vesta.state.focus = "explain";
    vesta.state.format = "normal";
    vesta.state.panel = true;

    vesta.applyBootSelection(payload());

    expect(vesta.state.mode).toEqual({ id: "safe-auto", label: "Safe Auto" });
    expect(vesta.state.model.id).toBe("account:claude:opus");
    expect(vesta.state.model.provider).toBe("claude");
    expect(vesta.state.focus).toBe("build");
    expect(vesta.state.format).toBe("concise");
    expect(vesta.state.panel).toBe(false);
  });

  it("falls back to the first mode/model and safe defaults on a sparse payload", () => {
    vesta.state.mode = { id: "full-auto", label: "Full Auto" };
    vesta.applyBootSelection({
      models: [], modes: [{ id: "ask", label: "Ask" }], prefs: {},
    });
    expect(vesta.state.mode).toEqual({ id: "ask", label: "Ask" });
    expect(vesta.state.focus).toBe("general");
    expect(vesta.state.format).toBe("normal");
    // SMOKE-UX-001: a sparse payload means "no stored preference", and the
    // documented default (gui_preferences.show_control_panel) is hidden -- a
    // first-time user should get a clean chat, not an empty inspector holding
    // the right third of the window. This asserted true, which is precisely
    // the behaviour that was reported.
    expect(vesta.state.panel).toBe(false);
  });
});

describe("derivedAgentMode (F21: live agent-mode preview)", () => {
  function derive(modeId, focus) {
    vesta.state.mode = { id: modeId, label: modeId };
    vesta.state.focus = focus;
    return vesta.derivedAgentMode();
  }

  it("maps edit-capable foci to Implement", () => {
    for (const focus of ["build", "debug", "refactor", "test", "implement"]) {
      expect(derive("safe-auto", focus)).toBe("Implement");
    }
  });

  it("maps review/explain/plan foci to their agent modes", () => {
    expect(derive("full-auto", "review")).toBe("Review");
    expect(derive("full-auto", "explain")).toBe("Explain");
    expect(derive("full-auto", "plan")).toBe("Plan");
  });

  it("a read-only run mode caps the derivation no matter the focus (F18)", () => {
    expect(derive("ask", "build")).toBe("Explain");
    expect(derive("plan", "build")).toBe("Plan");
    expect(derive("plan", "review")).toBe("Plan");
  });

  it("returns empty for foci with no honest derivation", () => {
    expect(derive("safe-auto", "general")).toBe("");
    expect(derive("safe-auto", "coding")).toBe("");
    expect(derive("safe-auto", "")).toBe("");
  });
});

describe("unverifiedClaimHtml (Round 5: pill and prose must not disagree)", () => {
  const verdict = (v, conflicts) => ({
    completion_verdict: { verdict: v, reason_code: "provider_failed", reason: "r", answer_conflicts: conflicts },
  });

  it("labels a success claim the run could not verify", () => {
    // The live failure: a red "Failed" pill directly above "has been
    // successfully pushed to the origin remote".
    const html = vesta.unverifiedClaimHtml(verdict("failed", true));
    expect(html).toContain("could not verify");
    expect(html).toContain("Failed");
  });

  it("names the verdict it disagrees with, using the shared label", () => {
    expect(vesta.unverifiedClaimHtml(verdict("timeout", true))).toContain("Timed out");
  });

  it("stays silent when the engine found no disagreement", () => {
    expect(vesta.unverifiedClaimHtml(verdict("failed", false))).toBe("");
    expect(vesta.unverifiedClaimHtml(verdict("completed", false))).toBe("");
  });

  it("stays silent without a verdict at all", () => {
    expect(vesta.unverifiedClaimHtml(null)).toBe("");
    expect(vesta.unverifiedClaimHtml({})).toBe("");
  });
});
