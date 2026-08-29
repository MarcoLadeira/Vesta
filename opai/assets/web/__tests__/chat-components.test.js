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
});
