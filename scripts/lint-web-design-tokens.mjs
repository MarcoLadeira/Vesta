import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const DECLARATION = /\b(font-size|margin(?:-[\w-]+)?|padding(?:-[\w-]+)?|gap|row-gap|column-gap)\s*:\s*([^;}]+);/g;
const TYPE_TOKENS = ["caption", "label", "body", "prose", "body-lg", "heading", "title", "display"];
const SPACING_TOKENS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "14", "15", "16"];

const hasToken = (value, prefix, names) => names.some((name) => value.includes(`var(--${prefix}-${name})`));

export function lintCss(css) {
  const violations = [];
  for (const match of css.matchAll(DECLARATION)) {
    const [, property, value] = match;
    const trimmed = value.trim();
    if (property === "font-size" && trimmed.includes("var(--type-") && !hasToken(trimmed, "type", TYPE_TOKENS)) {
      violations.push(`${property}: ${trimmed}`);
      continue;
    }
    if (property !== "font-size" && trimmed.includes("var(--space-") && !hasToken(trimmed, "space", SPACING_TOKENS)) {
      violations.push(`${property}: ${trimmed}`);
      continue;
    }
    if (trimmed === "0" || !/\b\d+(?:\.\d+)?px\b/.test(trimmed)) continue;
    // A valid token cannot bless a second, raw value in the same declaration.
    violations.push(`${property}: ${trimmed}`);
  }
  return violations;
}

/* ---------- colour: the light-mode contract ----------
 *
 * Light mode works app-wide only while every colour a component paints comes
 * from a token the light palette redefines. These checks keep it that way for
 * code nobody has written yet: a raw colour in component CSS, a palette token
 * with no light value, or a colour hard-coded into a script that renders UI is
 * rejected here, before it can ship as a dark island inside a light app.
 */

// Every CSS named colour. `transparent` and `currentColor` are not colours of
// a theme, so they are deliberately absent.
const NAMED_COLOURS = new Set(
  (
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown " +
    "burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan " +
    "darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid " +
    "darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet " +
    "deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro " +
    "ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki " +
    "lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow " +
    "lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray " +
    "lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine " +
    "mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen " +
    "mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace " +
    "olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred " +
    "papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue " +
    "saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey " +
    "snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow " +
    "yellowgreen"
  ).split(" "),
);

const HEX_COLOUR = /#[0-9a-f]{3,8}\b/i;
// A colour function whose channels are literal numbers. `rgba(var(--tint-*-rgb), a)`
// takes its colour from a token and is the sanctioned way to write a tint.
const LITERAL_COLOUR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(\s*(?!var\()[^)]*\d/i;
const BARE_CHANNELS = /^\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*$/;
// Masks read alpha only, so the colour written in one is not a theme colour.
const ALPHA_ONLY_PROPERTIES = new Set(["mask", "mask-image", "-webkit-mask", "-webkit-mask-image"]);

const stripCssComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, " "));
// url(...) holds bytes, not paint: an inline SVG may say stroke='black' for a mask.
const stripUrls = (value) => value.replace(/url\((?:"[^"]*"|'[^']*'|[^)]*)\)/g, "url()");
// Quoted text is a font name or generated content, never paint.
const stripStrings = (value) => value.replace(/"[^"]*"|'[^']*'/g, '""');
// A custom property's *name* may contain a colour word (--red, --green).
const stripVarNames = (value) => value.replace(/var\(\s*--[\w-]+/g, "var(");

export function colourLiteralIn(value) {
  const clean = stripVarNames(stripStrings(stripUrls(value)));
  const hex = clean.match(HEX_COLOUR);
  if (hex) return hex[0];
  const fn = clean.match(LITERAL_COLOUR_FUNCTION);
  if (fn) return fn[0];
  for (const word of clean.toLowerCase().match(/[a-z]+/g) || []) {
    if (NAMED_COLOURS.has(word)) return word;
  }
  return null;
}

function declarations(css) {
  const found = [];
  // URLs first: a data URI carries its own `;` and `:` that are not CSS syntax.
  const body = stripUrls(stripCssComments(css));
  for (const match of body.matchAll(/([-\w]+)\s*:\s*([^;{}]+)(?=[;}])/g)) {
    found.push({ property: match[1].toLowerCase(), value: match[2].trim() });
  }
  return found;
}

// Component CSS: no colour may be written down, only referenced.
export function lintColours(css) {
  const violations = [];
  for (const { property, value } of declarations(css)) {
    if (ALPHA_ONLY_PROPERTIES.has(property)) continue;
    if (property.startsWith("--")) {
      violations.push(`${property}: ${value} (define tokens in design-tokens.css, not in component CSS)`);
      continue;
    }
    const literal = colourLiteralIn(value);
    if (literal) violations.push(`${property}: ${value}`);
  }
  return violations;
}

function tokenBlocks(css) {
  const blocks = [];
  for (const match of stripCssComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = match[1].replace(/\s+/g, "");
    const tokens = new Map();
    for (const decl of match[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) tokens.set(decl[1], decl[2].trim());
    blocks.push({ selector, tokens });
  }
  return blocks;
}

// design-tokens.css: the light palette mirrors the dark one exactly, and the
// scales carry no colour at all.
export function lintThemeTokens(css) {
  const blocks = tokenBlocks(css);
  const pick = (selector) => blocks.filter((block) => block.selector === selector);
  const dark = pick(':root,[data-theme="dark"]');
  const light = pick('[data-theme="light"]');
  const scales = pick(":root");
  const violations = [];
  if (dark.length !== 1) violations.push('expected exactly one dark palette block (:root, [data-theme="dark"])');
  if (light.length !== 1) violations.push('expected exactly one light palette block ([data-theme="light"])');
  if (scales.length !== 1) violations.push("expected exactly one scale block (:root)");
  for (const block of blocks) {
    if (![':root,[data-theme="dark"]', '[data-theme="light"]', ":root"].includes(block.selector)) {
      violations.push(`unexpected token block ${block.selector}`);
    }
  }
  if (violations.length) return violations;

  const darkTokens = dark[0].tokens;
  const lightTokens = light[0].tokens;
  for (const name of darkTokens.keys()) {
    if (!lightTokens.has(name)) violations.push(`${name} has no light-theme value`);
  }
  for (const name of lightTokens.keys()) {
    if (!darkTokens.has(name)) violations.push(`${name} is light-only; declare it in the dark palette too`);
  }
  for (const [name, value] of scales[0].tokens) {
    if (darkTokens.has(name)) violations.push(`${name} is declared as both a palette token and a scale`);
    if (colourLiteralIn(value) || BARE_CHANNELS.test(value)) {
      violations.push(`${name}: ${value} is a colour; move it into both palettes`);
    }
  }
  return violations;
}

// Scripts that render UI: a colour belongs in a token, reached as var(--name).
export function lintScriptColours(source) {
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:\\'"`])\/\/[^\n]*/g, "$1");
  const violations = [];
  const patterns = [
    // "#e0937a", 'color:#06160f', `background: #fff`
    /["'`]#[0-9a-f]{3,8}["'`]/gi,
    /\b(?:color|background(?:-color)?|border(?:-color)?|fill|stroke|outline(?:-color)?)\s*:\s*#[0-9a-f]{3,8}\b/gi,
    /\b(?:rgba?|hsla?)\(\s*\d/gi,
  ];
  for (const pattern of patterns) {
    for (const match of code.matchAll(pattern)) violations.push(match[0]);
  }
  return violations;
}

async function main() {
  const web = new URL("../opai/assets/web/", import.meta.url);
  const read = (name) => readFile(fileURLToPath(new URL(name, web)), "utf8");
  const failures = [];
  const report = (title, violations) => {
    if (violations.length) failures.push(`${title}:\n  ${violations.join("\n  ")}`);
  };

  const stylesheet = await read("styles.css");
  report("Raw web type/spacing values must use design tokens", lintCss(stylesheet));
  report("styles.css must take every colour from a theme token", lintColours(stylesheet));
  report("design-tokens.css light and dark palettes must match", lintThemeTokens(await read("design-tokens.css")));

  const scripts = (await readdir(fileURLToPath(web))).filter((name) => name.endsWith(".js")).sort();
  for (const name of scripts) {
    report(`${name} must reference colours as var(--token)`, lintScriptColours(await read(name)));
  }

  if (!failures.length) return;
  console.error(failures.join("\n\n"));
  process.exitCode = 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
