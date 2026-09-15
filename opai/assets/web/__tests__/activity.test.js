import { describe, it, expect } from "vitest";
import OPaiActivity from "../activity.js";

const { shouldApply, stageMessage, formatElapsed, createStore, groupRows, worstStatus, TAKING_LONGER_S, STILL_WORKING_S } = OPaiActivity;

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
    expect(early.stage).toBe("Claude is preparing your response");

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
      .toBe('vesta ask --model claude:opus --mode plan "fix tests"');
  });
  it("omits --mode for the default ask mode", () => {
    expect(cliMirror("auto", "ask", "hello")).toBe('vesta ask --model auto "hello"');
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

describe("store v2 (#229): batch ingest + request scoping", () => {
  it("ingestBatch upserts in order and coalesces by id in one pass", () => {
    const s = createStore();
    s.ingestBatch([
      { id: "r:phase", status: "running", title: "Preparing" },
      { id: "r:stream", status: "running", title: "Streaming" },
      { id: "r:phase", status: "success", title: "Request sent" },
    ]);
    expect(s.events.length).toBe(2);
    expect(s.list()[0].title).toBe("Request sent");
    expect(s.list()[1].title).toBe("Streaming");
  });
  it("upsert stays O(1)-keyed and equivalent after clear()", () => {
    const s = createStore();
    s.upsert({ id: "x", status: "running", title: "a" });
    s.clear();
    s.upsert({ id: "x", status: "success", title: "b" });
    expect(s.events.length).toBe(1);
    expect(s.list()[0].title).toBe("b");
  });
  it("clearRequest removes only that request's events and keeps ids working", () => {
    const s = createStore();
    s.ingestBatch([
      { id: "a:1", requestId: "a", status: "success", title: "old" },
      { id: "b:1", requestId: "b", status: "running", title: "keep" },
      { id: "a:2", requestId: "a", status: "success", title: "old2" },
    ]);
    s.clearRequest("a");
    expect(s.list().map((e) => e.id)).toEqual(["b:1"]);
    s.upsert({ id: "b:1", requestId: "b", status: "success", title: "kept done" });
    expect(s.events.length).toBe(1);
    expect(s.list()[0].title).toBe("kept done");
  });
});

describe("store cap (#248): bounded memory with an honest truncation count", () => {
  it("keeps unbounded stores at zero truncation under the cap", () => {
    const s = createStore(100);
    for (let i = 0; i < 50; i++) s.add({ id: "e" + i, status: "success", title: "x" });
    expect(s.events.length).toBe(50);
    expect(s.truncatedCount()).toBe(0);
  });
  it("drops the oldest and counts them once the cap+slack is exceeded", () => {
    const s = createStore(100); // slack is 256 -> trims when > 356
    for (let i = 0; i < 400; i++) s.add({ id: "e" + i, status: "success", title: "x" });
    expect(s.events.length).toBeLessThanOrEqual(100 + 256);
    expect(s.truncatedCount()).toBeGreaterThan(0);
    // Total is conserved: what's kept + what's truncated == everything added.
    expect(s.events.length + s.truncatedCount()).toBe(400);
    // The survivors are the NEWEST events (oldest were dropped).
    expect(s.list()[s.events.length - 1].id).toBe("e399");
  });
  it("upsert-in-place never grows the store or truncates", () => {
    const s = createStore(100);
    for (let i = 0; i < 5000; i++) s.upsert({ id: "same", status: "running", title: "n=" + i });
    expect(s.events.length).toBe(1);
    expect(s.truncatedCount()).toBe(0);
  });
  it("clear() resets the truncation count", () => {
    const s = createStore(100);
    for (let i = 0; i < 400; i++) s.add({ id: "e" + i, status: "success", title: "x" });
    s.clear();
    expect(s.truncatedCount()).toBe(0);
    expect(s.events.length).toBe(0);
  });
  it("byId stays consistent after a cap trim (upsert still finds survivors)", () => {
    const s = createStore(100);
    for (let i = 0; i < 400; i++) s.add({ id: "e" + i, status: "running", title: "x" });
    const survivor = s.list()[s.events.length - 1].id;
    s.upsert({ id: survivor, status: "success", title: "done" });
    expect(s.list().filter((e) => e.id === survivor).length).toBe(1);
    expect(s.list()[s.events.length - 1].status).toBe("success");
  });
});

describe("groupRows (#229): consecutive same-group folding", () => {
  const ev = (id, over = {}) => ({ id, status: "success", title: "Read file: " + id, ...over });
  it("folds consecutive events sharing a group into one row with children", () => {
    const rows = groupRows([
      ev("t0", { group: "g0" }),
      ev("t1", { group: "g0" }),
      ev("t2", { group: "g0" }),
      ev("c0", { group: "g1", title: "Ran command: pytest" }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["group", "single"]);
    expect(rows[0].label).toBe("Read file ×3");
    expect(rows[0].children.map((c) => c.id)).toEqual(["t0", "t1", "t2"]);
    expect(rows[1].event.id).toBe("c0"); // single-member group renders plain
  });
  it("interleaved groups never merge across the interruption", () => {
    const rows = groupRows([
      ev("a1", { group: "gA" }),
      ev("b1", { group: "gB" }),
      ev("a2", { group: "gA" }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["single", "single", "single"]);
  });
  it("status aggregation is worst-of and order is preserved", () => {
    const rows = groupRows([
      ev("t0", { group: "g0" }),
      ev("t1", { group: "g0", status: "error" }),
      ev("t2", { group: "g0", status: "running" }),
    ]);
    expect(rows[0].status).toBe("error");
    expect(worstStatus([{ status: "running" }, { status: "warning" }])).toBe("warning");
    expect(worstStatus([{ status: "cancelled" }, { status: "success" }])).toBe("cancelled");
  });
  it("status-channel events render no row and never split a group", () => {
    const rows = groupRows([
      ev("t0", { group: "g0" }),
      { id: "r:connect", status: "success", title: "Connected", channel: "status" },
      ev("t1", { group: "g0" }),
    ]);
    expect(rows.length).toBe(1);
    expect(rows[0].kind).toBe("group");
    expect(rows[0].children.length).toBe(2);
  });
  it("v1 events with no group or channel pass through unchanged", () => {
    const rows = groupRows([ev("plain1"), ev("plain2")]);
    expect(rows.map((r) => r.kind)).toEqual(["single", "single"]);
    expect(rows[0].event.id).toBe("plain1");
  });
});
