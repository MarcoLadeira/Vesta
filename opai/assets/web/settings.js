/*
 * OPai settings surface (#217) — extracted from app.js in #236.
 *
 * A section registry drives both the left navigation rail and the rendered
 * pages, so adding a settings page is a registry entry here, not an edit to
 * app.js. Section markup (class names, ids, data-attributes) is what the
 * settings e2e specs and event wiring depend on.
 *
 * Layout is Claude-style paned navigation (owner direction): the rail is real
 * page navigation — one cleanly labelled page visible at a time. Search (#240)
 * stays global: typing switches into a cross-page results mode where every page
 * shows only its matching blocks under its page label; clearing the query
 * returns to the active page. Deep links (`#settings/<id>`) open that page.
 *
 * UMD: a plain <script> in the browser (sets window.OPaiSettings) and a CommonJS
 * module under Vitest (el() is unit-tested with an injected document).
 */
(function (global) {
  "use strict";

  function _doc() {
    if (typeof document !== "undefined") return document;
    return typeof globalThis !== "undefined" ? globalThis.document : undefined;
  }

  // el(tag, attrs, children): a safe DOM builder. Dynamic values go through
  // textContent / setAttribute / addEventListener — never innerHTML — so a
  // section author cannot accidentally inject markup from a data value. The one
  // escape hatch is an explicit `html` attribute for static, self-authored
  // fragments (used only where the old code already concatenated trusted HTML).
  function el(tag, attrs, children) {
    var node = _doc().createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value == null || value === false) return;
        if (key === "class" || key === "className") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "html") node.innerHTML = value;
        else if (key === "dataset" && typeof value === "object") {
          Object.keys(value).forEach(function (d) {
            node.dataset[d] = value[d];
          });
        } else if (key.slice(0, 2) === "on" && typeof value === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (value === true) node.setAttribute(key, "");
        else node.setAttribute(key, value);
      });
    }
    var kids = children == null ? [] : Array.isArray(children) ? children : [children];
    kids.forEach(function (child) {
      if (child == null || child === false) return;
      if (typeof child === "string" || typeof child === "number") {
        node.appendChild(_doc().createTextNode(String(child)));
      } else {
        node.appendChild(child);
      }
    });
    return node;
  }

  // ---- section HTML builders (exact markup moved from app.js) ------------- //
  // MIRROR of opaihub/autonomy.py MODE_LABELS — the single source of truth for
  // run-mode labels (#400). JS can't import the Python module, so this copy is
  // kept in lockstep by a CI contract test (tests/test_ui_honesty_sweep.py).
  // If you rename a mode, change autonomy.py and this together.
  var MODE_LABELS = {
    ask: "Ask",
    plan: "Plan",
    "safe-auto": "Safe Auto",
    "approve-edits": "Approve Edits",
    "full-auto": "Full Auto",
  };

  function row(esc, k, v) {
    return (
      '<div class="set-row"><span class="k">' +
      esc(k) +
      '</span><span class="v">' +
      esc(v) +
      "</span></div>"
    );
  }

  // One honest sentence for the top of the Providers page (#237). Shared by
  // the render path and app.js's live updater so the wording can never drift.
  function doctorSummary(healths) {
    var attention = healths.filter(function (health) {
      return (
        ["failed", "degraded", "not_installed", "not_configured"].indexOf(health) >= 0
      );
    }).length;
    var text = !healths.length
      ? "No providers detected yet"
      : attention === 0
        ? "All " + healths.length + " connections look good"
        : attention + " of " + healths.length + " connections need attention";
    return { attention: attention, text: text };
  }

  function providersHtml(d, ctx) {
    var esc = ctx.esc;
    var connectionHealthLabel = ctx.connectionHealthLabel;
    var doctorItems = Array.isArray(d.connectionDoctor)
      ? d.connectionDoctor
      : (d.accounts || []).map(function (a) {
          return {
            providerId: a.id,
            displayName: a.label || a.id,
            kind: "account",
            health: a.connected
              ? "verified"
              : a.cli_present === false
                ? "not_installed"
                : "not_configured",
            authStatus: a.connected ? "connected" : "not connected",
            credentialSourceLabel: "Subscription sign-in",
            cliInstalled: a.cli_present !== false,
            cliVersion: "",
            safeDiagnostic: a.connected
              ? "Sign-in detected; test the connection to verify it."
              : "No provider sign-in was detected.",
            detected: !!a.authenticated || !!a.connected,
            loginSupported: true,
            recoveryActions: a.connected
              ? ["test_connection", "disconnect"]
              : ["sign_in"],
          };
        });
    var checkedLabel = function (value) {
      return value ? new Date(Number(value)).toLocaleString() : "Never checked";
    };
    // Status-first (#237): the page opens with one honest health sentence.
    var summary = doctorSummary(
      doctorItems.map(function (item) {
        return item.health;
      })
    );
    var h =
      '<div class="doctor-summary ' +
      (summary.attention ? "warn" : "ok") +
      '" data-doctor-summary role="status"><span class="doctor-summary-dot" aria-hidden="true"></span><span class="doctor-summary-text">' +
      esc(summary.text) +
      "</span></div>";
    h +=
      '<section class="connection-doctor" role="region" aria-label="Connection Doctor"><div class="set-head">Connection Doctor</div><div class="set-note">Accounts and API provider health in one place. Credential values and files are never read or displayed.</div><div class="doctor-grid">';
    doctorItems.forEach(function (item) {
      var id = item.providerId || "provider";
      var isAccount = item.kind === "account";
      var actions = item.recoveryActions || [];
      var envNames = item.envOverridesRemoved || [];
      h +=
        '<article class="doctor-card" data-doctor-provider="' +
        esc(id) +
        '"' +
        (isAccount ? ' data-account-row="' + esc(id) + '"' : "") +
        ">" +
        '<div class="doctor-head"><div><strong>' +
        esc(item.displayName || id) +
        '</strong><span class="doctor-kind">' +
        (isAccount ? "account CLI" : "API") +
        '</span></div><span class="doctor-health ' +
        esc(item.health) +
        '" data-doctor-health>' +
        esc(connectionHealthLabel(item.health)) +
        "</span></div>" +
        '<div class="doctor-meta"><span>Credential <b>' +
        esc(item.credentialSourceLabel || "Not configured") +
        "</b></span>" +
        (isAccount
          ? "<span>CLI <b data-doctor-cli>" +
            esc(
              item.cliInstalled === false
                ? "not installed"
                : item.cliVersion || "installed"
            ) +
            "</b></span>"
          : "") +
        (item.credentialEnvironmentName
          ? "<span>Variable <b>" + esc(item.credentialEnvironmentName) + "</b></span>"
          : "") +
        "<span>Last checked <b>" +
        esc(checkedLabel(item.lastCheckedAt)) +
        "</b></span></div>" +
        '<div class="doctor-diagnostic" data-doctor-diagnostic>' +
        esc(item.safeDiagnostic || "No diagnostic available.") +
        "</div>" +
        (envNames.length
          ? '<div class="doctor-env">Ignored inherited overrides: ' +
            envNames.map(esc).join(", ") +
            "</div>"
          : "") +
        (item.lastError
          ? '<div class="doctor-error">' +
            esc(item.lastErrorCode || "Connection error") +
            ": " +
            esc(item.lastError) +
            "</div>"
          : "") +
        (isAccount
          ? '<div class="doctor-status">Status: <span data-account-status="' +
            esc(id) +
            '">' +
            esc(item.authStatus || "unknown") +
            "</span></div>"
          : "") +
        '<div class="doctor-actions">' +
        (isAccount && item.cliInstalled !== false
          ? '<button class="btn ghost" data-test-account="' +
            esc(id) +
            '">Test ' +
            esc(item.displayName || id) +
            "</button>"
          : "") +
        (isAccount && item.loginSupported !== false && item.health !== "verified"
          ? '<button class="btn primary" data-login-account="' +
            esc(id) +
            '">Sign in to ' +
            esc(item.displayName || id) +
            "</button>"
          : "") +
        (isAccount && (item.detected || actions.includes("disconnect"))
          ? '<button class="btn ghost" data-disconnect-account="' +
            esc(id) +
            '" data-account-label="' +
            esc(item.displayName || id) +
            '">Disconnect</button>'
          : "") +
        (id === "codex" && d.codexConfig && d.codexConfig.repairable
          ? '<button class="btn" id="repairCodex">Repair Codex config</button>'
          : "") +
        (!isAccount && actions.includes("test_connection")
          ? '<button class="btn ghost" data-doctor-test-provider="' +
            esc(id) +
            '">Test ' +
            esc(item.displayName || id) +
            "</button>"
          : "") +
        "</div></article>";
    });
    h += "</div></section>";
    h += '<div class="actions"><button class="btn primary" id="setConnect">Connect accounts</button></div>';
    if (
      d.codexConfig &&
      d.codexConfig.repairable &&
      !doctorItems.some(function (item) {
        return item.providerId === "codex";
      })
    ) {
      h +=
        '<div class="config-repair"><div><strong>Codex configuration needs repair</strong><div class="set-note">' +
        esc(d.codexConfig.message || "Invalid Codex configuration") +
        '</div></div><button class="btn" id="repairCodex">Repair Codex config</button></div>';
    }
    var providerNames = { gemini: "Gemini", groq: "Groq", mistral: "Mistral" };
    h += '<div class="set-head">Free model connections</div>';
    (d.credentials || []).forEach(function (credential) {
      var provider = credential.provider || "provider";
      var label = providerNames[provider] || provider;
      var status = credential.configured
        ? "Connected securely · " + credential.source
        : credential.keychainAvailable
          ? "Not connected"
          : "OS keychain unavailable — use environment setup";
      h +=
        '<div class="provider-key-card" data-provider="' +
        esc(provider) +
        '"><div class="provider-key-head"><span>' +
        esc(label) +
        '</span><span class="provider-key-status">' +
        esc(status) +
        "</span></div>" +
        '<div class="provider-key-form"><input type="password" autocomplete="off" spellcheck="false" aria-label="' +
        esc(label) +
        ' API key" placeholder="Paste API key">' +
        '<button class="btn" data-save-provider="' +
        esc(provider) +
        '">' +
        (credential.configured ? "Replace" : "Connect") +
        " " +
        esc(label) +
        "</button>" +
        (credential.configured
          ? '<button class="btn ghost" data-test-provider="' +
            esc(provider) +
            '">Test ' +
            esc(label) +
            "</button>"
          : "") +
        (credential.source === "keychain"
          ? '<button class="btn ghost" data-delete-provider="' +
            esc(provider) +
            '">Remove</button>'
          : "") +
        "</div></div>";
    });
    h += '<div class="set-note">Keys are stored only in the operating-system credential store. Environment variables override keychain values.</div>';
    if (d.github) {
      var gh = d.github;
      var ghConnected = !!gh.connected;
      var ghReady = !!gh.ready_for_push;
      h += '<div class="set-head">GitHub · pushes &amp; pull requests</div>';
      h += '<div class="provider-key-card github-card" data-github-card>';
      h +=
        '<div class="provider-key-head"><span>GitHub' +
        (gh.login ? " · " + esc(gh.login) : "") +
        '</span><span class="provider-key-status" data-github-status>' +
        (ghConnected
          ? ghReady
            ? "Connected · ready to push &amp; open PRs"
            : "Connected · pushes off"
          : "Not connected") +
        "</span></div>";
      if (!ghConnected) {
        h +=
          '<div class="provider-key-form"><input type="password" autocomplete="off" spellcheck="false" aria-label="GitHub personal access token" placeholder="Paste a GitHub token (PAT)" data-github-token>' +
          '<button class="btn" data-github-connect>Connect GitHub</button></div>';
        h += '<div class="set-note">Create a token at github.com/settings/tokens with <b>repo</b> scope (classic) or Contents + Pull requests read/write (fine-grained). Stored only in your OS keychain — never shown again.</div>';
      } else {
        h +=
          '<div class="provider-key-form">' +
          '<button class="btn ' +
          (gh.allow_push ? "primary" : "") +
          '" data-github-allowpush="' +
          (gh.allow_push ? "off" : "on") +
          '">' +
          (gh.allow_push ? "Disable pushes &amp; PRs" : "Enable pushes &amp; PRs") +
          '</button><button class="btn ghost" data-github-disconnect>Disconnect</button></div>';
        h += ghReady
          ? '<div class="set-note">Agents can now push branches and open pull requests on your GitHub repos.</div>'
          : '<div class="set-note" data-github-hint>' +
            esc(gh.hint || "Enable pushes above to let agents open PRs.") +
            "</div>";
      }
      h += "</div>";
    }
    return h;
  }

  // Usage-limit cards (shared markup; lives on the Cost Firewall page, #238).
  function usageCardsHtml(d, ctx) {
    var esc = ctx.esc;
    var modelsById = Object.fromEntries((d.models || []).map(function (m) {
      return [m.id, m];
    }));
    var fmtUsage = function (value) {
      return Number(value || 0).toLocaleString();
    };
    var h = "";
    (d.usage || []).forEach(function (usage) {
      var model = modelsById[usage.modelId] || { label: usage.modelId };
      var bounded = usage.limit != null;
      var summary = bounded
        ? fmtUsage(usage.used) + " / " + fmtUsage(usage.limit) + " " + esc(usage.metric)
        : fmtUsage(usage.used) + " " + esc(usage.metric) + " used · no limit set";
      var pct = bounded ? Math.max(0, Math.min(100, +usage.percent || 0)) : 0;
      h +=
        '<div class="usage-card" data-model-id="' +
        esc(usage.modelId) +
        '"><div class="usage-head"><span>' +
        esc(model.label || usage.modelId) +
        "</span><span>" +
        summary +
        "</span></div>" +
        '<div class="usage-track" role="progressbar" aria-label="' +
        esc(model.label || usage.modelId) +
        ' usage" aria-valuemin="0" aria-valuemax="100"' +
        (bounded ? ' aria-valuenow="' + pct + '"' : "") +
        '><span style="width:' +
        pct +
        '%"></span></div>' +
        '<div class="usage-meta">' +
        esc(usage.source === "provider" ? "Provider reported" : "OPai tracked") +
        " · " +
        esc(usage.window || "month") +
        " · " +
        esc(usage.confidence || "unknown") +
        // #334: make a large token total legible — one task is often many
        // provider calls (a tool loop re-sends context each step).
        (usage.modelCalls
          ? " · " +
            fmtUsage(usage.modelCalls) +
            " model call" +
            (usage.modelCalls === 1 ? "" : "s") +
            (usage.taskCount
              ? " across " +
                fmtUsage(usage.taskCount) +
                " task" +
                (usage.taskCount === 1 ? "" : "s")
              : "")
          : "") +
        "</div>" +
        '<div class="usage-limit-form"><input type="number" min="1" step="1" aria-label="Soft ' +
        esc(String(usage.metric || "tokens").replace(/s$/, "")) +
        ' limit" value="' +
        (bounded ? esc(usage.limit) : "") +
        '" placeholder="Set limit">' +
        '<button class="btn ghost" data-save-limit="' +
        esc(usage.modelId) +
        '" data-metric="' +
        esc(usage.metric || "tokens") +
        '" data-window="' +
        esc(usage.window || "month") +
        '">Save limit</button><span class="usage-error" data-usage-error hidden></span></div></div>';
    });
    return h;
  }

  function modelsHtml(d, ctx) {
    var esc = ctx.esc;
    var prefs = d.prefs || {};
    var boot = (ctx.state && ctx.state.boot) || {};
    // Editable defaults (#238): persisted through the same savePref slot the
    // composer uses, and reflected there instantly via ctx.applyDefaults.
    var selectRow = function (label, key, options, selected, hint) {
      if (!options.length) return "";
      return (
        '<div class="default-row"><div class="default-label"><span class="k">' +
        esc(label) +
        "</span>" +
        (hint ? '<span class="hint">' + esc(hint) + "</span>" : "") +
        '</div><select data-default-pref="' +
        esc(key) +
        '" aria-label="' +
        esc(label) +
        '">' +
        options
          .map(function (option) {
            return (
              '<option value="' +
              esc(option.id) +
              '"' +
              (option.id === selected ? " selected" : "") +
              ">" +
              esc(option.label) +
              "</option>"
            );
          })
          .join("") +
        "</select></div>"
      );
    };
    // Offer exactly what the composer offers (boot.models), so the Default
    // model picker and the composer selector can never disagree (#238).
    var modelSource = boot.models && boot.models.length ? boot.models : d.models;
    var modelOptions = (modelSource || []).map(function (m) {
      return { id: m.id, label: m.label || m.id };
    });
    if (
      !modelOptions.some(function (m) {
        return m.id === "auto";
      })
    ) {
      modelOptions.unshift({ id: "auto", label: "OPai · Auto mode" });
    }
    // Full Auto is deliberately absent: it can only be pinned from the
    // composer with an explicit acknowledgement (#137); a bare savePref for it
    // is downgraded server-side.
    var modeOptions = ["ask", "plan", "safe-auto", "approve-edits"].map(function (id) {
      return { id: id, label: MODE_LABELS[id] };
    });
    var focusOptions = (boot.taskModes || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var formatOptions = (boot.outputFormats || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var h = '<div class="set-head">Defaults</div>';
    h += selectRow("Default model", "default_model", modelOptions, prefs.default_model || "auto");
    h += selectRow(
      "Default run mode",
      "default_mode",
      modeOptions,
      MODE_LABELS[prefs.default_mode] ? prefs.default_mode : "safe-auto",
      "Full Auto can only be pinned from the composer, with an explicit acknowledgement."
    );
    h += selectRow("Task focus", "default_task_mode", focusOptions, ctx.state.focus);
    h += selectRow("Output format", "default_output_format", formatOptions, ctx.state.format);
    h += '<div class="set-note">Changes apply to the composer immediately and persist for this workspace.</div>';
    h += '<div class="set-head">Local-first routing</div>';
    var order = d.firewall && d.firewall.local_first;
    if (order) {
      h += '<div class="set-row"><span class="k">Route order</span><span class="v">' + esc(order) + "</span></div>";
    }
    h += '<div class="cb">• Every task tries free tiers first: deterministic tools, the local cache, then a local model.</div>';
    h += '<div class="cb">• Cloud models are considered only when those tiers cannot do the job; the cloud gate can require a confirmation for every paid call.</div>';
    h += '<div class="cb">• Run <span class="mono">opai why</span> on a task to see exactly why a route was chosen; every decision is in the local ledger.</div>';
    return h;
  }

  function firewallHtml(d, ctx) {
    var esc = ctx.esc;
    var firewall = d.firewall || {};
    var caps = firewall.caps || {};
    var remaining = firewall.remaining || {};
    var money = function (value) {
      return value == null ? null : "$" + (+value).toFixed(2);
    };
    var capRow = function (label, cap, left) {
      var value = money(cap) || "No cap set";
      if (money(cap) && left != null) value += " · " + money(left) + " left";
      return row(esc, label, value);
    };
    var h = '<div class="set-head">Cost firewall</div>';
    h += row(esc, "Profile", firewall.profile || "—");
    h += row(esc, "Spent today", money(firewall.spent_today || 0));
    h += row(esc, "Spent this month", money(firewall.spent_month || 0));
    h += '<div class="set-head">Budgets</div>';
    h += capRow("Daily cap", caps.daily_usd_limit, remaining.today_usd);
    h += capRow("Monthly cap", caps.monthly_usd_limit, remaining.month_usd);
    h += capRow("Per-task cap", caps.per_task_hard_limit_usd, null);
    h += '<div class="set-note">Spend is estimated locally from the usage ledger; nothing is transmitted.</div>';
    h += '<div class="set-head">Model usage limits</div>';
    h += usageCardsHtml(d, ctx);
    h += '<div class="set-head">Safety switches</div>';
    h += row(esc, "Panic mode", firewall.panic ? "ON (local-only)" : "off");
    h += '<div class="cb">• Panic mode refuses every cloud call and forces local-only routing until you disable it.</div>';
    h += row(esc, "Cloud gate", firewall.cloud_gate ? "confirm" : "open");
    h += '<div class="cb">• Confirm asks before each paid cloud call; open sends without a per-call confirmation.</div>';
    h +=
      '<div class="actions"><button class="btn" id="setPanic">' +
      (firewall.panic ? "Disable panic" : "Enable panic") +
      "</button></div>";
    return h;
  }

  function permissionsHtml(d, ctx) {
    var esc = ctx.esc;
    var activeMode = MODE_LABELS[d.prefs.default_mode] || d.prefs.default_mode;
    var h =
      '<div class="set-head">Tool permissions · ' + esc(activeMode) + "</div>";
    h +=
      '<div class="set-note">What OPai may do this turn under your current run mode. Allow = does it without asking; Ask = pauses for your OK; Blocked = refused.</div>';
    (d.permissions || []).forEach(function (p) {
      h +=
        '<div class="perm"><span class="k">' +
        esc(p.label) +
        (p.note ? '<span class="perm-note">' + esc(p.note) + "</span>" : "") +
        '</span><span class="s ' +
        p.state +
        '">' +
        esc(p.state) +
        "</span></div>";
    });
    // Per-mode comparison (#239): how the five run modes differ, derived from
    // the same permission rules (not re-invented). The active mode is marked.
    if ((d.modePermissions || []).length) {
      h += '<div class="set-head">Run modes</div>';
      h +=
        '<div class="set-note">Switch modes from the composer. Full Auto acts without asking and must be pinned there with an acknowledgement.</div>';
      d.modePermissions.forEach(function (mode) {
        h +=
          '<div class="mode-row' +
          (mode.active ? " active" : "") +
          '"><span class="k">' +
          esc(mode.label) +
          (mode.active ? ' <span class="mode-active">current</span>' : "") +
          '</span><span class="mode-summary">' +
          esc(mode.summary) +
          "</span></div>";
      });
    }
    return h;
  }

  function privacyHtml(d, ctx) {
    var esc = ctx.esc;
    var privacy = d.privacy || {};
    var statements = privacy.statements || [
      "No telemetry — nothing leaves your machine.",
      "Raw prompts are never stored; the local ledger keeps one-way task hashes and counts only.",
      "Local-first routing; cloud only on confirmation.",
    ];
    var h = '<div class="set-head">Privacy &amp; data</div>';
    statements.forEach(function (t) {
      h += '<div class="cb">• ' + esc(t) + "</div>";
    });
    h += '<div class="set-head">Saved chat &amp; recents</div>';
    h +=
      '<div class="set-note">Saved chat is stored redacted on this machine, per workspace. Clearing it is immediate and cannot be undone.</div>';
    h +=
      '<div class="actions"><button class="btn danger" id="settingsClearRecents">Clear saved chat &amp; recents</button></div>';
    return h;
  }

  function appearanceHtml(d, ctx) {
    var esc = ctx.esc;
    var prefs = d.prefs || {};
    var density = prefs.density === "compact" ? "compact" : "comfortable";
    var motion =
      prefs.reduced_motion === "on" || prefs.reduced_motion === "off"
        ? prefs.reduced_motion
        : "system";
    var seg = function (key, current, options) {
      return (
        '<div class="seg" role="group" data-appearance-key="' +
        esc(key) +
        '">' +
        options
          .map(function (option) {
            var active = option.id === current;
            return (
              '<button type="button" data-value="' +
              esc(option.id) +
              '" aria-pressed="' +
              (active ? "true" : "false") +
              '"' +
              (active ? ' class="active"' : "") +
              ">" +
              esc(option.label) +
              "</button>"
            );
          })
          .join("") +
        "</div>"
      );
    };
    var h = '<div class="set-head">Appearance</div>';
    h += '<div class="set-note">Applied instantly and saved for this workspace.</div>';
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Density</span><span class="hint">Compact tightens spacing across the cockpit.</span></div>' +
      seg("density", density, [
        { id: "comfortable", label: "Comfortable" },
        { id: "compact", label: "Compact" },
      ]) +
      "</div>";
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Reduced motion</span><span class="hint">System follows your OS setting. On disables animations everywhere; Off keeps them on.</span></div>' +
      seg("reduced_motion", motion, [
        { id: "system", label: "System" },
        { id: "on", label: "On" },
        { id: "off", label: "Off" },
      ]) +
      "</div>";
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Theme</span><span class="hint">Dark is the only complete theme; a light theme is not shipped yet.</span></div><span class="v">Dark (default)</span></div>';
    return h;
  }

  function aboutHtml(d, ctx) {
    var esc = ctx.esc;
    if (!(d.about && d.about.version)) return "";
    return (
      '<div class="set-head">About</div>' +
      row(esc, "Version", d.about.version) +
      row(esc, "Release stage", d.about.release_stage || "—") +
      // Replay the first-run tour on demand (#250).
      '<div class="set-note">New here, or want a refresher? Replay the three-step welcome tour.</div>' +
      '<div class="actions"><button class="btn" id="settingsReplayTour">Replay tour</button></div>'
    );
  }

  // The registry: rail label + keywords + the section's content builder.
  var sections = [
    {
      id: "providers",
      title: "Providers & Connections",
      keywords: "provider account api key github connection doctor sign in credential codex",
      render: providersHtml,
    },
    {
      id: "models",
      title: "Models & Routing",
      keywords: "model usage limit default routing focus format",
      render: modelsHtml,
    },
    {
      id: "firewall",
      title: "Cost Firewall",
      keywords: "cost firewall panic budget spend cloud gate profile",
      render: firewallHtml,
    },
    {
      id: "permissions",
      title: "Permissions & Safety",
      keywords: "permission tool safety mode approve",
      render: permissionsHtml,
    },
    {
      id: "privacy",
      title: "Privacy & Data",
      keywords: "privacy data telemetry redacted local",
      render: privacyHtml,
    },
    {
      id: "appearance",
      title: "Appearance",
      keywords: "theme density motion animation compact reduced dark",
      render: appearanceHtml,
    },
    { id: "about", title: "About", keywords: "about version release", render: aboutHtml },
  ];

  // ---- search + paned pages ---------------------------------------------- //
  // Group a pane's children into logical blocks: a boundary (.set-head or the
  // Connection Doctor card) plus everything up to the next boundary. Search
  // shows/hides whole blocks, so a matching row keeps its heading (#240).
  function settingsBlocks(pane) {
    var blocks = [];
    var cur = null;
    Array.prototype.forEach.call(pane.children, function (el) {
      var isBoundary =
        el.classList.contains("set-head") || el.classList.contains("connection-doctor");
      if (isBoundary || !cur) {
        cur = [];
        blocks.push(cur);
      }
      cur.push(el);
    });
    return blocks;
  }

  // ---- orchestration ----------------------------------------------------- //
  // Claude-style paned settings: the rail on the left is real page navigation —
  // one cleanly labelled page visible at a time. Search stays global (#240):
  // typing switches the layout into a cross-page results mode where every page
  // shows only its matching blocks (each under its page label), and clearing
  // the query returns to the active page.
  function render(page, ctx) {
    var d = ctx.d || {};
    var esc = ctx.esc;
    var present = sections.filter(function (section) {
      var html = section.render(d, ctx);
      section._html = html;
      return !!html;
    });

    var header =
      '<div class="page-title">Settings</div><div class="page-sub">Project: ' +
      esc((ctx.state.boot.workspace && ctx.state.boot.workspace.root) || "") +
      "</div>" +
      '<div class="settings-toolbar"><input id="settingsSearch" type="search" placeholder="Search settings…" aria-label="Search settings" autocomplete="off" spellcheck="false"><span class="settings-noresults" id="settingsNoResults" hidden>No settings match your search.</span></div>';

    var panesHtml = present
      .map(function (section) {
        return (
          '<section class="settings-pane" id="set-sec-' +
          esc(section.id) +
          '" data-pane="' +
          esc(section.id) +
          '" data-pane-title="' +
          esc(section.title) +
          '" role="region" aria-label="' +
          esc(section.title) +
          '">' +
          section._html +
          "</section>"
        );
      })
      .join("");

    var rail =
      '<nav class="settings-rail" aria-label="Settings pages">' +
      present
        .map(function (section) {
          return (
            '<button class="settings-rail-item" type="button" data-rail-target="' +
            esc(section.id) +
            '"><span class="settings-rail-label">' +
            esc(section.title) +
            "</span></button>"
          );
        })
        .join("") +
      "</nav>";

    page.innerHTML =
      '<div class="settings-layout">' +
      rail +
      '<div class="settings-content" id="settingsContent">' +
      header +
      panesHtml +
      "</div></div>";

    var layout = page.querySelector(".settings-layout");
    var content = page.querySelector("#settingsContent");
    var panes = Array.prototype.slice.call(content.querySelectorAll(".settings-pane"));
    var railItems = Array.prototype.slice.call(
      page.querySelectorAll(".settings-rail-item")
    );
    wire(content, ctx);

    function activate(id, updateHash) {
      panes.forEach(function (pane) {
        pane.classList.toggle("active", pane.dataset.pane === id);
      });
      railItems.forEach(function (link) {
        var on = link.dataset.railTarget === id;
        link.classList.toggle("active", on);
        if (on) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      if (updateHash === false) return;
      try {
        global.history &&
          global.history.replaceState &&
          global.history.replaceState(null, "", "#settings/" + id);
      } catch (_e) {
        /* hash routing is best-effort */
      }
    }

    function applySearch(query) {
      var q = String(query || "").trim().toLowerCase();
      var searching = q !== "";
      layout.classList.toggle("searching", searching);
      var anyShown = false;
      panes.forEach(function (pane) {
        var paneShown = false;
        settingsBlocks(pane).forEach(function (block) {
          var text = block
            .map(function (el) {
              return el.textContent;
            })
            .join(" ")
            .toLowerCase();
          var show = !searching || text.indexOf(q) >= 0;
          if (show) paneShown = true;
          block.forEach(function (el) {
            el.style.display = show ? "" : "none";
          });
        });
        pane.classList.toggle("no-match", searching && !paneShown);
        if (paneShown) anyShown = true;
      });
      var noResults = content.querySelector("#settingsNoResults");
      if (noResults) noResults.toggleAttribute("hidden", anyShown || !searching);
      railItems.forEach(function (link) {
        var pane = content.querySelector('[data-pane="' + link.dataset.railTarget + '"]');
        link.toggleAttribute(
          "data-dim",
          searching && !!pane && pane.classList.contains("no-match")
        );
      });
    }

    var search = content.querySelector("#settingsSearch");
    railItems.forEach(function (link) {
      link.onclick = function () {
        if (search && search.value) {
          search.value = "";
          applySearch("");
        }
        activate(link.dataset.railTarget);
      };
    });

    if (search) {
      search.oninput = function () {
        applySearch(search.value);
      };
      search.onkeydown = function (e) {
        if (e.key === "Escape") {
          search.value = "";
          applySearch("");
        }
      };
    }

    // Deep link (#settings/<id>) opens that page; otherwise the first page.
    var hash = (global.location && global.location.hash) || "";
    var match = /^#settings\/([\w-]+)$/.exec(hash);
    var initial =
      match && content.querySelector('[data-pane="' + match[1] + '"]')
        ? match[1]
        : present.length
          ? present[0].id
          : "";
    if (initial) activate(initial, false);

    if (ctx.startDoctorRefresh) ctx.startDoctorRefresh();
  }

  // ---- wiring (exact handlers moved from app.js), scoped to `page` -------- //
  function wire(page, ctx) {
    var bridge = ctx.bridge;
    var esc = ctx.esc;
    var toast = ctx.toast;
    var state = ctx.state;
    var refresh = ctx.refresh;
    var q = function (sel) {
      return page.querySelector(sel);
    };

    var pb = q("#setPanic");
    if (pb)
      pb.onclick = function () {
        ctx.switchView("chat");
        bridge.runTool("panic");
      };
    var cb = q("#setConnect");
    if (cb)
      cb.onclick = function () {
        ctx.switchView("chat");
        bridge.runTool("connect");
      };
    var repair = q("#repairCodex");
    if (repair)
      repair.onclick = function () {
        var host = repair.closest(".config-repair") || repair.parentElement;
        repair.disabled = true;
        ctx
          .inlineConfirm(host, {
            title: "Repair Codex config?",
            body: 'OPai will create a backup, then remove only the invalid service_tier = "default" line from your Codex config.',
            confirmLabel: "Repair config",
          })
          .then(function (ok) {
            repair.disabled = false;
            if (!ok) return;
            bridge.repairCodexConfig(function (json2) {
              var result = JSON.parse(json2);
              if (result.repaired) {
                host.innerHTML =
                  '<div><strong>Codex config repaired</strong><div class="set-note">Invalid tier removed; backup created.</div></div>';
              } else toast(result.error || "Could not repair Codex config");
            });
          });
      };
    page.querySelectorAll("[data-save-provider]").forEach(function (button) {
      button.onclick = function () {
        var card = button.closest(".provider-key-card");
        var input = card.querySelector("input");
        var secret = input.value.trim();
        if (!secret) return;
        bridge.saveProviderKey(button.dataset.saveProvider, secret, function (json2) {
          input.value = "";
          var result = JSON.parse(json2);
          var status = card.querySelector(".provider-key-status");
          status.textContent = result.configured
            ? "Connected securely · " + result.source
            : result.error || "Not connected";
          if (result.configured && bridge.refreshModels)
            bridge.refreshModels(function (modelsJson) {
              var refreshed = JSON.parse(modelsJson);
              if (refreshed.models) {
                state.boot.models = refreshed.models;
                ctx.renderComposerSelects();
              }
            });
        });
      };
    });
    page.querySelectorAll("[data-delete-provider]").forEach(function (button) {
      button.onclick = function () {
        return bridge.deleteProviderKey(button.dataset.deleteProvider, function () {
          refresh();
        });
      };
    });
    var ghConnect = q("[data-github-connect]");
    if (ghConnect)
      ghConnect.onclick = function () {
        var input = q("[data-github-token]");
        var statusEl = q("[data-github-status]");
        var token = ((input && input.value) || "").trim();
        if (!token) {
          if (statusEl) statusEl.textContent = "Paste a token first";
          return;
        }
        if (!bridge.githubConnect) {
          if (statusEl) statusEl.textContent = "GitHub connect is unavailable in this build.";
          return;
        }
        ghConnect.disabled = true;
        if (statusEl) statusEl.textContent = "Verifying token…";
        bridge.githubConnect(token, function (json2) {
          if (input) input.value = "";
          var result = JSON.parse(json2);
          if (result.connected) {
            toast("GitHub connected as " + (result.login || "user"));
            refresh();
          } else {
            if (statusEl) statusEl.textContent = result.error || "Connection failed";
            ghConnect.disabled = false;
          }
        });
      };
    var ghToggle = q("[data-github-allowpush]");
    if (ghToggle && bridge.githubSetPush)
      ghToggle.onclick = function () {
        ghToggle.disabled = true;
        bridge.githubSetPush(ghToggle.dataset.githubAllowpush, function (json2) {
          var result = JSON.parse(json2);
          toast(
            result.allow_push
              ? result.ready
                ? "Pushes & PRs enabled"
                : "Consent on — a token is still needed"
              : "Pushes disabled"
          );
          refresh();
        });
      };
    var ghDisconnect = q("[data-github-disconnect]");
    if (ghDisconnect && bridge.githubDisconnect)
      ghDisconnect.onclick = function () {
        return bridge.githubDisconnect(function () {
          toast("GitHub disconnected");
          refresh();
        });
      };
    page.querySelectorAll("[data-test-provider]").forEach(function (button) {
      button.onclick = function () {
        var card = button.closest(".provider-key-card");
        var status = card.querySelector(".provider-key-status");
        status.textContent = "Testing connection…";
        button.disabled = true;
        bridge.testProvider(button.dataset.testProvider, function (json2) {
          var result = JSON.parse(json2);
          button.disabled = false;
          var detail = result.error && (result.error.userMessage || result.error);
          status.textContent = result.connected
            ? "Connection verified"
            : detail || "Connection failed";
        });
      };
    });
    page.querySelectorAll("[data-doctor-test-provider]").forEach(function (button) {
      button.onclick = function () {
        var id = button.dataset.doctorTestProvider;
        button.disabled = true;
        button.textContent = "Testing…";
        bridge.testProvider(id, function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          button.disabled = false;
          button.textContent = "Test " + ctx.providerName(id);
          ctx.updateDoctorCard(id, result);
          toast(
            result.connected
              ? "Connection verified"
              : (result.error && result.error.userMessage) || "Connection failed"
          );
        });
      };
    });
    page.querySelectorAll("[data-login-account]").forEach(function (button) {
      button.onclick = function () {
        ctx.startGuidedProviderLogin(button.dataset.loginAccount, { button: button });
      };
    });
    page.querySelectorAll("[data-test-account]").forEach(function (button) {
      button.onclick = function () {
        var id = button.dataset.testAccount;
        var status = q('[data-account-status="' + id + '"]');
        button.disabled = true;
        button.textContent = "Testing…";
        if (status) status.textContent = "testing…";
        bridge.testProvider(id, function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          button.disabled = false;
          button.textContent = "Test connection";
          var live = result.authStatus === "connected";
          if (status)
            status.textContent = live ? "connected" : result.authStatus || "needs attention";
          ctx.updateDoctorCard(id, result);
          if (live) {
            toast("Connection verified");
            return;
          }
          var hint = result.loginHint ? " " + result.loginHint : "";
          toast((result.safeDiagnostic || "Connection check failed.") + hint);
        });
      };
    });
    page.querySelectorAll("[data-disconnect-account]").forEach(function (button) {
      button.onclick = function () {
        var id = button.dataset.disconnectAccount;
        var label = button.dataset.accountLabel || id;
        var host =
          button.closest(".doctor-card") ||
          button.closest("[data-account-row]") ||
          button.parentElement;
        button.disabled = true;
        ctx
          .inlineConfirm(host, {
            title: "Sign out of " + label + "?",
            body: "OPai runs the provider's own sign-out so the next run starts a fresh login. You'll need to sign in again to use it.",
            confirmLabel: "Sign out",
            danger: true,
          })
          .then(function (ok) {
            if (!ok) {
              button.disabled = false;
              return;
            }
            button.textContent = "Disconnecting…";
            bridge.disconnectAccount(id, function (json2) {
              var result = {};
              try {
                result = JSON.parse(json2);
              } catch (_e) {
                /* keep {} */
              }
              button.textContent = "Disconnect";
              toast(
                result.message ||
                  (result.disconnected ? "Signed out." : "Could not sign out.")
              );
              if (result.disconnected) {
                var status = q('[data-account-status="' + id + '"]');
                var dot = q('[data-account-row="' + id + '"] .prov-dot');
                if (status) status.textContent = "not connected";
                if (dot) dot.classList.remove("on");
                var health =
                  button.closest(".doctor-card") &&
                  button.closest(".doctor-card").querySelector("[data-doctor-health]");
                if (health) {
                  health.textContent = "Not configured";
                  health.className = "doctor-health not_configured";
                }
                var testBtn = q('[data-test-account="' + id + '"]');
                if (testBtn) testBtn.disabled = true;
                button.disabled = true;
              } else {
                button.disabled = false;
              }
            });
          });
      };
    });
    page.querySelectorAll("[data-save-limit]").forEach(function (button) {
      button.onclick = function () {
        var form = button.closest(".usage-limit-form");
        var input = form.querySelector("input");
        var error = form.querySelector("[data-usage-error]");
        var value = input.value.trim();
        // Client-side validation (#238): a limit must be a whole number above
        // zero. Invalid input gets an inline error, and nothing is saved.
        if (!value || !/^\d+$/.test(value) || +value <= 0) {
          if (error) {
            error.textContent = "Enter a whole number above zero.";
            error.hidden = false;
          }
          return;
        }
        if (error) error.hidden = true;
        bridge.saveUsageLimit(
          button.dataset.saveLimit,
          button.dataset.metric,
          value,
          button.dataset.window,
          function (json2) {
            var result = JSON.parse(json2);
            toast(result.ok ? "Usage limit saved" : result.error || "Could not save limit");
          }
        );
      };
    });
    // Replay the first-run tour (#250) — reuses the real onboarding overlay.
    var replayBtn = q("#settingsReplayTour");
    if (replayBtn && ctx.replayTour)
      replayBtn.onclick = function () {
        ctx.replayTour();
      };
    // Clear saved chat & recents (#239): a destructive action, gated by the
    // same styled inline confirm the rest of the app uses — never a bare click.
    var clearBtn = q("#settingsClearRecents");
    if (clearBtn)
      clearBtn.onclick = function () {
        if (!bridge.clearRecents) {
          toast("Clearing saved chat is unavailable in this build.");
          return;
        }
        var host = clearBtn.closest(".actions") || clearBtn.parentElement;
        clearBtn.disabled = true;
        ctx
          .inlineConfirm(host, {
            title: "Clear saved chat & recents?",
            body: "This permanently removes this workspace's saved chat and recent-task list from your machine. It cannot be undone.",
            confirmLabel: "Clear now",
            danger: true,
          })
          .then(function (ok) {
            clearBtn.disabled = false;
            if (!ok) return;
            bridge.clearRecents(function (json2) {
              var result = {};
              try {
                result = JSON.parse(json2);
              } catch (_e) {
                /* keep {} */
              }
              toast(result.ok ? "Saved chat cleared." : result.error || "Could not clear saved chat.");
            });
          });
      };
    // Editable defaults (#238): persist and reflect in the composer instantly.
    page.querySelectorAll("[data-default-pref]").forEach(function (select) {
      select.onchange = function () {
        bridge.savePref(select.dataset.defaultPref, select.value);
        if (ctx.applyDefaults) ctx.applyDefaults(select.dataset.defaultPref, select.value);
      };
    });
    // Appearance (#241): persist via savePref and apply to the root instantly.
    page.querySelectorAll("[data-appearance-key]").forEach(function (segment) {
      var key = segment.dataset.appearanceKey;
      segment.querySelectorAll("button").forEach(function (button) {
        button.onclick = function () {
          segment.querySelectorAll("button").forEach(function (other) {
            other.classList.toggle("active", other === button);
            other.setAttribute("aria-pressed", other === button ? "true" : "false");
          });
          bridge.savePref(key, button.dataset.value);
          if (!ctx.applyAppearance) return;
          var current = {};
          page.querySelectorAll("[data-appearance-key]").forEach(function (other) {
            var active = other.querySelector("button.active");
            var name =
              other.dataset.appearanceKey === "reduced_motion"
                ? "reducedMotion"
                : other.dataset.appearanceKey;
            current[name] = active ? active.dataset.value : "";
          });
          ctx.applyAppearance(current);
        };
      });
    });
  }

  var api = {
    el: el,
    sections: sections,
    render: render,
    doctorSummary: doctorSummary,
    MODE_LABELS: MODE_LABELS,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiSettings = api;
})(typeof window !== "undefined" ? window : this);
