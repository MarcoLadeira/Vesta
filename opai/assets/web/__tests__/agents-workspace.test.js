import { describe, expect, it } from "vitest";
import "../agents-workspace.js";

const workspace = globalThis.OPaiAgentsWorkspace;
const objective = {
  objective_id: "objective-1", objective: "Repair independent regressions", status: "running",
  budget_usd: 4, cost_usd: 0.12, cost_complete: false, max_parallel: 2,
  allowed_actions: ["cancel", "pause", "set_budget"],
  assignments: [{
    assignment_id: "worker-1", title: "Repair API", role: "implementer", status: "completed",
    owner: "worker-1", model: "local-coder", intended_paths: ["api/"], depends_on: [],
    changed_files: ["api/server.py"], cost_usd: 0.12,
    verification: { status: "passed", summary: "API checks passed" },
  }],
  integration: { status: "pending", verification: { status: "pending" } },
};

describe("Agents workspace projection", () => {
  it("shows exact one-time approval evidence and revision-fenced review controls", () => {
    const html = workspace.renderHtml({ objectives: [{ ...objective, revision: 8, allowed_actions: ["request_review"], assignments: [{ ...objective.assignments[0], allowed_actions: ["approve"], pending_approval: { request_id: "pending-1", kind: "command", command: ["python", "check.py"], reason: "Needs approval" } }] }] });
    expect(html).toContain('data-agent-action="request_review"');
    expect(html).toContain('data-agent-action="approve"');
    expect(html).toContain("Approve once");
    expect(html).toContain("check.py");
    expect(html).toContain("Starts a new attempt");
  });
  it("offers artifact inspection by IDs without following worker-provided URLs", () => {
    const html = workspace.renderHtml({ objectives: [{ ...objective, assignments: [{ ...objective.assignments[0], worktree: "/canonical", result: { url: "https://evil.invalid", git_evidence: { base_sha: "a".repeat(40), head_sha: "b".repeat(40) } } }] }] });
    expect(html).toContain('data-agent-artifact="diff"');
    expect(html).toContain('data-agent-artifact="pr"');
    expect(html).not.toContain('href="https://evil.invalid');
  });
  it("renders exact journal decimal strings and canonical stop/budget actions", () => {
    const html = workspace.renderHtml({ objectives: [{ ...objective, cost_usd: "0.000001", budget_usd: "4.50", allowed_actions: ["stop", "budget"] }] });
    expect(html).toContain("$0.000001");
    expect(html).toContain("$4.50");
    expect(html).toContain('data-agent-action="stop"');
    expect(html).toContain('data-agent-action="budget"');
    const exponent = workspace.renderHtml({ objectives: [{ ...objective, cost_usd: "1E-7", budget_usd: "9E+3" }] });
    expect(exponent).toContain("$0.0000001");
    expect(exponent).toContain("$9000.00");
  });
  it("shows canonical objective status and integrated verification independently of completed workers", () => {
    const html = workspace.renderHtml({ objectives: [objective] });
    expect(html).toContain("Repair independent regressions");
    expect(html).toContain("Running");
    expect(html).toContain("Integration");
    expect(html).toContain("Pending");
    expect(html).toContain("API checks passed");
    expect(html).not.toContain("Objective verified");
  });

  it("marks provisional totals and never presents absent costs as zero", () => {
    expect(workspace.renderHtml({ objectives: [objective] })).toContain("Cost reporting incomplete");
    const unknown = { ...objective, cost_usd: null, assignments: [{ ...objective.assignments[0], cost_usd: null }] };
    const html = workspace.renderHtml({ objectives: [unknown] });
    expect(html).toContain("Not reported");
    expect(html).not.toContain("$0.00");
  });

  it("only renders controls advertised by the backend", () => {
    const html = workspace.renderHtml({ objectives: [objective] });
    expect(html).toContain('data-agent-action="cancel"');
    expect(html).not.toContain('data-agent-action="reconcile"');
    expect(html).not.toContain('data-agent-action="reroute"');
  });

  it("escapes provider findings, identifiers, paths, and status strings", () => {
    const unsafe = { ...objective, objective: '<img src=x onerror="alert(1)">', objective_id: 'x" onclick="alert(1)', assignments: [] };
    const html = workspace.renderHtml({ objectives: [unsafe] });
    expect(html).not.toContain("<img");
    expect(html).not.toContain('data-objective-id="x" onclick=');
    expect(html).toContain("&lt;img");
  });

  it("keeps dependencies, file ownership, and the selected assignment inspectable", () => {
    const worker = { ...objective.assignments[0], assignment_id: "worker-2", title: "Add coverage", depends_on: ["worker-1"], intended_paths: ["tests/"], blocked_reason: "Waiting for API contract", status: "blocked" };
    const html = workspace.renderHtml({ objectives: [{ ...objective, assignments: [...objective.assignments, worker] }] }, { assignmentId: "worker-2" });
    expect(html).toContain("Waiting for API contract");
    expect(html).toContain("worker-1");
    expect(html).toContain("tests/");
  });
});
