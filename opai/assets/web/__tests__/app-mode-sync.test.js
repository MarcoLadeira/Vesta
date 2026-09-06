import { beforeAll, describe, expect, it } from "vitest";
import "../generated-lifecycle.js";
import "../run-result.js";

// Unit coverage for the F16/F4 + F21 front-end fixes. app.js is a classic
// browser script (no exports); it only touches window/document at load for
// event listeners, so a minimal window stub makes it importable in the node
// environment. The internals under test are exposed via the window.__opai
// test hook.

let opai;

beforeAll(async () => {
  // OPaiIcons is a sibling browser script; stub it so the HTML builders under
  // test (which inline an icon) are callable in the node environment.
  globalThis.window = { addEventListener: () => {}, OPaiIcons: { icon: () => "<svg/>" } };
  await import("../app.js");
  opai = globalThis.window.__opai;
});

function payload(overrides = {}) {
  return {
    models: [
      { id: "auto", label: "OPai · Auto mode", kind: "auto", provider: "auto" },
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
    opai.state.mode = { id: "full-auto", label: "Full Auto" };
    opai.state.model = { id: "auto", label: "OPai · Auto mode", kind: "auto", provider: "auto" };
    opai.state.focus = "explain";
    opai.state.format = "normal";
    opai.state.panel = true;

    opai.applyBootSelection(payload());

    expect(opai.state.mode).toEqual({ id: "safe-auto", label: "Safe Auto" });
    expect(opai.state.model.id).toBe("account:claude:opus");
    expect(opai.state.model.provider).toBe("claude");
    expect(opai.state.focus).toBe("build");
    expect(opai.state.format).toBe("concise");
    expect(opai.state.panel).toBe(false);
  });

  it("falls back to the first mode/model and safe defaults on a sparse payload", () => {
    opai.state.mode = { id: "full-auto", label: "Full Auto" };
    opai.applyBootSelection({
      models: [], modes: [{ id: "ask", label: "Ask" }], prefs: {},
    });
    expect(opai.state.mode).toEqual({ id: "ask", label: "Ask" });
    expect(opai.state.focus).toBe("general");
    expect(opai.state.format).toBe("normal");
    // SMOKE-UX-001: a sparse payload means "no stored preference", and the
    // documented default (gui_preferences.show_control_panel) is hidden -- a
    // first-time user should get a clean chat, not an empty inspector holding
    // the right third of the window. This asserted true, which is precisely
    // the behaviour that was reported.
    expect(opai.state.panel).toBe(false);
  });
});

describe("derivedAgentMode (F21: live agent-mode preview)", () => {
  function derive(modeId, focus) {
    opai.state.mode = { id: modeId, label: modeId };
    opai.state.focus = focus;
    return opai.derivedAgentMode();
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
    const html = opai.unverifiedClaimHtml(verdict("failed", true));
    expect(html).toContain("could not verify");
    expect(html).toContain("Failed");
  });

  it("names the verdict it disagrees with, using the shared label", () => {
    expect(opai.unverifiedClaimHtml(verdict("timeout", true))).toContain("Timed out");
  });

  it("stays silent when the engine found no disagreement", () => {
    expect(opai.unverifiedClaimHtml(verdict("failed", false))).toBe("");
    expect(opai.unverifiedClaimHtml(verdict("completed", false))).toBe("");
  });

  it("stays silent without a verdict at all", () => {
    expect(opai.unverifiedClaimHtml(null)).toBe("");
    expect(opai.unverifiedClaimHtml({})).toBe("");
  });
});
