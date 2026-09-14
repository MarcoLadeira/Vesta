import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { test } from "node:test";

import {
  lintColours,
  lintCss,
  lintScriptColours,
  lintThemeTokens,
  paletteIds,
} from "../../../../scripts/lint-web-design-tokens.mjs";

test("web design tokens define the documented scales", async () => {
  const tokens = await readFile(new URL("../design-tokens.css", import.meta.url), "utf8");
  for (const token of ["--type-caption", "--type-label", "--type-body", "--type-prose", "--type-body-lg", "--type-heading", "--type-title", "--type-caption-line", "--weight-semibold", "--space-1", "--space-2", "--space-3", "--space-4", "--space-6", "--space-8", "--radius-md", "--elevation-md", "--icon-md", "--measure-prose", "--measure-response", "--measure-composer", "--surface-code", "--focus-ring", "--control-min"]) {
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

/* ---------- light mode: colour must come from tokens ---------- */

test("colour lint rejects every way of writing a colour into component CSS", () => {
  assert.deepEqual(
    lintColours(
      ".a { color: #fff; background: rgba(0, 0, 0, 0.5); border-color: white; outline-color: hsl(200 50% 40%); }" +
        ".b { box-shadow: 0 1px 2px rgb(10 20 30 / 40%); fill: RED; background: color-mix(in srgb, #123456 20%, transparent); }",
    ),
    [
      "color: #fff",
      "background: rgba(0, 0, 0, 0.5)",
      "border-color: white",
      "outline-color: hsl(200 50% 40%)",
      "box-shadow: 0 1px 2px rgb(10 20 30 / 40%)",
      "fill: RED",
      "background: color-mix(in srgb, #123456 20%, transparent)",
    ],
  );
});

test("colour lint allows token references and the few places a colour is not paint", () => {
  assert.deepEqual(
    lintColours(
      ".a { color: var(--red); background: rgba(var(--tint-success-rgb), 0.12); border: 1px solid transparent; }" +
        ".b { fill: currentColor; background: color-mix(in oklab, var(--accent) 14%, transparent); }" +
        /* masks read alpha only */
        ".c { mask-image: linear-gradient(#000, transparent); -webkit-mask-image: linear-gradient(black, transparent); }" +
        /* bytes inside a data URI, a font name, generated text, and a comment */
        ".d { background-image: url(\"data:image/svg+xml;utf8,<svg fill='black'/>\"); font-family: \"Tan Sans\"; content: \"#1\"; }" +
        "/* color: #fff */ .e { transition: color 0.2s var(--ease); }",
    ),
    [],
  );
});

test("colour lint refuses tokens declared inside component CSS", () => {
  assert.deepEqual(lintColours("#view-settings { --settings-surface: var(--panel); }"), [
    "--settings-surface: var(--panel) (define tokens in design-tokens.css, not in component CSS)",
  ]);
});

test("theme lint requires every palette to declare every token, and no colour in the scales", () => {
  const css = (base, light, dark, scales) =>
    `:root,\n[data-theme="viber-coder"] { ${base} }\n[data-theme="light"] { ${light} }\n[data-theme="dark"] { ${dark} }\n:root { ${scales} }`;
  assert.deepEqual(
    lintThemeTokens(css("--bg: #04050f; --ink: #fff;", "--bg: #fff; --ink: #000;", "--bg: #000; --ink: #fff;", "--space-1: 4px;")),
    [],
  );
  assert.deepEqual(
    lintThemeTokens(css("--bg: #04050f; --new: #123;", "--bg: #fff; --new: #456; --extra: #fff;", "--bg: #000;", "--space-1: 4px;")),
    [
      '--extra is only in "light"; declare it in every palette',
      '--new has no "dark" value',
    ],
  );
  assert.deepEqual(
    lintThemeTokens(css("--bg: #000;", "--bg: #fff;", "--bg: #000;", "--glow: rgba(1, 2, 3, 0.5); --star: 1, 2, 3; --bg: #111;")),
    [
      "--glow: rgba(1, 2, 3, 0.5) is a colour; move it into every palette",
      "--star: 1, 2, 3 is a colour; move it into every palette",
      "--bg is declared as both a palette token and a scale",
      "--bg: #111 is a colour; move it into every palette",
    ],
  );
  assert.deepEqual(lintThemeTokens(":root { --bg: #000; }"), [
    'expected exactly one default palette block (:root, [data-theme="…"])',
    'expected at least one more theme palette block ([data-theme="…"])',
  ]);
  assert.deepEqual(
    lintThemeTokens(css("--bg: #000;", "--bg: #fff;", "--bg: #000;", "--space-1: 4px;") + '\n[data-theme="light"] { --bg: #eee; }'),
    ['theme "light" has more than one palette block'],
  );
});

test("the shipped palettes are Viber Coder by default, then Light, Dark and Vesta", async () => {
  assert.deepEqual(paletteIds(await readFile(new URL("../design-tokens.css", import.meta.url), "utf8")), [
    "viber-coder",
    "light",
    "dark",
    "vesta",
  ]);
});

test("the Dark palette has no colour: every value it paints with is a grey", async () => {
  const css = (await readFile(new URL("../design-tokens.css", import.meta.url), "utf8")).replace(/\/\*[\s\S]*?\*\//g, "");
  const block = css.match(/\[data-theme="dark"\]\s*\{([^}]*)\}/);
  assert.ok(block, "Dark palette block");
  const coloured = [];
  for (const [, name, value] of block[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    const channels = [];
    for (const [, hex] of value.matchAll(/(?:#|%23)([0-9a-f]{6}|[0-9a-f]{3})\b/gi)) {
      const full = hex.length === 3 ? hex.replace(/./g, "$&$&") : hex;
      channels.push([0, 2, 4].map((index) => parseInt(full.slice(index, index + 2), 16)));
    }
    for (const [, r, g, b] of value.matchAll(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/g)) channels.push([+r, +g, +b]);
    const bare = value.trim().match(/^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$/);
    if (bare) channels.push([+bare[1], +bare[2], +bare[3]]);
    for (const [r, g, b] of channels) {
      if (Math.max(r, g, b) - Math.min(r, g, b) > 0) coloured.push(`${name}: ${value.trim()}`);
    }
  }
  assert.deepEqual(coloured, []);
});

test("script lint rejects colours hard-coded into rendered UI", () => {
  assert.deepEqual(
    lintScriptColours(
      'const a = { claude: "#e0937a" };\nel.innerHTML = \'<span style="color:#06160f">\';\nctx.fillStyle = "rgba(0, 0, 0, 0.5)";',
    ),
    ['"#e0937a"', "color:#06160f", "rgba(0"],
  );
  assert.deepEqual(
    lintScriptColours(
      '// #229 is an issue, not a colour\n/* #fff in a comment */\nconst a = "var(--claude)"; $("#input"); fetch("http://x/#abc"); ctx.fillStyle = "rgba(" + colour + ",0)";',
    ),
    [],
  );
});

test("the shipped web UI keeps the light-mode contract", async () => {
  const web = new URL("../", import.meta.url);
  const read = (name) => readFile(new URL(name, web), "utf8");
  assert.deepEqual(lintColours(await read("styles.css")), []);
  assert.deepEqual(lintThemeTokens(await read("design-tokens.css")), []);
  for (const name of (await readdir(web)).filter((file) => file.endsWith(".js"))) {
    assert.deepEqual(lintScriptColours(await read(name)), [], name);
  }
});
