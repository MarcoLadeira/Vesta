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
      label: "OPai <script>",
      color: "var(--accent)",
      copy: true,
      copyIconHtml: "<svg aria-hidden=\"true\"></svg>",
    });
    expect(html).toContain('class="role assistant-header"');
    expect(html).toContain("OPai &lt;script&gt;");
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
      headerHtml: '<div class="role">OPai</div>',
      proseHtml: '<div class="body">Restored answer</div>',
      presentation,
    });
    const live = components.renderAssistantPresentation({
      density: "balanced",
      headerHtml: '<div class="role">OPai</div>',
      proseHtml: '<div class="body">Live answer</div>',
      presentation,
      retryable: true,
    });

    for (const html of [restored, live]) {
      expect(html).toContain('class="completion-verdict completed"');
      expect(html).toContain('class="evidence-bar"');
      expect(html).toContain('class="gen-toggle done"');
      expect(html).toContain('class="timeline done"');
      expect(html).toContain('class="body"');
      expect(html).toContain('class="role"');
      expect(html).toContain("&lt;script&gt;");
      expect(html).not.toContain("<script>");
      expect(html.indexOf('class="body"')).toBeLessThan(html.indexOf('class="evidence-bar"'));
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
        headerHtml: '<div class="role">OPai</div>',
        proseHtml: prose,
        presentation,
      });
      expect(html).toContain(prose);
      expect(html).not.toContain("evidence-bar");
      expect(html).not.toContain("completion-verdict");
      expect(html).not.toContain("timeline done");
    }
  });
});
