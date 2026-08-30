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

  function record(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  }

  function boundedText(value, maximum) {
    return typeof value === "string" ? value.trim().slice(0, maximum) : "";
  }

  function boundedCount(value) {
    return Number.isSafeInteger(value) && value >= 0 ? value : null;
  }

  function presentationModel(value) {
    var source = record(value);
    if (!source || source.schema_version !== 1) return null;
    var model = {
      run: record(source.run),
      evidence: record(source.evidence),
      tests: record(source.tests),
      changes: record(source.changes),
      approval: record(source.approval),
      activity: Array.isArray(source.activity) ? source.activity.slice(-32) : [],
    };
    return model.run || model.evidence || model.tests || model.changes ||
      model.approval || model.activity.length ? model : null;
  }

  function evidenceBarModel(presentation) {
    var model = presentationModel(presentation);
    var items = [];
    if (!model) return { items: items };
    var evidence = model.evidence || {};
    var verification = record(evidence.verification);
    var delivery = record(evidence.delivery);
    var tests = model.tests;
    var changes = model.changes;
    var approval = model.approval;
    var value;

    if (verification && (value = boundedText(verification.verdict, 64))) {
      items.push({ key: "verification", label: "Verification", value: value });
    }
    if (tests && (value = boundedText(tests.status, 64))) {
      var testParts = [value];
      var passed = boundedCount(tests.passed);
      var failed = boundedCount(tests.failed);
      var skipped = boundedCount(tests.skipped);
      if (passed !== null) testParts.push(passed + " passed");
      if (failed !== null) testParts.push(failed + " failed");
      if (skipped !== null) testParts.push(skipped + " skipped");
      items.push({ key: "tests", label: "Checks", value: testParts.join(" · ") });
    }
    var summary = changes && record(changes.summary);
    if (summary) {
      var changeParts = [];
      var files = boundedCount(summary.files);
      var additions = boundedCount(summary.additions);
      var deletions = boundedCount(summary.deletions);
      if (files !== null) changeParts.push(files + (files === 1 ? " file" : " files"));
      if (additions !== null || deletions !== null) {
        changeParts.push("+" + (additions === null ? 0 : additions) +
          " −" + (deletions === null ? 0 : deletions));
      }
      if (changeParts.length) {
        items.push({ key: "changes", label: "Changes", value: changeParts.join(" · ") });
      }
    }
    if (delivery && (value = boundedText(delivery.verdict, 64))) {
      items.push({ key: "delivery", label: "Delivery", value: value });
    }
    if (approval) {
      value = boundedText(approval.state, 64) || boundedText(approval.kind, 64);
      if (value) items.push({ key: "approval", label: "Approval", value: value });
    }
    return { items: items };
  }

  function renderEvidenceBar(presentation) {
    var model = evidenceBarModel(presentation);
    if (!model.items.length) return "";
    var items = model.items.map(function (item) {
      return '<span class="evidence-item evidence-' + esc(item.key) + '">' +
        '<span class="evidence-label">' + esc(item.label) + '</span>' +
        '<span class="evidence-value">' + esc(item.value) + "</span></span>";
    }).join("");
    return '<section class="evidence-bar" aria-label="Run evidence">' + items + "</section>";
  }

  function workLogModel(presentation, density) {
    var model = presentationModel(presentation);
    var normalizedDensity = normalizeResponseDensity(density);
    var groups = [];
    if (model) {
      model.activity.forEach(function (raw) {
        var item = record(raw);
        if (!item) return;
        var row = {
          phase: boundedText(item.phase, 64),
          status: boundedText(item.status, 64),
          message: boundedText(item.message, 500),
          nextAction: boundedText(item.next_action, 500),
        };
        if (!row.phase && !row.status && !row.message && !row.nextAction) return;
        var phase = row.phase || "Work";
        var current = groups.length ? groups[groups.length - 1] : null;
        if (!current || current.phase !== phase) {
          current = { phase: phase, rows: [] };
          groups.push(current);
        }
        current.rows.push(row);
      });
    }
    return {
      density: normalizedDensity,
      expanded: normalizedDensity === "detailed",
      groups: groups,
      rowCount: groups.reduce(function (total, group) {
        return total + group.rows.length;
      }, 0),
    };
  }

  function statusClass(value) {
    return boundedText(value, 64).toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || "unknown";
  }

  function renderWorkLog(presentation, options) {
    var value = options || {};
    var model = workLogModel(presentation, value.density);
    if (!model.rowCount) return "";
    var label = "Work log (" + model.rowCount + ")";
    var groups = model.groups.map(function (group) {
      var rows = group.rows.map(function (row) {
        var message = row.message
          ? '<span class="work-log-message">' + esc(row.message) + "</span>"
          : "";
        var next = row.nextAction
          ? '<span class="work-log-next">Next: ' + esc(row.nextAction) + "</span>"
          : "";
        var status = row.status
          ? '<span class="work-log-status">' + esc(row.status) + "</span>"
          : "";
        return '<div class="work-log-row ' + statusClass(row.status) + '">' +
          status + message + next + "</div>";
      }).join("");
      var open = model.density === "compact" ? "" : " open";
      return '<details class="work-log-group"' + open + "><summary>" +
        esc(group.phase) + " (" + group.rows.length + ")</summary>" + rows + "</details>";
    }).join("");
    return '<button class="gen-toggle done" type="button" data-label="' + esc(label) +
      '" aria-expanded="' + (model.expanded ? "true" : "false") + '">' + esc(label) +
      '</button><div class="timeline done" aria-label="AI activity"' +
      (model.expanded ? "" : " hidden") + ">" + groups + "</div>";
  }

  function completionVerdictModel(presentation) {
    var model = presentationModel(presentation);
    var run = model && model.run;
    if (!run) return null;
    var states = [
      "completed", "partial", "blocked", "failed", "cancelled", "timeout",
      "needs_attention",
    ];
    var state = boundedText(run.state, 64).toLowerCase();
    if (states.indexOf(state) < 0) return null;
    return {
      state: state,
      label: boundedText(run.label, 120) || state,
      reason: boundedText(run.reason, 800),
      nextAction: boundedText(run.next_action, 500),
    };
  }

  function renderCompletionVerdict(presentation, options) {
    var model = completionVerdictModel(presentation);
    if (!model) return "";
    var next = model.nextAction
      ? '<div class="cv-next">Next: ' + esc(model.nextAction) + "</div>"
      : "";
    var retry = options && options.retryable === true &&
      ["failed", "partial", "timeout"].indexOf(model.state) >= 0
      ? '<div class="cv-actions"><button class="btn" data-a="retry">Retry</button></div>'
      : "";
    return '<section class="completion-verdict ' + esc(model.state) +
      '" role="status" aria-label="Completion verdict: ' + esc(model.label) + '">' +
      '<div class="cv-title">' + esc(model.label) + '</div>' +
      (model.reason ? '<div class="cv-reason">' + esc(model.reason) + "</div>" : "") +
      next + retry + "</section>";
  }

  function renderAssistantPresentation(options) {
    var value = options || {};
    var density = normalizeResponseDensity(value.density);
    var structured = presentationModel(value.presentation);
    var content = "";
    if (structured) {
      content += renderCompletionVerdict(value.presentation, {
        retryable: value.retryable === true,
      });
      content += String(value.prefixHtml || "") + String(value.proseHtml || "");
      content += renderEvidenceBar(value.presentation);
      content += renderWorkLog(value.presentation, { density: density });
    } else {
      content += String(value.legacyBeforeHtml || "");
      content += String(value.prefixHtml || "") + String(value.proseHtml || "");
    }
    content += String(value.extraHtml || "");
    return renderResponseShell({
      density: density,
      headerHtml: String(value.headerHtml || ""),
      contentHtml: content,
    });
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
    presentationModel: presentationModel,
    evidenceBarModel: evidenceBarModel,
    renderEvidenceBar: renderEvidenceBar,
    workLogModel: workLogModel,
    renderWorkLog: renderWorkLog,
    completionVerdictModel: completionVerdictModel,
    renderCompletionVerdict: renderCompletionVerdict,
    renderAssistantPresentation: renderAssistantPresentation,
    responseShellModel: responseShellModel,
    renderResponseShell: renderResponseShell,
  };
});
