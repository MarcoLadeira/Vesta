// An app-wide check that a theme is actually being worn.
//
// The token lint proves every colour in styles.css comes from a token. This
// proves what reaches the screen: it walks every visible element that draws
// text and measures that text against the background really behind it, and it
// looks for neutral surfaces of the wrong polarity -- a near-black code well in
// a light theme, a near-white card in a dark one. Either is what a
// component that bypasses the palette looks like, however it was written
// (a hard-coded style attribute, a canvas, a colour computed in a script).
//
// Run in the page. The result lists offenders; an empty list is a pass.

/* eslint-disable no-undef */
export function auditThemeInPage({ minContrast, colourless = false }) {
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  const cache = new Map();

  // Any CSS colour the engine can paint, in sRGB, by painting it: computed
  // styles can come back as oklab() or color(srgb ...) after a color-mix().
  function rgba(css) {
    if (cache.has(css)) return cache.get(css);
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = "rgba(0, 0, 0, 0)";
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
    const value = { r, g, b, a: a / 255 };
    cache.set(css, value);
    return value;
  }

  const over = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    const mix = (channel) => (top[channel] * top.a + bottom[channel] * bottom.a * (1 - top.a)) / a;
    return { r: mix("r"), g: mix("g"), b: mix("b"), a };
  };

  const luminance = ({ r, g, b }) => {
    const linear = (value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
  };

  const contrast = (first, second) => {
    const [light, dark] = [luminance(first), luminance(second)].sort((x, y) => y - x);
    return (light + 0.05) / (dark + 0.05);
  };

  const chroma = ({ r, g, b }) => Math.max(r, g, b) - Math.min(r, g, b);

  const COLOUR = /(?:rgba?|hsla?|hwb|oklab|oklch|lab|lch|color)\([^()]*\)|#[0-9a-f]{3,8}\b/gi;

  // Every ancestor's paint, bottom-up. A gradient contributes the average of
  // its stops, which is exact for the flat tints this UI uses and close enough
  // for a pass/fail threshold on the rest. The body's sky washes are left out:
  // they are a few percent strong and cover the whole window.
  function backgroundBehind(element) {
    const chain = [];
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) chain.push(node);
    let paint = { r: 255, g: 255, b: 255, a: 1 };
    for (const node of chain.reverse()) {
      const style = getComputedStyle(node);
      let layer = rgba(style.backgroundColor);
      const image = style.backgroundImage;
      if (image && image !== "none" && !image.includes("url(") && node !== document.body) {
        const stops = (image.match(COLOUR) || []).map(rgba);
        if (stops.length) {
          const average = stops.reduce(
            (sum, stop) => ({ r: sum.r + stop.r, g: sum.g + stop.g, b: sum.b + stop.b, a: sum.a + stop.a }),
            { r: 0, g: 0, b: 0, a: 0 },
          );
          const n = stops.length;
          layer = over({ r: average.r / n, g: average.g / n, b: average.b / n, a: average.a / n }, layer);
        }
      }
      paint = over(layer, paint);
    }
    return paint;
  }

  const describe = (element) =>
    element.tagName.toLowerCase() +
    (element.id ? `#${element.id}` : "") +
    (typeof element.className === "string" && element.className.trim()
      ? "." + element.className.trim().split(/\s+/).join(".")
      : "");

  const visible = (element) => {
    const box = element.getBoundingClientRect();
    if (box.width * box.height < 4) return false;
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility !== "visible") return false;
      // Faded on purpose -- disabled, dimmed, revealed on hover -- is exempt.
      if (parseFloat(style.opacity) < 0.99) return false;
    }
    return true;
  };

  const root = document.documentElement;
  const theme = root.dataset.theme || "dark";
  const lowContrast = [];
  const wrongSurfaces = [];
  // For a colourless theme: any text, fill or border painted with a hue.
  const hues = [];

  // Whether a palette is a light one is read from its own ground, not from its
  // name, so every light theme -- Light, Vesta, whatever comes next -- is held
  // to the light polarity without this audit having to know it exists.
  const lightPalette = new Map();
  const isLight = (scope) => {
    const key = scope.dataset.theme || "";
    if (!lightPalette.has(key)) {
      const ground = rgba(getComputedStyle(scope).getPropertyValue("--bg").trim());
      lightPalette.set(key, luminance(ground) > 0.5);
    }
    return lightPalette.get(key);
  };

  for (const element of document.body.querySelectorAll("*")) {
    // A subtree that deliberately wears the other theme (the picker's
    // previews) is measured against its own palette, not the window's.
    const scope = element.closest("[data-theme]");
    const palette = scope && scope !== root ? scope : root;
    if (element.closest("[aria-hidden='true'], .composer-native, script, style, noscript, option")) continue;
    if (!visible(element)) continue;

    const style = getComputedStyle(element);
    const ownText = Array.from(element.childNodes)
      .filter((node) => node.nodeType === 3)
      .map((node) => node.textContent)
      .join("")
      .trim();

    if (ownText && !element.closest(":disabled, [aria-disabled='true']")) {
      const fill = style.webkitTextFillColor ? rgba(style.webkitTextFillColor) : null;
      if (!fill || fill.a > 0) {
        const background = backgroundBehind(element);
        const text = over(rgba(style.color), background);
        const ratio = contrast(text, background);
        if (ratio < minContrast) {
          lowContrast.push({
            element: describe(element),
            text: ownText.slice(0, 48),
            ratio: Math.round(ratio * 100) / 100,
            color: style.color,
          });
        }
      }
    }

    if (colourless && (!scope || scope === root)) {
      for (const [property, value] of [
        ["color", ownText ? style.color : "transparent"],
        ["background-color", style.backgroundColor],
        ["border-color", style.borderTopWidth !== "0px" ? style.borderTopColor : "transparent"],
      ]) {
        const paint = rgba(value);
        if (paint.a > 0.05 && chroma(paint) > 12) {
          hues.push({ element: describe(element), property, value });
          break;
        }
      }
    }

    const own = rgba(style.backgroundColor);
    const box = element.getBoundingClientRect();
    if (own.a >= 0.5 && box.width * box.height >= 900) {
      const surface = backgroundBehind(element);
      const lum = luminance(surface);
      const neutral = chroma(surface) < 45;
      const wrong = isLight(palette) ? lum < 0.1 : lum > 0.75;
      if (neutral && wrong) {
        wrongSurfaces.push({
          element: describe(element),
          background: style.backgroundColor,
          luminance: Math.round(lum * 1000) / 1000,
        });
      }
    }
  }

  return { theme, lowContrast, wrongSurfaces, hues };
}
