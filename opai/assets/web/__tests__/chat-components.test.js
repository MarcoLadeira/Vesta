import { beforeAll, describe, expect, it } from "vitest";

let components;

beforeAll(async () => {
  components = (await import("../chat-components.js")).default;
});

describe("chat presentation components", () => {
  it("renders user text as inert content", () => {
    expect(components.renderUserMessage('<img src=x onerror="alert(1)">')).toBe(
      '<div class="bubble user-message__bubble">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</div>',
    );
  });

  it("renders the compact assistant identity row without trusting its label", () => {
    const html = components.renderAssistantHeader({
      label: "Vesta <script>",
      color: "var(--accent)",
      copy: true,
      copyIconHtml: "<svg aria-hidden=\"true\"></svg>",
    });
    expect(html).toContain('class="role assistant-header"');
    expect(html).toContain("Vesta &lt;script&gt;");
    expect(html).toContain('data-a="copy-answer"');
    expect(html).not.toContain("<script>");
  });

  it("normalizes response density and exposes it on the response shell", () => {
    expect(components.normalizeResponseDensity("compact")).toBe("compact");
    expect(components.normalizeResponseDensity("detailed")).toBe("detailed");
    expect(components.normalizeResponseDensity("unknown")).toBe("balanced");
    expect(components.renderResponseShell({
      density: "detailed",
      headerHtml: "<header>trusted</header>",
      contentHtml: "<section>trusted</section>",
    })).toBe(
      '<div class="response-shell response-density-detailed" data-response-density="detailed"><header>trusted</header><div class="response-content"><section>trusted</section></div></div>',
    );
  });

  it("keeps the semantic prose selector around already-sanitized Markdown", () => {
    expect(components.renderProseRegion("<p>Safe Markdown</p>")).toBe(
      '<div class="body response-prose"><p>Safe Markdown</p></div>',
    );
  });

  it("builds evidence only from explicit structured values", () => {
    const model = components.evidenceBarModel({
      schema_version: 1,
      evidence: {
        verification: { applicable: true, verdict: "verified" },
        delivery: { applicable: true, verdict: "delivered" },
      },
      tests: { status: "passed", passed: 7, skipped: 1 },
      changes: { summary: { files: 3, additions: 18, deletions: 2 } },
    });

    expect(model.items).toEqual([
      { key: "verification", label: "Verification", value: "verified" },
      { key: "tests", label: "Checks", value: "passed · 7 passed · 1 skipped" },
      { key: "changes", label: "Changes", value: "3 files · +18 −2" },
      { key: "delivery", label: "Delivery", value: "delivered" },
    ]);
    expect(components.evidenceBarModel({
      schema_version: 1,
      tests: { status: "passed" },
      changes: { summary: {} },
    }).items).toEqual([
      { key: "tests", label: "Checks", value: "passed" },
    ]);
  });

  it("groups and safely renders a bounded structured work log", () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const presentation = {
      schema_version: 1,
      activity: [
        { phase: "inspect", status: "completed", message: hostile },
        { phase: "inspect", status: "completed", message: "Read app.py" },
        { phase: "test", status: "completed", message: "Focused tests passed", next_action: "Run full tests" },
      ],
    };
    const model = components.workLogModel(presentation, "balanced");

    expect(model.groups).toHaveLength(2);
    expect(model.groups[0].phase).toBe("inspect");
    expect(model.groups[0].rows).toHaveLength(2);
    expect(model.expanded).toBe(false);

    const html = components.renderWorkLog(presentation, { density: "balanced" });
    expect(html).toContain('class="gen-toggle done"');
    expect(html).toContain('class="timeline done"');
    expect(html).toContain('role="log"');
    const controlledId = html.match(/aria-controls="([^"]+)"/)[1];
    expect(html).toContain(`id="${controlledId}"`);
    expect(html).toContain('class="work-log-group"');
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(html).not.toContain("<img");
  });

  it("uses density only to choose disclosure, never to change evidence", () => {
    const presentation = {
      schema_version: 1,
      activity: [{ phase: "test", status: "completed", message: "Passed" }],
    };
    const compact = components.workLogModel(presentation, "compact");
    const balanced = components.workLogModel(presentation, "balanced");
    const detailed = components.workLogModel(presentation, "detailed");

    expect(compact.groups).toEqual(balanced.groups);
    expect(detailed.groups).toEqual(balanced.groups);
    expect(compact.expanded).toBe(false);
    expect(balanced.expanded).toBe(false);
    expect(detailed.expanded).toBe(true);
    expect(components.renderWorkLog(presentation, { density: "compact" })).toContain("hidden");
    expect(components.renderWorkLog(presentation, { density: "detailed" })).not.toContain(" hidden");
  });

  it("models verification checks and command attempts only from structured fields", () => {
    const result = {
      verification_manifest: {
        checks: [
          {
            check_id: "unit",
            kind: "unit",
            requirement: "Run focused tests",
            status: "passed",
            attempts: [{
              index: 1,
              status: "passed",
              command: ["python", "-m", "pytest", "tests/unit test.py"],
              started_at: "2026-08-30T12:00:00Z",
              ended_at: "2026-08-30T12:00:01.250Z",
              exit_status: 0,
              output_summary: '<script>alert("output")</script> 999 tests passed',
              teardown_verified: true,
            }],
          },
          {
            check_id: "lint",
            kind: "lint",
            requirement: "Lint changed files",
            status: "failed",
            attempts: [],
          },
        ],
      },
    };

    const model = components.verificationDetailsModel(result);
    expect(model.counts).toEqual({ passed: 1, failed: 1, skipped: 0, unverified: 0, total: 2 });
    expect(model.checks[0].attempts[0]).toMatchObject({
      commandText: 'python -m pytest "tests/unit test.py"',
      durationMs: 1250,
      exitStatus: 0,
      teardownVerified: true,
    });
    const html = components.renderVerificationDetails(result);
    expect(html).toContain("1 passed · 1 failed");
    expect(html).toContain("Output summary");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("999 tests passed</span>");
  });

  it("expands structured verification only in detailed response density", () => {
    const result = {
      verification_manifest: {
        checks: [{ check_id: "unit", kind: "unit", requirement: "Run checks", status: "passed" }],
      },
    };
    expect(components.renderVerificationDetails(result, { density: "compact" }))
      .toContain('<details class="verification-check passed"><summary>');
    expect(components.renderVerificationDetails(result, { density: "detailed" }))
      .toContain('<details class="verification-check passed" open><summary>');
  });

  it("renders deduplicated typed warnings without deriving them from prose", () => {
    const result = {
      answer: "WARNING: pretend prose warning",
      warnings: [
        { severity: "warning", reason: "Review permissions", term: "write" },
        { severity: "warning", reason: "Review permissions", term: "write" },
      ],
      verification_manifest: { integrity_errors: ["Manifest digest mismatch"] },
      workflow: {
        diff_review: { summary: { risky: 1, truncated: true } },
      },
      background_work: { unfinished: [{ command: ["python", "worker.py"] }] },
    };
    const presentation = { schema_version: 1, run: { answer_conflicts: true } };

    const model = components.warningModel(result, presentation);
    expect(model.items.map((item) => item.message)).toEqual([
      "Review permissions · write",
      "Manifest digest mismatch",
      "The answer conflicts with the measured run result.",
      "The diff evidence is truncated.",
      "1 risky file requires review.",
      "1 background command is still unfinished.",
    ]);
    expect(JSON.stringify(model)).not.toContain("pretend prose warning");
    const html = components.renderWarnings(result, presentation);
    expect(html.match(/Review permissions/g)).toHaveLength(1);
  });

  it("renders live and restored assistants through one structured shell", () => {
    const presentation = {
      schema_version: 1,
      run: {
        state: "completed",
        label: "Completed",
        reason: '<script>alert("run")</script>',
        next_action: "Review <changes>",
      },
      tests: { status: "passed", passed: 4 },
      changes: { summary: { files: 2 } },
      activity: [{ phase: "test", status: "completed", message: "4 tests passed" }],
    };
    const restored = components.renderAssistantPresentation({
      density: "balanced",
      headerHtml: '<div class="role">Vesta</div>',
      proseHtml: '<div class="body">Restored answer</div>',
      presentation,
    });
    const live = components.renderAssistantPresentation({
      density: "balanced",
      headerHtml: '<div class="role">Vesta</div>',
      proseHtml: '<div class="body">Live answer</div>',
      presentation,
      retryable: true,
    });

    for (const html of [restored, live]) {
      // e92e01d folded the verdict card into the one-line turn summary: the
      // verdict is the summary's first word now, not a card after the log.
      expect(html).toContain('class="turn-summary is-completed"');
      expect(html).toContain('<span class="ts-verdict">Done</span>');
      expect(html).toContain('class="evidence-bar"');
      expect(html).toContain('class="gen-toggle done"');
      expect(html).toContain('class="timeline done"');
      expect(html).toContain('class="body"');
      expect(html).toContain('class="role"');
      expect(html).toContain("&lt;script&gt;");
      expect(html).not.toContain("<script>");
      // Said once. The old stack rendered the verdict as its own card too.
      expect(html).not.toContain('class="completion-verdict');
      expect(html.indexOf('class="body"')).toBeLessThan(html.indexOf('class="turn-summary'));
      expect(html.indexOf('class="turn-summary')).toBeLessThan(html.indexOf('class="evidence-bar"'));
      expect(html.indexOf('class="evidence-bar"')).toBeLessThan(html.indexOf('class="gen-toggle done"'));
    }
    expect(restored).not.toContain('data-a="retry"');
    expect(live).not.toContain('data-a="retry"');
  });

  it("falls back to legacy prose without parsing or inventing evidence", () => {
    const prose = '<div class="body">999 tests passed and 42 files changed</div>';
    for (const presentation of [undefined, { schema_version: 2 }, { schema_version: 1 }]) {
      const html = components.renderAssistantPresentation({
        density: "balanced",
        headerHtml: '<div class="role">Vesta</div>',
        proseHtml: prose,
        presentation,
      });
      expect(html).toContain(prose);
      expect(html).not.toContain("evidence-bar");
      expect(html).not.toContain("completion-verdict");
      expect(html).not.toContain("timeline done");
    }
  });

  it("composes the response in semantic reading order", () => {
    const html = components.renderAssistantPresentation({
      density: "balanced",
      headerHtml: '<div class="role">Vesta</div>',
      proseHtml: '<div class="body">Outcome</div>',
      presentation: {
        schema_version: 1,
        run: { state: "partial", label: "Partial" },
        evidence: { verification: { verdict: "unverified" } },
        activity: [{ phase: "test", status: "failed", message: "Check failed" }],
      },
      result: {
        warnings: [{ severity: "warning", reason: "Review the failed check" }],
      },
      changesHtml: '<section class="changeset-card">Changes</section>',
      supportHtml: '<section class="workflow-card">Workflow</section>',
      warningsHtml: '<aside class="unverified-claim">Unverified claim</aside>',
    });
    // e92e01d/a02d8a8: the work stays visible -- the answer, a warning that
    // contradicts it, the changes to review -- and the record of the work sits
    // behind the summary, which opens with the verdict.
    const positions = [
      'class="body"',
      'class="unverified-claim"',
      'class="changeset-card"',
      'class="turn-summary is-partial"',
      'class="evidence-bar"',
      'class="gen-toggle done"',
      'class="workflow-card"',
      'class="response-warnings"',
    ].map((needle) => html.indexOf(needle));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("keeps measured live activity and the result's verdict when a projection omits them", () => {
    // The legacy verdict card is no longer rendered (e92e01d); the verdict
    // reaches the summary through `verdict`, the shape app.js's
    // summaryVerdict() hands over. What must survive is the truth of it: a
    // partial run whose projection carries no `run` is never shown as a plain
    // "Answered".
    const html = components.renderAssistantPresentation({
      proseHtml: '<div class="body">Outcome</div>',
      presentation: { schema_version: 1, evidence: { delivery: { verdict: "delivered" } } },
      legacyWorkHtml: '<button class="gen-toggle done">Activity</button>',
      legacyFinalHtml: '<section class="completion-verdict partial">Partial</section>',
      verdict: { state: "partial", label: "Partial", displayLabel: "", reason: "No diff evidence" },
    });
    expect(html).toContain('<button class="gen-toggle done">Activity</button>');
    expect(html).toContain('class="turn-summary is-partial"');
    expect(html).toContain('<span class="ts-verdict">No changes made</span>');
    expect(html).toContain("No diff evidence");
    expect(html).not.toContain(">Answered<");
    expect(html).not.toContain('class="completion-verdict');
  });
});
