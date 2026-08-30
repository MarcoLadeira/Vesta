import { readFile } from "node:fs/promises";
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

async function main() {
  const stylesheet = fileURLToPath(new URL("../opai/assets/web/styles.css", import.meta.url));
  const violations = lintCss(await readFile(stylesheet, "utf8"));
  if (!violations.length) return;
  console.error("Raw web type/spacing values must use design tokens:\n" + violations.join("\n"));
  process.exitCode = 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
