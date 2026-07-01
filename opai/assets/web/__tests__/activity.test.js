import { describe, it, expect } from "vitest";
import OPaiActivity from "../activity.js";

const { shouldApply, stageMessage, formatElapsed, createStore, TAKING_LONGER_S, STILL_WORKING_S } = OPaiActivity;

describe("shouldApply (stale-response guard)", () => {
  it("applies only when ids match", () => {
    expect(shouldApply("abc", "abc")).toBe(true);
    expect(shouldApply("abc", "xyz")).toBe(false);
  });
  it("never applies when current id is empty (cancelled/superseded)", () => {
    expect(shouldApply(null, "abc")).toBe(false);
    expect(shouldApply("", "abc")).toBe(false);
    expect(shouldApply("abc", null)).toBe(false);
  });
});

describe("stageMessage (slow-model thresholds)", () => {
  it("is calm early, escalates after the threshold", () => {
    const early = stageMessage(1, { modelLabel: "Claude · Opus" });
    expect(early.suggestFaster).toBe(false);
    expect(early.stage).toContain("Waiting");

    const mid = stageMessage(TAKING_LONGER_S + 1, { modelLabel: "Claude · Opus" });
    expect(mid.suggestFaster).toBe(true);
    expect(mid.reassurance).toContain("longer than usual");
    expect(mid.severity).toBe("warning");

    const late = stageMessage(STILL_WORKING_S + 1, { modelLabel: "Opus" });
    expect(late.stage).toContain("Still working");
  });
  it("streaming overrides the waiting copy", () => {
    const sm = stageMessage(999, { streaming: true, modelLabel: "Opus" });
    expect(sm.stage).toBe("Streaming response");
    expect(sm.suggestFaster).toBe(false);
  });
});

describe("formatElapsed", () => {
  it("formats mm:ss", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(5000)).toBe("00:05");
    expect(formatElapsed(65000)).toBe("01:05");
  });
});

describe("cliMirror (GUI/CLI parity)", () => {
  const { cliMirror } = OPaiActivity;
  it("strips the account: prefix to friendly shorthand", () => {
    expect(cliMirror("account:claude:opus", "plan", "fix tests"))
      .toBe('opai ask --model claude:opus --mode plan "fix tests"');
  });
  it("omits --mode for the default ask mode", () => {
    expect(cliMirror("auto", "ask", "hello")).toBe('opai ask --model auto "hello"');
  });
  it("truncates long tasks and sanitizes quotes", () => {
    const cmd = cliMirror("auto", "ask", 'say "hi" ' + "x".repeat(100));
    expect(cmd).not.toContain('"hi"');
    expect(cmd).toContain("...");
  });
  it("uses a placeholder when no task yet", () => {
    expect(cliMirror("auto", "ask", "")).toContain("<your task>");
  });
});

describe("createStore (activity reducer)", () => {
  it("adds, upserts by id, cancels running, clears", () => {
    const s = createStore();
    s.add({ id: "1", status: "running", title: "a" });
    s.add({ id: "2", status: "pending", title: "b" });
    expect(s.events.length).toBe(2);
    s.upsert({ id: "1", status: "success", title: "a done" });
    expect(s.events.length).toBe(2);
    expect(s.list()[0].status).toBe("success");
    s.cancelRunning();
    expect(s.list()[1].status).toBe("cancelled");
    s.clear();
    expect(s.events.length).toBe(0);
  });
});
