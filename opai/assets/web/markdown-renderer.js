(function installOPaiMarkdown(root) {
  "use strict";

  const escapeHtml = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

  const fallback = (source) => `<p>${escapeHtml(source).replace(/\r?\n/g, "<br>\n")}</p>`;
  if (typeof root.markdownit !== "function") {
    root.OPaiMarkdown = Object.freeze({ available: false, render: fallback });
    return;
  }

  const parser = root.markdownit({
    breaks: true,
    html: false,
    linkify: false,
    maxNesting: 20,
    typographer: false,
  });
  const externalUrl = /^https?:\/\/[^\u0000-\u0020\u007f]+$/i;

  parser.core.ruler.after("inline", "opai_safe_links", (state) => {
    state.tokens.forEach((block) => {
      if (!Array.isArray(block.children)) return;
      const linkStack = [];
      block.children.forEach((token) => {
        if (token.type === "link_open") {
          const allowed = externalUrl.test(token.attrGet("href") || "");
          linkStack.push(allowed);
          token.meta = { ...(token.meta || {}), opaiExternal: allowed };
          if (allowed) token.attrSet("data-ext", "1");
        } else if (token.type === "link_close") {
          token.meta = { ...(token.meta || {}), opaiExternal: linkStack.pop() === true };
        }
      });
    });
  });

  parser.renderer.rules.link_open = (tokens, index, options, env, renderer) => (
    tokens[index].meta && tokens[index].meta.opaiExternal
      ? renderer.renderToken(tokens, index, options)
      : ""
  );
  parser.renderer.rules.link_close = (tokens, index, options, env, renderer) => (
    tokens[index].meta && tokens[index].meta.opaiExternal
      ? renderer.renderToken(tokens, index, options)
      : ""
  );
  parser.renderer.rules.image = (tokens, index) => escapeHtml(tokens[index].content || "");

  function incompleteFenceOffset(source) {
    let offset = 0;
    let open = null;
    for (const line of source.split("\n")) {
      const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (match) {
        const marker = match[1];
        if (!open) {
          if (marker[0] !== "`" || !match[2].includes("`")) {
            open = { character: marker[0], length: marker.length, offset };
          }
        } else if (
          marker[0] === open.character
          && marker.length >= open.length
          && match[2].trim() === ""
        ) {
          open = null;
        }
      }
      offset += line.length + 1;
    }
    return open ? open.offset : -1;
  }

  function render(source, options) {
    const text = String(source == null ? "" : source).replace(/\r\n?/g, "\n");
    const streaming = Boolean(options && options.streaming);
    const incompleteAt = streaming ? incompleteFenceOffset(text) : -1;
    if (incompleteAt < 0) return parser.render(text);

    const complete = text.slice(0, incompleteAt);
    const partial = text.slice(incompleteAt);
    const prefix = complete ? parser.render(complete) : "";
    return `${prefix}<p class="md-incomplete-fence">${escapeHtml(partial).replace(/\n/g, "<br>\n")}</p>`;
  }

  root.OPaiMarkdown = Object.freeze({ available: true, render });
}(typeof globalThis !== "undefined" ? globalThis : window));
