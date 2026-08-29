(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OPaiChatComponents = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  var RESPONSE_DENSITIES = ["compact", "balanced", "detailed"];

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeResponseDensity(value) {
    return RESPONSE_DENSITIES.indexOf(value) >= 0 ? value : "balanced";
  }

  function safeColor(value) {
    var color = String(value || "");
    return /^(?:#[0-9a-f]{3,8}|var\(--[a-z0-9-]+\))$/i.test(color)
      ? color
      : "var(--muted)";
  }

  function userMessageModel(value) {
    return { text: String(value == null ? "" : value) };
  }

  function renderUserMessage(value) {
    var model = userMessageModel(value);
    return '<div class="bubble user-message__bubble">' + esc(model.text) + "</div>";
  }

  function assistantHeaderModel(options) {
    var value = options || {};
    var label = String(value.label || "OPai");
    return {
      label: label,
      initial: label.charAt(0) || "O",
      color: safeColor(value.color),
      copy: value.copy === true,
      copyIconHtml: String(value.copyIconHtml || ""),
    };
  }

  function renderAssistantHeader(options) {
    var model = assistantHeaderModel(options);
    var avatar = '<span class="av" style="background:' + model.color +
      ';color:#06160f">' + esc(model.initial) + "</span>";
    var copy = model.copy
      ? '<button class="msg-copy" type="button" data-a="copy-answer" title="Copy this response" aria-label="Copy this response">' +
        model.copyIconHtml + "</button>"
      : "";
    return '<div class="role assistant-header" style="color:' + model.color + '">' +
      avatar + esc(model.label) + copy + "</div>";
  }

  function renderProseRegion(markdownHtml) {
    return '<div class="body response-prose">' + String(markdownHtml || "") + "</div>";
  }

  function responseShellModel(options) {
    var value = options || {};
    return {
      density: normalizeResponseDensity(value.density),
      headerHtml: String(value.headerHtml || ""),
      contentHtml: String(value.contentHtml || ""),
    };
  }

  function renderResponseShell(options) {
    var model = responseShellModel(options);
    return '<div class="response-shell response-density-' + model.density +
      '" data-response-density="' + model.density + '">' + model.headerHtml +
      '<div class="response-content">' + model.contentHtml + "</div></div>";
  }

  return {
    RESPONSE_DENSITIES: RESPONSE_DENSITIES.slice(),
    normalizeResponseDensity: normalizeResponseDensity,
    userMessageModel: userMessageModel,
    renderUserMessage: renderUserMessage,
    assistantHeaderModel: assistantHeaderModel,
    renderAssistantHeader: renderAssistantHeader,
    renderProseRegion: renderProseRegion,
    responseShellModel: responseShellModel,
    renderResponseShell: renderResponseShell,
  };
});
