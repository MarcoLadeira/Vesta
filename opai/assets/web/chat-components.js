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
    var label = String(value.label || "Vesta");
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
      ';color:var(--avatar-ink)">' + esc(model.initial) + "</span>";
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
        // Field values arrive as identifiers ("not_applicable"); the chip is
        // read by a person, not matched by a parser.
        '<span class="evidence-value">' + esc(item.value.replace(/_/g, " ")) + "</span></span>";
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

  /* ---------- the turn summary ----------
   *
   * One line, and everything else behind it.
   *
   * A finished turn used to stack up to eight blocks under the answer: an
   * evidence bar, a verification table, a changes card, a work log, a workflow
   * card, warnings, a cost receipt and a completion verdict. Several of them
   * said the same thing -- the verdict alone appeared three times, as the
   * workflow card's heading, as the cost receipt's prefix, and as its own card
   * -- and the result was unreadable precisely because nothing in it was
   * ranked.
   *
   * The ranking is the fix. After a turn there are four questions worth
   * answering on sight: did it work, what changed, what did it cost, and what
   * do I do now. Everything else is diagnostics: wanted occasionally, and
   * never wanted all at once.
   *
   * So the four live on one row and the diagnostics live behind it. The row
   * is adaptive -- a read-only answer that changed nothing shows a verdict and
   * a price, not four empty columns -- which is what keeps it a sentence
   * rather than a dashboard.
   */
  var VERDICT_WORDS = {
    completed: "Done",
    // "Partial" is jargon for a specific, checkable thing: the model said it
    // edited something and no diff evidence agrees. Say that.
    partial: "No changes made",
    blocked: "Blocked",
    failed: "Failed",
    cancelled: "Stopped",
    timeout: "Timed out",
    needs_attention: "Needs attention",
  };

  function turnSummaryModel(presentation, options) {
    var extra = record(options) || {};
    var model = presentationModel(presentation);
    // Two sources carry the verdict and a turn may use either: the structured
    // presentation's `run`, and the result-level `completion_verdict` that
    // predates it. Reading only the first made a partial run whose verdict
    // lives in the second render as plain "Answered" -- a failure quietly
    // relabelled as a success, which is the one thing this row must never do.
    var verdict = completionVerdictModel(presentation) || record(extra.verdict);
    var facts = [];
    var value;

    var changes = model && record(model.changes);
    var summary = changes && record(changes.summary);
    var files = summary ? boundedCount(summary.files) : null;
    if (files === null && boundedCount(extra.changedFiles) !== null) {
      files = boundedCount(extra.changedFiles);
    }
    if (files) facts.push(files + (files === 1 ? " file" : " files"));

    var tests = model && record(model.tests);
    if (tests) {
      var failed = boundedCount(tests.failed);
      var passed = boundedCount(tests.passed);
      // Failures first: a run with one failure and forty passes is a failing
      // run, and leading with "40 tests passed" would be true and misleading.
      if (failed) facts.push(failed + (failed === 1 ? " test failed" : " tests failed"));
      else if (passed) facts.push(passed + (passed === 1 ? " test passed" : " tests passed"));
    }

    value = Number(extra.costUsd);
    if (isFinite(value) && value > 0) facts.push("$" + value.toFixed(value < 0.01 ? 4 : 2));

    // Elapsed, but not "00:00": a turn that finished inside the clock's
    // resolution has nothing to report, and a zero on the row is noise
    // dressed as a fact.
    value = boundedText(extra.elapsed, 24);
    if (value && !/^0+[:0]*$/.test(value.replace(/[^0-9:]/g, ""))) facts.push(value);

    var state = verdict ? boundedText(verdict.state || verdict.verdict, 64).toLowerCase() : "";
    return {
      state: state || "answered",
      label: state
        ? (boundedText(verdict.displayLabel, 120) ||
           VERDICT_WORDS[state] || boundedText(verdict.label, 120) || state)
        : "Answered",
      reason: verdict ? boundedText(verdict.reason, 800) : "",
      // A next action can come from the verdict or from the workflow, and a
      // turn may carry one without the other. Reading only the verdict's lost
      // "Inspect PR checks" on every workflow-only turn -- the same way the
      // reason vanished when the verdict card was folded away.
      nextAction: (verdict && boundedText(verdict.nextAction || verdict.next_action, 500)) ||
        boundedText(extra.nextAction, 500),
      facts: facts,
      retryable: extra.retryable === true &&
        ["failed", "partial", "timeout"].indexOf(state) >= 0,
    };
  }

  function renderTurnSummary(presentation, detailHtml, options) {
    var model = turnSummaryModel(presentation, options);
    var detail = String(detailHtml || "");
    if (!detail) return "";
    var facts = model.facts.length
      ? '<span class="ts-facts">' + esc(model.facts.join(" · ")) + "</span>"
      : "";
    // The one action worth reaching without expanding anything. Inside the
    // summary it would toggle the disclosure on its way to the handler, so it
    // is marked for the click wiring to stop.
    var retry = model.retryable
      ? '<button class="ts-retry" type="button" data-a="retry" data-stop-toggle="1">Retry</button>'
      : "";
    // The reason and the next action are the summary's own, not a card's.
    //
    // They used to live on the completion-verdict card, and folding that card
    // away took them with it -- on a turn with no workflow card to fall back
    // to, "no changed-file or diff evidence verifies the requested edit"
    // simply vanished. Owning them here means they appear exactly once and
    // always, whatever else the turn happens to carry.
    var headline = "";
    if (model.reason) headline += '<p class="ts-reason">' + esc(model.reason) + "</p>";
    if (model.nextAction) {
      headline += '<p class="ts-next"><span>Next</span> ' + esc(model.nextAction) + "</p>";
    }
    return '<details class="turn-summary is-' + esc(model.state) + '">' +
      '<summary class="ts-row">' +
      '<span class="ts-dot" aria-hidden="true"></span>' +
      '<span class="ts-verdict">' + esc(model.label) + "</span>" +
      facts + retry +
      '<span class="ts-more" aria-hidden="true">Details</span>' +
      "</summary>" +
      '<div class="ts-detail">' + headline + detail + "</div>" +
      "</details>";
  }

  function renderAssistantPresentation(options) {
    var value = options || {};
    var density = normalizeResponseDensity(value.density);
    var structured = presentationModel(value.presentation);
    // The answer stays where it is. Everything that describes the *run* goes
    // behind the one-line summary, which is the only part of this that is
    // shown by default.
    //
    // The standalone completion verdict is gone rather than nested: it is the
    // summary's first word now, and rendering it twice is how the old stack
    // managed to say "Partially completed" three times in one turn.
    // The split is between the work and the record of the work.
    //
    // Visible: the answer, a warning that contradicts it, the files that
    // changed, and anything the user acts on -- a changeset to approve, a plan
    // to edit, a build result to open. Those are the task, not a report about
    // the task, and burying a review behind a disclosure would make reviewing
    // the harder path.
    //
    // Behind the summary: evidence bars, verification tables, the work log,
    // the workflow card, the cost receipt. Each is worth having and none is
    // worth reading every time.
    var content = String(value.proseHtml || "");
    var detail = "";
    if (structured) {
      detail += renderEvidenceBar(value.presentation);
      detail += renderVerificationDetails(value.result, { density: density });
      detail += renderWorkLog(value.presentation, { density: density }) ||
        String(value.legacyWorkHtml || "");
      detail += String(value.supportHtml || "") + String(value.extraHtml || "");
      detail += renderWarnings(value.result, value.presentation);
    } else {
      detail += renderVerificationDetails(value.result, { density: density });
      detail += String(value.legacyWorkHtml || value.legacyBeforeHtml || "");
      detail += String(value.supportHtml || "") + String(value.extraHtml || "");
      // The verdict card is gone from here too. Folding the structured one
      // into the summary row while leaving the legacy one inside the panel is
      // how the same sentence still managed to appear three times: as the
      // workflow card's message, as the cost strip's prefix, and as its own
      // card -- with its next action repeated under both.
      detail += renderWarnings(value.result, value.presentation);
    }
    // One thing does not go behind the disclosure. An unverified-claim banner
    // says the answer just above it disagrees with the measured result -- it is
    // a warning *about the prose*, not a diagnostic about the run, and a
    // contradiction the reader has to click to discover is worse than no
    // contradiction detected at all.
    content += String(value.warningsHtml || value.prefixHtml || "");
    content += String(value.changesHtml || "");
    content += String(value.outsideHtml || "");
    content += renderTurnSummary(value.presentation, detail, {
      verdict: value.verdict,
      nextAction: value.nextAction,
      costUsd: value.costUsd,
      elapsed: value.elapsed,
      changedFiles: value.changedFiles,
      retryable: value.retryable === true,
    });
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
    turnSummaryModel: turnSummaryModel,
    renderTurnSummary: renderTurnSummary,
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
