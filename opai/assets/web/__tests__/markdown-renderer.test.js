import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "..");

let markdown;

beforeAll(() => {
  const context = vm.createContext({});
  context.globalThis = context;
  context.window = context;
  vm.runInContext(
    fs.readFileSync(path.join(WEB, "vendor", "markdown-it-14.1.0.min.js"), "utf8"),
    context,
  );
  vm.runInContext(fs.readFileSync(path.join(WEB, "markdown-renderer.js"), "utf8"), context);
  markdown = context.OPaiMarkdown;
});

describe("OPaiMarkdown", () => {
  it("renders semantic CommonMark and GFM structure", () => {
    const html = markdown.render([
      "# Heading",
      "",
      "> quoted **evidence**",
      "",
      "- outer",
      "  - inner",
      "",
      "---",
      "",
      "| File | Status |",
      "| --- | --- |",
      "| app.js | changed |",
    ].join("\n"));

    expect(html).toContain("<h1>Heading</h1>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<strong>evidence</strong>");
    expect(html.match(/<ul>/g)).toHaveLength(2);
    expect(html).toContain("<hr>");
    expect(html).toContain('<div class="response-table-scroll" role="region" aria-label="Scrollable table" tabindex="0"><table>');
    expect(html).toContain("</table></div>");
    expect(html).toContain("<th>File</th>");
    expect(html).toContain("<td>changed</td>");
  });

  it("gives every table its own keyboard-focusable horizontal scroll region", () => {
    const html = markdown.render([
      "| Run | Code | Result |",
      "| --- | --- | ---: |",
      "| A | with fix | 76 passed |",
      "",
      "| File | Status |",
      "| --- | --- |",
      "| app.js | changed |",
    ].join("\n"));
    expect(html.match(/class="response-table-scroll"/g)).toHaveLength(2);
    expect(html.match(/tabindex="0"/g)).toHaveLength(2);
    expect(html.match(/<table>/g)).toHaveLength(2);
  });

  it("renders inline code and fenced language metadata", () => {
    const html = markdown.render("Use `npm test`.\n\n```javascript\nconst ok = true;\n```\n");
    expect(html).toContain("<code>npm test</code>");
    expect(html).toContain('class="language-javascript"');
    expect(html).toContain("const ok = true;");
  });

  it("only activates explicit HTTP(S) Markdown links and autolinks", () => {
    const html = markdown.render([
      "[secure](https://example.com/a?q=1)",
      "[plain](http://example.com)",
      "<https://example.com/docs>",
      "https://example.com/not-linkified",
      "[relative](/settings)",
    ].join("\n\n"));

    expect(html.match(/data-ext="1"/g)).toHaveLength(3);
    expect(html).toContain('href="https://example.com/a?q=1"');
    expect(html).toContain('href="http://example.com"');
    expect(html).not.toContain('href="/settings"');
    expect(html).not.toContain('href="https://example.com/not-linkified"');
    expect(html).toContain("relative");
  });

  it.each([
    "[x](javascript:alert(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](file:///etc/passwd)",
    "[x](vbscript:msgbox(1))",
  ])("does not activate a hostile URL: %s", (payload) => {
    const html = markdown.render(payload);
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("data-ext");
  });

  it("keeps raw HTML, image syntax, and attribute payloads inert", () => {
    const html = markdown.render([
      '<script data-x="1">globalThis.pwned = true</script>',
      '<img src="https://attacker.invalid/x" onerror="alert(1)">',
      "![remote](https://attacker.invalid/pixel.png)",
      '[safe](https://example.com "&quot; onmouseover=&quot;alert(1)")',
    ].join("\n\n"));

    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain(' onerror="');
    expect(html).not.toContain(' onmouseover="');
    expect(html).toContain("&lt;script");
    expect(html).toContain("remote");
    expect(html).toContain('data-ext="1"');
  });

  it("shows an incomplete streaming fence literally until it closes", () => {
    const partial = markdown.render("Before\n\n```html\n<script>alert(1)</script>", { streaming: true });
    expect(partial).not.toContain("<pre>");
    expect(partial).not.toContain("<script>");
    expect(partial).toContain("```html");
    expect(partial).toContain("&lt;script&gt;");

    const complete = markdown.render("Before\n\n```html\n<script>alert(1)</script>\n```", { streaming: true });
    expect(complete).toContain("<pre><code");
    expect(complete).toContain('class="language-html"');
    expect(complete).toContain("&lt;script&gt;");
  });

  it("bounds parser nesting and remains deterministic on broken input", () => {
    const hostile = `${"> ".repeat(200)}bounded\n\n**unfinished [link](https://example.com`;
    const first = markdown.render(hostile, { streaming: true });
    const second = markdown.render(hostile, { streaming: true });
    expect(first).toBe(second);
    expect(first.length).toBeLessThan(10_000);
    expect(first).not.toContain("undefined");
  });
});
