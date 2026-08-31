(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.OPaiChatComponents = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  var RESPONSE_DENSITIES = ["compact", "balanced", "detailed"];
  var workLogSequence = 0;

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
    var logId = "work-log-" + (++workLogSequence);
    return '<button class="gen-toggle done" type="button" data-label="' + esc(label) +
      '" aria-controls="' + logId + '" aria-expanded="' + (model.expanded ? "true" : "false") + '">' + esc(label) +
      '</button><div class="timeline done" id="' + logId + '" role="log" aria-label="AI activity"' +
      (model.expanded ? "" : " hidden") + ">" + groups + "</div>";
  }

  function verificationDetailsModel(result) {
    var source = record(result);
    var manifest = source && record(source.verification_manifest);
    var rawChecks = manifest && Array.isArray(manifest.checks) ? manifest.checks : [];
    var checks = [];
    var counts = { passed: 0, failed: 0, skipped: 0, unverified: 0, total: 0 };
    rawChecks.slice(0, 100).forEach(function (rawCheck) {
      var check = record(rawCheck);
      if (!check) return;
      var status = boundedText(check.status, 64).toLowerCase() || "unknown";
      var attempts = [];
      if (Array.isArray(check.attempts)) {
        check.attempts.slice(-5).forEach(function (rawAttempt, offset) {
          var attempt = record(rawAttempt);
          if (!attempt) return;
          var command = Array.isArray(attempt.command)
            ? attempt.command.slice(0, 50).map(function (part) {
              return boundedText(part, 500);
            }).filter(Boolean)
            : [];
          var commandText = command.map(function (part) {
            return /^[A-Za-z0-9_./:\\=-]+$/.test(part) ? part : JSON.stringify(part);
          }).join(" ");
          var started = Date.parse(boundedText(attempt.started_at, 64));
          var ended = Date.parse(boundedText(attempt.ended_at, 64));
          var durationMs = Number.isFinite(started) && Number.isFinite(ended) && ended >= started
            ? Math.min(ended - started, 86_400_000)
            : null;
          attempts.push({
            index: boundedCount(attempt.index) || offset + 1,
            status: boundedText(attempt.status, 64).toLowerCase() || status,
            commandText: commandText,
            durationMs: durationMs,
            exitStatus: Number.isSafeInteger(attempt.exit_status) ? attempt.exit_status : null,
            outputSummary: boundedText(attempt.output_summary, 2_000),
            teardownVerified: attempt.teardown_verified === true,
          });
        });
      }
      checks.push({
        id: boundedText(check.check_id, 120),
        kind: boundedText(check.kind, 64) || "check",
        requirement: boundedText(check.requirement, 500),
        status: status,
        flakeSuspected: check.flake_suspected === true,
        attempts: attempts,
      });
      counts.total += 1;
      if (status === "passed") counts.passed += 1;
      else if (status === "skipped" || status === "waived") counts.skipped += 1;
      else if (["failed", "timeout", "cancelled"].indexOf(status) >= 0) counts.failed += 1;
      else counts.unverified += 1;
    });
    var issues = [];
    if (manifest) {
      var creationError = boundedText(manifest.creation_error, 500);
      if (creationError) issues.push(creationError);
      if (Array.isArray(manifest.integrity_errors)) {
        manifest.integrity_errors.slice(0, 16).forEach(function (value) {
          var issue = boundedText(value, 500);
          if (issue && issues.indexOf(issue) < 0) issues.push(issue);
        });
      }
    }
    return { checks: checks, counts: counts, issues: issues };
  }

  function durationLabel(value) {
    if (!Number.isFinite(value) || value < 0) return "";
    if (value < 1_000) return Math.round(value) + " ms";
    return (value / 1_000).toFixed(value < 10_000 ? 2 : 1).replace(/\.0+$/, "") + " s";
  }

  function renderVerificationDetails(result, options) {
    var density = normalizeResponseDensity(options && options.density);
    var model = verificationDetailsModel(result);
    if (!model.checks.length && !model.issues.length) return "";
    var summary = [];
    if (model.counts.passed) summary.push(model.counts.passed + " passed");
    if (model.counts.failed) summary.push(model.counts.failed + " failed");
    if (model.counts.skipped) summary.push(model.counts.skipped + " skipped");
    if (model.counts.unverified) summary.push(model.counts.unverified + " not verified");
    var issues = model.issues.map(function (issue) {
      return '<div class="verification-issue">' + esc(issue) + "</div>";
    }).join("");
    var checks = model.checks.map(function (check) {
      var attempts = check.attempts.map(function (attempt) {
        var facts = [];
        if (attempt.exitStatus !== null) facts.push("Exit " + attempt.exitStatus);
        if (attempt.durationMs !== null) facts.push(durationLabel(attempt.durationMs));
        if (attempt.teardownVerified) facts.push("Teardown verified");
        var command = attempt.commandText
          ? '<div class="verification-command"><div class="verification-command-head"><span>Command</span><button type="button" class="btn ghost" data-copy-command>Copy</button></div><code>' +
            esc(attempt.commandText) + "</code></div>"
          : "";
        var output = attempt.outputSummary
          ? '<details class="verification-output"><summary>Output summary</summary><pre>' +
            esc(attempt.outputSummary) + "</pre></details>"
          : "";
        return '<div class="verification-attempt"><div class="verification-attempt-head"><span>Attempt ' +
          esc(attempt.index) + '</span><span>' + esc(facts.join(" · ")) + "</span></div>" +
          command + output + "</div>";
      }).join("");
      return '<details class="verification-check ' + esc(statusClass(check.status)) + '"' +
        (density === "detailed" ? " open" : "") + '><summary>' +
        '<span class="verification-kind">' + esc(check.kind) + '</span><span class="verification-requirement">' +
        esc(check.requirement || check.id || "Verification check") + '</span><span class="verification-status">' +
        esc(check.status) + "</span></summary>" + attempts + "</details>";
    }).join("");
    return '<section class="verification-card" aria-label="Verification checks"><div class="verification-head"><span>Verification</span><span>' +
      esc(summary.join(" · ") || "Not verified") + "</span></div>" + issues + checks + "</section>";
  }

  function warningModel(result, presentation) {
    var source = record(result) || {};
    var structured = presentationModel(presentation);
    var items = [];
    var seen = Object.create(null);
    function add(severity, message) {
      var clean = boundedText(message, 800);
      if (!clean) return;
      var key = clean.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      items.push({
        severity: ["error", "warning", "info"].indexOf(severity) >= 0 ? severity : "warning",
        message: clean,
      });
    }
    if (Array.isArray(source.warnings)) {
      source.warnings.slice(0, 32).forEach(function (raw) {
        var warning = record(raw);
        if (!warning) return;
        var reason = boundedText(warning.reason, 700);
        var term = boundedText(warning.term, 100);
        add(boundedText(warning.severity, 32).toLowerCase(), reason + (reason && term ? " · " : "") + term);
      });
    }
    var manifest = record(source.verification_manifest);
    if (manifest && Array.isArray(manifest.integrity_errors)) {
      manifest.integrity_errors.slice(0, 16).forEach(function (issue) {
        add("error", issue);
      });
    }
    if (structured && structured.run && structured.run.answer_conflicts === true) {
      add("warning", "The answer conflicts with the measured run result.");
    }
    var workflow = record(source.workflow);
    if (workflow) {
      if (Array.isArray(workflow.blockers)) {
        workflow.blockers.slice(0, 16).forEach(function (blocker) {
          add("warning", blocker);
        });
      }
      add("warning", workflow.blocker);
      var gates = record(workflow.safety_gates);
      if (gates && Array.isArray(gates.failed)) {
        gates.failed.slice(0, 16).forEach(function (gate) {
          var label = boundedText(gate, 300);
          if (label) add("warning", "Safety gate failed: " + label);
        });
      }
    }
    var review = workflow && record(workflow.diff_review);
    var changeSummary = review && record(review.summary);
    if (changeSummary && changeSummary.truncated === true) {
      add("warning", "The diff evidence is truncated.");
    }
    var risky = changeSummary && boundedCount(changeSummary.risky);
    if (risky) add("warning", risky + (risky === 1 ? " risky file requires" : " risky files require") + " review.");
    var background = record(source.background_work);
    var unfinished = background && Array.isArray(background.unfinished) ? background.unfinished.length : 0;
    if (unfinished) add("warning", unfinished + (unfinished === 1 ? " background command is" : " background commands are") + " still unfinished.");
    return { items: items };
  }

  function renderWarnings(result, presentation) {
    var model = warningModel(result, presentation);
    if (!model.items.length) return "";
    var rows = model.items.map(function (item) {
      return '<div class="response-warning ' + esc(item.severity) + '"><span class="warning-title">' +
        esc(item.severity === "error" ? "Error" : item.severity === "info" ? "Note" : "Warning") +
        '</span><span class="warning-message">' + esc(item.message) + "</span></div>";
    }).join("");
    return '<section class="response-warnings" aria-label="Warnings">' + rows + "</section>";
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
      content += String(value.proseHtml || "");
      content += renderEvidenceBar(value.presentation);
      content += renderVerificationDetails(value.result, { density: density });
      content += String(value.changesHtml || "");
      content += renderWorkLog(value.presentation, { density: density }) ||
        String(value.legacyWorkHtml || "");
      content += String(value.supportHtml || "") + String(value.extraHtml || "");
      content += renderWarnings(value.result, value.presentation);
      content += String(value.warningsHtml || value.prefixHtml || "");
      content += renderCompletionVerdict(value.presentation, {
        retryable: value.retryable === true,
      }) || String(value.legacyFinalHtml || "");
    } else {
      content += String(value.proseHtml || "");
      content += renderVerificationDetails(value.result, { density: density });
      content += String(value.changesHtml || "");
      content += String(value.legacyWorkHtml || value.legacyBeforeHtml || "");
      content += String(value.supportHtml || "") + String(value.extraHtml || "");
      content += renderWarnings(value.result, value.presentation);
      content += String(value.warningsHtml || value.prefixHtml || "");
      content += String(value.legacyFinalHtml || "");
    }
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
    verificationDetailsModel: verificationDetailsModel,
    renderVerificationDetails: renderVerificationDetails,
    warningModel: warningModel,
    renderWarnings: renderWarnings,
    completionVerdictModel: completionVerdictModel,
    renderCompletionVerdict: renderCompletionVerdict,
    renderAssistantPresentation: renderAssistantPresentation,
    responseShellModel: responseShellModel,
    renderResponseShell: renderResponseShell,
  };
});
