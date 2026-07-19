import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { lintCss } from "../../../../scripts/lint-web-design-tokens.mjs";

test("web design tokens define the documented scales", async () => {
  const tokens = await readFile(new URL("../design-tokens.css", import.meta.url), "utf8");
  for (const token of ["--type-caption", "--type-label", "--type-body", "--type-body-lg", "--type-heading", "--type-title", "--type-caption-line", "--weight-semibold", "--space-1", "--space-2", "--space-3", "--space-4", "--space-6", "--space-8", "--radius-md", "--elevation-md", "--icon-md"]) {
    assert.match(tokens, new RegExp(`${token}:`));
  }
});

test("token lint rejects raw type and layout spacing while allowing token references", () => {
  assert.deepEqual(lintCss(".card { font-size: var(--type-body); padding: var(--space-8); gap: var(--space-4); }"), []);
  assert.deepEqual(
    lintCss(".card { font-size: 13px; padding: 10px 12px; gap: 6px; margin: var(--space-px-9); padding-inline: var(--space-4) 10px; row-gap: calc(var(--space-2) + 3px); }"),
    ["font-size: 13px", "padding: 10px 12px", "gap: 6px", "margin: var(--space-px-9)", "padding-inline: var(--space-4) 10px", "row-gap: calc(var(--space-2) + 3px)"],
  );
});
