import { beforeAll, describe, expect, it } from "vitest";

// Unit coverage for the F16/F4 + F21 front-end fixes. app.js is a classic
// browser script (no exports); it only touches window/document at load for
// event listeners, so a minimal window stub makes it importable in the node
// environment. The internals under test are exposed via the window.__opai
// test hook.

let opai;

beforeAll(async () => {
  globalThis.window = { addEventListener: () => {} };
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
    expect(opai.state.panel).toBe(true);
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
