/*
 * First-run onboarding (#250).
 *
 * A new user lands in an empty cockpit; the product's "aha" is the first
 * savings receipt. This walks there in three skippable steps and then gets out
 * of the way: connect a provider, pick a default model, run a first task that
 * ends on a rendered receipt. It reuses the real bridge paths (no parallel
 * implementations) and never bypasses the cost firewall — the first task goes
 * through the normal send(), so a paid model still asks first.
 *
 * Shown once per profile: completing or skipping persists `onboarding_seen`, so
 * returning users never see it. A "Replay tour" entry in Settings -> About
 * re-opens it on demand.
 *
 * UMD: a plain <script> in the browser (window.OPaiOnboarding) and a CommonJS
 * module under Vitest.
 */
(function (global) {
  "use strict";

  var TOTAL_STEPS = 3;
  var SUGGESTED_TASK = "Summarize my uncommitted changes";

  function _doc() {
    return typeof document !== "undefined"
      ? document
      : global && global.document;
  }

  function shouldShow(boot) {
    return !(boot && boot.prefs && boot.prefs.onboardingSeen);
  }

  function connectedCount(ctx) {
    var accounts = (ctx.boot && ctx.boot.accounts) || [];
    return accounts.filter(function (a) {
      return a && (a.connected || a.authenticated);
    }).length;
  }

  // ---- step content (HTML; dynamic values escaped via ctx.esc) ----------- #
  function connectStep(ctx) {
    var count = connectedCount(ctx);
    var status =
      count > 0
        ? '<div class="ob-status ok">' +
          ctx.esc(count + " provider" + (count === 1 ? "" : "s") + " connected") +
          "</div>"
        : '<div class="ob-status">No providers connected yet — that\'s fine, local models are free.</div>';
    return (
      '<div class="ob-eyebrow">Step 1 of 3</div>' +
      '<h2 class="ob-title">Connect a provider</h2>' +
      '<p class="ob-body">Vesta works out of the box with free local models. Connect Claude, Codex, or an API key to unlock more — your keys stay in your OS keychain and never leave this machine.</p>' +
      status +
      '<div class="ob-actions-inline"><button class="btn" type="button" data-ob="open-providers">Open Providers</button></div>'
    );
  }

  function modelStep(ctx) {
    var models = (ctx.boot && ctx.boot.models) || [];
    var current = ctx.currentModel ? ctx.currentModel() : "auto";
    var options = models
      .map(function (m) {
        return (
          '<option value="' +
          ctx.esc(m.id) +
          '"' +
          (m.id === current ? " selected" : "") +
          ">" +
          ctx.esc(m.label || m.id) +
          "</option>"
        );
      })
      .join("");
    return (
      '<div class="ob-eyebrow">Step 2 of 3</div>' +
      '<h2 class="ob-title">Pick your default model</h2>' +
      '<p class="ob-body">Vesta routes to the cheapest capable model and only uses a cloud model after you confirm. "Auto" decides for you, task by task.</p>' +
      '<label class="ob-field"><span>Default model</span><select data-ob="model">' +
      options +
      "</select></label>"
    );
  }

  // Bug 11: the first task runs against whatever project is currently open,
  // reading its real uncommitted changes. Name that project so the user knows
  // exactly what "Run this task now" will read — pure and exported so the
  // transparency contract is unit-testable.
  function firstTaskTarget(ctx) {
    var ws = (ctx && ctx.boot && ctx.boot.workspace) || {};
    var name = ws.label || ws.name || "the current project";
    var dirty = (ws.dirty_paths || []).length;
    var e = (ctx && ctx.esc) || function (v) { return String(v); };
    return (
      "This reads <b>" +
      e(name) +
      "</b>" +
      (dirty
        ? " (" + dirty + " uncommitted change" + (dirty === 1 ? "" : "s") + ")"
        : "") +
      " — it only summarizes, it never edits."
    );
  }

  function taskStep(ctx) {
    // The footer's default/primary button finishes the tour (see STEPS), not
    // runs a task, so a brand-new user reflexively clicking the primary CTA on
    // their real work folder never kicks off a run. Sending is a deliberate,
    // clearly-labelled opt-in below.
    var target = firstTaskTarget(ctx);
    return (
      '<div class="ob-eyebrow">Step 3 of 3</div>' +
      '<h2 class="ob-title">Earn your first receipt</h2>' +
      '<p class="ob-body">Try a first task whenever you\'re ready. Vesta plans it, routes it locally when it can, and hands you a receipt showing exactly what it cost — and what it saved.</p>' +
      '<div class="ob-task" data-ob="task">' +
      ctx.esc(SUGGESTED_TASK) +
      "</div>" +
      '<p class="ob-note">' +
      target +
      "</p>" +
      '<button class="btn" type="button" data-ob="send">Run this task now</button>' +
      '<p class="ob-note">Runs through the same cost firewall as always — a cloud model always asks first.</p>'
    );
  }

  var STEPS = [
    { render: connectStep, primary: null },
    { render: modelStep, primary: null },
    { render: taskStep, primary: { action: "finish", label: "Finish" } },
  ];

  // ---- overlay lifecycle ------------------------------------------------- #
  function _close() {
    var doc = _doc();
    var existing = doc.getElementById("onboarding");
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
  }

  function _seenAndClose(ctx) {
    if (ctx.markSeen) ctx.markSeen();
    _close();
  }

  function _render(ctx, index) {
    var doc = _doc();
    var overlay = doc.getElementById("onboarding");
    if (!overlay) return;
    var step = STEPS[index];
    var isLast = index === TOTAL_STEPS - 1;
    var dots = STEPS.map(function (_s, i) {
      return '<span class="ob-dot' + (i === index ? " active" : "") + '"></span>';
    }).join("");
    var footer =
      '<div class="ob-footer">' +
      (index > 0
        ? '<button class="btn ghost" type="button" data-ob="back">Back</button>'
        : '<span></span>') +
      '<div class="ob-dots" aria-hidden="true">' +
      dots +
      "</div>" +
      '<div class="ob-footer-right">' +
      '<button class="btn ghost" type="button" data-ob="skip">Skip tour</button>' +
      (step.primary
        ? '<button class="btn primary" type="button" data-ob="' +
          step.primary.action +
          '">' +
          ctx.esc(step.primary.label) +
          "</button>"
        : '<button class="btn primary" type="button" data-ob="next">Next</button>') +
      "</div></div>";
    overlay.querySelector(".ob-card").innerHTML =
      '<button class="ob-close" type="button" data-ob="skip" aria-label="Skip onboarding">' +
      window.OPaiIcons.icon("close") +
      "</button>" +
      '<div class="ob-content">' +
      step.render(ctx) +
      "</div>" +
      footer;
    _wire(ctx, index);
    var focusable = overlay.querySelector(".ob-content select, .ob-content button, .ob-footer .primary");
    if (focusable && focusable.focus) focusable.focus();
  }

  function _wire(ctx, index) {
    var doc = _doc();
    var overlay = doc.getElementById("onboarding");
    var on = function (action, fn) {
      // Wire every matching control (e.g. both the × and the footer "Skip tour").
      overlay.querySelectorAll('[data-ob="' + action + '"]').forEach(function (el) {
        el.onclick = fn;
      });
    };
    on("skip", function () {
      _seenAndClose(ctx);
    });
    on("back", function () {
      _render(ctx, Math.max(0, index - 1));
    });
    on("next", function () {
      _render(ctx, Math.min(TOTAL_STEPS - 1, index + 1));
    });
    on("open-providers", function () {
      if (ctx.openProviders) ctx.openProviders();
    });
    var model = overlay.querySelector('[data-ob="model"]');
    if (model)
      model.onchange = function () {
        if (ctx.pickModel) ctx.pickModel(model.value);
      };
    on("finish", function () {
      _seenAndClose(ctx);
    });
    on("send", function () {
      _seenAndClose(ctx);
      if (ctx.sendPrompt) ctx.sendPrompt(SUGGESTED_TASK);
    });
  }

  function _open(ctx, index) {
    var doc = _doc();
    _close();
    var overlay = doc.createElement("div");
    overlay.className = "overlay ob-overlay open";
    overlay.id = "onboarding";
    overlay.innerHTML =
      '<div class="ob-card" role="dialog" aria-modal="true" aria-label="Welcome to Vesta"></div>';
    doc.body.appendChild(overlay);
    // Escape skips the tour, matching the close button.
    overlay.addEventListener("keydown", function (e) {
      if (e.key === "Escape") _seenAndClose(ctx);
    });
    _render(ctx, index || 0);
  }

  function maybeStart(ctx) {
    if (shouldShow(ctx.boot)) _open(ctx, 0);
  }

  function replay(ctx) {
    _open(ctx, 0);
  }

  var api = {
    shouldShow: shouldShow,
    maybeStart: maybeStart,
    replay: replay,
    connectedCount: connectedCount,
    firstTaskTarget: firstTaskTarget,
    // The last step's footer primary action — "finish", never "send", so the
    // reflexive primary click can't run a task on real work (Bug 11).
    lastStepPrimaryAction: function () {
      var last = STEPS[TOTAL_STEPS - 1];
      return (last && last.primary && last.primary.action) || "";
    },
    SUGGESTED_TASK: SUGGESTED_TASK,
    TOTAL_STEPS: TOTAL_STEPS,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiOnboarding = api;
})(typeof window !== "undefined" ? window : this);
