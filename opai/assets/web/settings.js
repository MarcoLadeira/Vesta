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
    "safe-auto": "Auto",
    "approve-edits": "Manual",
    "auto-edits": "Accept Edits",
    "full-auto": "Bypass Permissions",
  };
  function modePresentationLabel(id, fallback) {
    var raw = fallback || MODE_LABELS[id] || id || "Mode";
    if (typeof global.OPaiModePresentationLabel === "function") {
      return global.OPaiModePresentationLabel({ id: id, label: raw });
    }
    return raw;
  }
  function modePresentationCopy(value) {
    if (typeof global.OPaiModePresentationCopy === "function") {
      return global.OPaiModePresentationCopy(value);
    }
    return String(value || "")
      .replace(/\bSafe Auto\b/g, modePresentationLabel("safe-auto"))
      .replace(/\bApprove Edits\b/g, modePresentationLabel("approve-edits"))
      .replace(/\bAuto-Accept Edits\b/g, modePresentationLabel("auto-edits"))
      .replace(/\bFull Auto\b/g, modePresentationLabel("full-auto"));
  }

  function row(esc, k, v) {
    return (
      '<div class="set-row"><span class="k">' +
      esc(k) +
      '</span><span class="v">' +
      esc(v) +
      "</span></div>"
    );
  }

  function settingsContext(ctx, options) {
    var next = {};
    Object.keys(ctx || {}).forEach(function (key) {
      next[key] = ctx[key];
    });
    Object.keys(options || {}).forEach(function (key) {
      next[key] = options[key];
    });
    return next;
  }

  function settingsSubsection(esc, id, title, content) {
    if (!content) return "";
    return (
      '<section class="settings-subsection" data-settings-subsection="' +
      esc(id) +
      '" data-settings-title="' +
      esc(title) +
      '">' +
      content +
      "</section>"
    );
  }

  // ---- Settings redesign (OPai Settings design doc) ----------------------- //
  // Every page opens with its own title, a one-sentence purpose, and scope
  // chips that state where the setting lives. Chips are facts, not marketing:
  // blue = app-wide, accent = this project, muted = stored locally only.
  var HERO_CHIPS = {
    app: { label: "App-wide", tone: "blue" },
    project: { label: "This project", tone: "accent" },
    local: { label: "Local only", tone: "muted" },
    instant: { label: "Saved instantly", tone: "accent" },
  };

  function heroHtml(esc, title, desc, chips) {
    var h =
      '<div class="pane-hero"><h1 class="pane-title" tabindex="-1">' +
      esc(title) +
      "</h1>";
    if (desc) h += '<div class="pane-desc">' + esc(desc) + "</div>";
    if (chips && chips.length) {
      h +=
        '<div class="pane-chips">' +
        chips
          .map(function (id) {
            var chip = HERO_CHIPS[id];
            return (
              '<span class="pane-chip ' +
              chip.tone +
              '"><span class="pane-chip-dot" aria-hidden="true"></span>' +
              esc(chip.label) +
              "</span>"
            );
          })
          .join("") +
        "</div>";
    }
    return h + "</div>";
  }

  // A small uppercase-label stat tile. `tone` colours the sub line so status
  // is always colour + a word, never colour alone.
  function statTile(esc, opts) {
    return (
      '<div class="stat-tile"><div class="stat-label">' +
      esc(opts.label) +
      '</div><div class="stat-value' +
      (opts.mono ? " mono" : "") +
      '">' +
      esc(opts.value) +
      "</div>" +
      (opts.sub
        ? '<div class="stat-sub' + (opts.tone ? " " + opts.tone : "") + '">' + esc(opts.sub) + "</div>"
        : "") +
      "</div>"
    );
  }

  // Auth-status wording comes from app.js's AUTH_STATUS_LABEL, deliberately
  // not redefined here. Both files are classic scripts sharing one global
  // scope, and every call below runs from a handler or a render after both
  // have loaded.
  //
  // Keeping a second copy is what caused the defect this fixes: three places
  // wrote the status element with wording of their own, so a card could read
  // "Sign-in verified locally; provider acceptance is confirmed" directly
  // above "Status: not connected". One map, or the contradiction comes back.

  // Connection Doctor items, from the rich payload when present or derived
  // from the plain accounts list otherwise. Shared by the Providers page and
  // the Overview status card so their counts can never disagree.
  function doctorItemsOf(d) {
    return Array.isArray(d.connectionDoctor)
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
            authStatus: a.connected ? "connected" : "not_configured",
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
    var doctorItems = doctorItemsOf(d);
    var checkedLabel = function (value) {
      return value ? new Date(Number(value)).toLocaleString() : "Never checked";
    };
    // Status-first (#237): the page opens with one honest health sentence.
    var summary = doctorSummary(
      doctorItems.map(function (item) {
        return item.health;
      })
    );
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Connections",
          "Connect AI services and resolve problems where they occur.",
          ["app", "local"]
        );
    h +=
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
        "<span>Last checked <b data-doctor-last-checked>" +
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
            esc(authStatusLabel(item.authStatus)) +
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
    h += '<div class="actions"><button class="btn primary" id="setConnect">Connect CLI accounts…</button><span class="set-note">Opens a guided sign-in in Chat for CLI accounts (Claude, Codex, Copilot). API-key providers are managed above.</span></div>';
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
    // Round 2: this card used to sit *below* the free-provider key list, and
    // once push consent was on its only affordance read "Disable pushes & PRs".
    // A user told to find "Enable pushes & PRs" therefore found nothing — the
    // phrase was absent and the card was off-screen. It now leads the page,
    // states On/Off in words, and always names the setting so Settings search
    // finds it in either state.
    if (d.github) {
      var gh = d.github;
      var ghConnected = !!gh.connected;
      var ghReady = !!gh.ready_for_push;
      h += '<div class="set-head">GitHub · pushes &amp; pull requests</div>';
      h +=
        '<div class="set-note">Controls whether OPai may run <b>git push</b> and open pull requests for you. This is the only place pushes &amp; PRs are enabled — there is no other push or git setting.</div>';
      h += '<div class="provider-key-card github-card" data-github-card>';
      h +=
        '<div class="provider-key-head"><span>GitHub' +
        (gh.login ? " · " + esc(gh.login) : "") +
        '</span><span class="provider-key-status" data-github-status>' +
        (ghConnected
          ? ghReady
            ? "Connected · pushes &amp; PRs ON"
            : "Connected · pushes &amp; PRs OFF"
          : "Not connected") +
        "</span></div>";
      if (!ghConnected) {
        h +=
          '<div class="provider-key-form"><input type="password" autocomplete="off" spellcheck="false" aria-label="GitHub personal access token" placeholder="Paste a GitHub token (PAT)" data-github-token>' +
          '<button class="btn" data-github-connect>Connect GitHub</button></div>';
        h += '<div class="set-note">Step 1 of 2 — connect a token, then the <b>Enable pushes &amp; PRs</b> button appears here. Create a token at github.com/settings/tokens with <b>repo</b> scope (classic) or Contents + Pull requests read/write (fine-grained). Stored only in your OS keychain — never shown again.</div>';
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
          ? '<div class="set-note">Pushes &amp; PRs are <b>already enabled</b> — this is the "Enable pushes &amp; PRs" setting, now on. OPai can push branches and open pull requests. Use the button above to turn it back off.</div>'
          : '<div class="set-note" data-github-hint>' +
            esc(gh.hint || "Pushes & PRs are off. Click Enable pushes & PRs above.") +
            "</div>";
      }
      h += "</div>";
    }
    var providerNames = { kimi: "Kimi", gemini: "Gemini", groq: "Groq", mistral: "Mistral" };
    // Bug 7: this section and Connection Doctor above both list the free
    // providers, which reads as duplicate UI. Name the split by role — Doctor
    // shows health and runs a Test; this is where you paste/replace/remove the
    // actual API key — and point back up so it's clearly one flow, not two.
    h += '<div class="set-head">Free model API keys</div>';
    h += '<div class="set-note">Add, replace, or remove the API key for each free provider here. Their live health and the Test button are in Connection Doctor above.</div>';
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
    h += '<div class="set-head">What OPai can access</div>';
    h +=
      '<div class="callout-card"><div class="callout-body">OPai sends only the files and context you attach to a task, to the provider you route to. ' +
      "Credentials live in your OS keychain or the provider's own CLI — OPai reads a connection <b>status</b>, never the secret itself. " +
      "Connection tests and health checks stay on this device.</div></div>";
    return h;
  }

  // ---- Credits & Balance (per-provider credit truth) --------------------- //
  var CURRENCY_SYMBOLS = { USD: "$", EUR: "€", GBP: "£", CNY: "¥", JPY: "¥" };
  function fmtMoney(amount, currency) {
    if (amount == null || isNaN(amount)) return "Unknown";
    var symbol = CURRENCY_SYMBOLS[currency];
    var value = Number(amount).toFixed(2);
    return symbol ? symbol + value : value + " " + (currency || "");
  }
  var BALANCE_STATUS_LABEL = {
    ok: "Credit available",
    low: "Running low",
    out: "Out of credit",
    unknown: "Balance unknown",
    not_configured: "Not connected",
  };

  function balanceHtml(d, ctx) {
    var esc = ctx.esc;
    var balances = Array.isArray(d.providerBalances) ? d.providerBalances : [];
    if (!balances.length) return "";
    var h = '<div class="set-head">Credits &amp; balance</div>';
    h +=
      '<div class="set-note">How much credit is left on every AI tool you have set up. ' +
      "A tool that is out of credit is removed from the model picker and skipped by Auto " +
      "until it has credit again. Live balances come from the provider; for tools without " +
      "a balance API, enter what your provider console shows.</div>";
    balances.forEach(function (b) {
      var status = b.status || "unknown";
      var known = b.amount != null;
      var pct =
        status === "out"
          ? 0
          : b.percent != null
            ? Math.max(0, Math.min(100, +b.percent))
            : known && b.amount > 0
              ? 100
              : 0;
      // "Unknown" is honest but unhelpful — three real, distinct situations
      // hide behind it. Tell them apart so nothing reads as a generic failure:
      //   1. Subscription plans (Claude/Codex/Copilot) have no spendable
      //      balance to meter at all — that's Model Usage's job, not this page.
      //   2. A provider with a live balance API (Kimi) just hasn't been
      //      checked yet — a real number is one Refresh away.
      //   3. A free-tier API with no balance API and nothing entered — the
      //      user can track their own number, or leave it be.
      var unknownPill = "Balance unknown";
      var unknownAmount = "Unknown";
      var unknownSource = "No balance data yet";
      if (!known && status === "unknown") {
        if (b.kind === "account") {
          unknownPill = "No credit balance";
          unknownAmount = "No credit balance";
          unknownSource = "Subscription plan — see Model Usage for rate limits";
        } else if (b.supportsLiveBalance) {
          unknownPill = "Not checked yet";
          unknownAmount = "Not checked yet";
          unknownSource = "Press Refresh to fetch your live balance";
        } else {
          unknownPill = "Not tracked";
          unknownAmount = "Not tracked";
          unknownSource = "No balance API for this provider — enter one below if you track it yourself";
        }
      }
      var amountText = known ? fmtMoney(b.amount, b.currency) : unknownAmount;
      var checked = b.checkedAt
        ? new Date(Number(b.checkedAt) * 1000).toLocaleString()
        : null;
      var sourceLine =
        b.source === "provider"
          ? "Live from " + (b.displayName || b.provider)
          : b.source === "manual"
            ? "Entered by you"
            : b.source === "observed"
              ? "Observed from a refused call"
              : unknownSource;
      if (checked) sourceLine += " · " + checked;
      h +=
        '<div class="balance-card" data-balance-provider="' +
        esc(b.provider) +
        '"><div class="balance-head"><span class="balance-name">' +
        esc(b.displayName || b.provider) +
        '</span><span class="balance-pill ' +
        esc(status) +
        '">' +
        (status === "unknown" ? esc(unknownPill) : esc(BALANCE_STATUS_LABEL[status] || status)) +
        "</span></div>" +
        '<div class="balance-amount' +
        (known ? "" : " balance-amount-text") +
        '" data-balance-amount>' +
        esc(amountText) +
        (known ? '<span class="balance-left"> left</span>' : "") +
        "</div>" +
        // A bar implies a known quantity — never rendered for an honestly
        // unknown amount (that would misread as "empty"/"out").
        (known
          ? '<div class="usage-track balance-track ' +
            esc(status) +
            '" role="progressbar" aria-label="' +
            esc((b.displayName || b.provider) + " remaining credit") +
            '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
            pct +
            '"><span style="width:' +
            pct +
            '%"></span></div>'
          : "") +
        '<div class="balance-meta">' +
        esc(sourceLine) +
        "</div>" +
        (status === "out"
          ? '<div class="balance-recharge">' + esc(b.rechargeHint || "") + "</div>"
          : "") +
        '<div class="balance-form"><input type="number" min="0" step="0.01" ' +
        'aria-label="' +
        esc((b.displayName || b.provider) + " balance") +
        '" placeholder="Enter balance"' +
        (b.source === "manual" && known ? ' value="' + esc(b.amount) + '"' : "") +
        '><select aria-label="Currency">' +
        ["EUR", "USD", "GBP", "CNY"]
          .map(function (code) {
            return (
              '<option value="' +
              code +
              '"' +
              ((b.currency || "USD") === code ? " selected" : "") +
              ">" +
              code +
              "</option>"
            );
          })
          .join("") +
        '</select><button class="btn ghost" data-save-balance="' +
        esc(b.provider) +
        '">Save</button><span class="usage-error" data-balance-error hidden></span></div>' +
        "</div>";
    });
    h +=
      '<div class="actions"><button class="btn" id="balanceRefresh">Refresh live balances</button></div>';
    h +=
      '<div class="set-note">Balances are stored only on this machine. OPai never sends them anywhere.</div>';
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
    var part = ctx.settingsPart || "all";
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
              (option.disabled ? " disabled" : "") +
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
    var modelOverrides = d.modelOverrides || { global: true, path: "~/.opai/models.json", providers: {}, hidden: {}, errors: [] };
    var modelOptions = (modelSource || []).filter(function (m) {
      return !ctx.isModelVisible || ctx.isModelVisible(m, modelOverrides);
    }).map(function (m) {
      return { id: m.id, label: m.label || m.id };
    });
    if (
      !modelOptions.some(function (m) {
        return m.id === "auto";
      })
    ) {
      modelOptions.unshift({ id: "auto", label: "OPai · Auto mode" });
    }
    // Every mode is selectable here, Full Auto included. It used to be absent
    // (and shown disabled if it was already your default) because a bare
    // savePref for it was downgraded server side, so this page would have been
    // an alternate route around the composer's acknowledgement gate. There is
    // no downgrade and no gate any more: a mode persists by being picked.
    var modeOptions = ["ask", "plan", "approve-edits", "safe-auto", "auto-edits", "full-auto"].map(
      function (id) {
        return { id: id, label: modePresentationLabel(id, MODE_LABELS[id]) };
      }
    );
    var focusOptions = (boot.taskModes || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var formatOptions = (boot.outputFormats || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Models & Routing",
          "Choose the models OPai can use and understand its routing order.",
          ["project"]
        );
    if (part !== "routing") {
      h += '<div class="set-head">Defaults for new tasks</div>';
      h += selectRow(
        "Default run mode",
        "default_mode",
        modeOptions,
        MODE_LABELS[prefs.default_mode] ? prefs.default_mode : "safe-auto",
        "The approval mode OPai starts with for each new task."
      );
      h += selectRow("Task focus", "default_task_mode", focusOptions, ctx.state.focus);
      h += selectRow("Output format", "default_output_format", formatOptions, ctx.state.format);
      h += '<div class="set-note">Saved for this workspace and reflected in the composer immediately.</div>';
    }
    if (part === "general" || part === "defaults") return h;
    h += '<div class="set-head">Default intelligence</div>';
    h += selectRow(
      "Default model",
      "default_model",
      modelOptions,
      prefs.default_model || "auto",
      "Auto chooses an eligible route for each task; you can always override it in the composer."
    );
    h += '<div class="set-head">Your model picker</div>';
    h += '<div class="set-note">Global · ' + esc(modelOverrides.path || "~/.opai/models.json") + '. Show or hide models everywhere. Availability stays separate: unavailable models keep their reason.</div>';
    if ((modelOverrides.errors || []).length) {
      h += '<div class="set-note" role="alert">' + esc(modelOverrides.errors.join(" ")) + "</div>";
    }
    h += '<div data-model-override-error class="set-note" hidden></div>';
    var pickableModels = (modelSource || []).filter(function (m) {
      return m && m.kind !== "auto" && m.group !== "routing";
    });
    pickableModels.forEach(function (m) {
      var provider = String(m.provider || "").toLowerCase();
      var rawId = String(m.model || String(m.id || "").split(":").pop() || "");
      var visible = !ctx.isModelVisible || ctx.isModelVisible(m, modelOverrides);
      var unavailable = m.available === false
        ? (m.disabled_reason || "Unavailable")
        : (m.healthy === false ? (m.health_reason || "Currently unavailable") : "");
      h += '<label class="set-row"><span class="default-label"><span class="k">' + esc(m.label || m.id) + '</span>' +
        (unavailable ? '<span class="hint">' + esc(unavailable) + "</span>" : "") +
        '</span><input type="checkbox" data-model-visibility="' + esc(m.id) + '" data-model-provider="' + esc(provider) + '" data-model-override-id="' + esc(rawId) + '" aria-label="Show ' + esc(m.label || m.id) + '"' + (visible ? " checked" : "") + "></label>";
    });
    var providerNames = [];
    pickableModels.forEach(function (m) {
      var provider = String(m.provider || "").toLowerCase();
      if (m.kind === "account" && provider && providerNames.indexOf(provider) < 0) providerNames.push(provider);
    });
    h += '<div class="set-row"><span class="default-label"><span class="k">Add a custom model</span><span class="hint">Use a provider already available to this OPai install.</span></span></div>';
    h += '<div class="set-row model-custom-form"><select data-custom-provider aria-label="Custom model provider">' + providerNames.map(function (provider) { return '<option value="' + esc(provider) + '">' + esc(provider) + "</option>"; }).join("") + '</select><input data-custom-model aria-label="Custom model ID" placeholder="Model ID"><input data-custom-label aria-label="Custom model label" placeholder="Label"><select data-custom-capability aria-label="Custom model capability"><option value="balanced">Balanced</option><option value="fast">Fast</option><option value="best">Best</option></select><button type="button" class="btn" data-add-custom-model>Add model</button></div>';
    Object.keys(modelOverrides.providers || {}).sort().forEach(function (provider) {
      ((modelOverrides.providers[provider] || {}).models || []).forEach(function (entry) {
        h += '<div class="set-row"><span class="k">' + esc(provider + " · " + (entry.display || entry.id)) + '</span><button type="button" class="btn" data-remove-custom-provider="' + esc(provider) + '" data-remove-custom-id="' + esc(entry.id) + '">Remove</button></div>';
      });
    });
    h += '<button type="button" class="btn" data-reset-model-overrides>Reset model picker</button>';
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
    var part = ctx.settingsPart || "all";
    var money = function (value) {
      return value == null ? null : "$" + (+value).toFixed(2);
    };
    var capRow = function (label, cap, left) {
      var value = money(cap) || "No cap set";
      if (money(cap) && left != null) value += " · " + money(left) + " left";
      return row(esc, label, value);
    };
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Usage & Budgets",
          "Understand local usage estimates, provider allowances, and spend boundaries.",
          ["project", "local"]
        );
    if (part !== "safety") {
      h += '<div class="set-head">Current spend</div>';
      h += '<div class="stat-grid two">';
      h += statTile(esc, {
        label: "Spent today",
        value: money(firewall.spent_today || 0),
        sub: "Estimated from OPai's local ledger",
        mono: true,
      });
      h += statTile(esc, {
        label: "Spent this month",
        value: money(firewall.spent_month || 0),
        sub: "Estimated from OPai's local ledger",
        mono: true,
      });
      h += "</div>";
      h += '<div class="set-head">Budgets &amp; limits</div>';
      h += capRow("Daily cap", caps.daily_usd_limit, remaining.today_usd);
      h += capRow("Monthly cap", caps.monthly_usd_limit, remaining.month_usd);
      h += capRow("Per-task cap", caps.per_task_hard_limit_usd, null);
      h += '<div class="set-note">Spend estimates stay on this device. An unavailable value is never treated as zero.</div>';
      h += '<div class="set-head">Per-model limits</div>';
      h += usageCardsHtml(d, ctx);
    }
    if (part !== "financial") {
      h += '<div class="set-head">Cloud boundaries</div>';
      h +=
        '<div class="panic-card' +
        (firewall.panic ? " on" : "") +
        '"><div class="panic-body"><div class="panic-title">' +
        (firewall.panic ? "Local-only mode is on" : "Local-only mode is off") +
        "</div>" +
        '<div class="panic-desc">When on, every cloud call is refused while local models and saved work remain available.</div></div>' +
        '<button class="btn danger" id="setPanic">' +
        (firewall.panic ? "Allow cloud routes" : "Use local only") +
        "</button></div>";
      h += row(esc, "Paid cloud requests", firewall.cloud_gate ? "Ask every time" : "Allowed");
      h += '<div class="set-note">This reflects the current cloud-gate policy; change it from the active task when OPai requests authority.</div>';
    }
    return h;
  }

  // ---- Model Usage (official provider allowance windows) ----------------- //
  // Distinct from the Cost Firewall's per-model token budgets: this shows each
  // provider's own rate/usage window (Claude's 5-hour session, Gemini's daily
  // requests, Kimi's prepaid credit, …) from the most reliable available
  // source — never a fabricated number.
  // "Usage unavailable" reads like an error; it isn't one — it's an honest,
  // permanent capability boundary for account CLIs (no usage API exists).
  // "No usage API" says the same thing without implying something is broken.
  var USAGE_STATUS = {
    live: { label: "Live", tone: "ok" },
    stale: { label: "Stale", tone: "warn" },
    unavailable: { label: "No usage API", tone: "muted" },
    not_configured: { label: "Not connected", tone: "muted" },
    unsupported: { label: "Unsupported", tone: "muted" },
    loading: { label: "Refreshing…", tone: "muted" },
    error: { label: "Couldn't load", tone: "bad" },
  };

  function fmtCount(value) {
    return Number(value || 0).toLocaleString();
  }

  // Human, timezone-safe countdown from a seconds value (reset windows).
  function fmtDuration(seconds) {
    if (seconds == null || !isFinite(seconds) || seconds < 0) return "";
    var s = Math.floor(seconds);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (h > 0) return h + " hr " + m + " min";
    if (m > 0) return m + " min " + sec + " sec";
    return sec + " sec";
  }

  // "Checked N ago" from a unix-seconds timestamp, for the "last refreshed"
  // freshness readout the credit/no-bar cards use instead of a reset clock.
  function fmtAgo(unixSeconds) {
    if (unixSeconds == null || !isFinite(unixSeconds)) return "";
    var ago = Date.now() / 1000 - unixSeconds;
    if (ago < 0) return "just now";
    if (ago < 60) return "just now";
    if (ago < 3600) return Math.floor(ago / 60) + " min ago";
    if (ago < 86400) return Math.floor(ago / 3600) + " hr ago";
    return Math.floor(ago / 86400) + " d ago";
  }

  // One consistent card skeleton for every provider, regardless of which of
  // the three real data shapes it has (a bounded percentage, a remaining
  // credit amount, or nothing official at all): the same head row, one
  // headline stat at the same size/weight/position, one subtext line, an
  // optional bar, an optional OPai-tracked caption, and the same footer.
  // Only the *content* of each slot changes — never the layout — so a
  // provider without official data never looks like a different product.
  function usageCardHtml(esc, u) {
    var official = u.official || {};
    var status = u.status || "unavailable";
    var meta = USAGE_STATUS[status] || USAGE_STATUS.unavailable;
    var win = u.window || {};
    var tracked = u.opaiTracked;
    var hasBar = official.available && official.percent != null;
    var hasCredit = !hasBar && official.metric === "credit" && official.remaining != null;
    var pct = hasBar ? Math.max(0, Math.min(100, +official.percent)) : 0;

    // Resolve the one headline stat + its subtext, in the same shape either way.
    // subtextHtml carries pre-escaped markup only when a live countdown span
    // is needed (hasBar); every other case is plain text, escaped at render.
    var headline, headlineTone, subtext, subtextHtml, showTrackedRow;
    if (hasBar) {
      var used = official.used;
      var limit = official.limit;
      var metric = official.metric || win.metric || "";
      headline = Math.round(pct) + "% used";
      headlineTone = "";
      var figures = used != null && limit != null ? fmtCount(used) + " / " + fmtCount(limit) + " " + metric : "";
      subtextHtml = official.resetsAt
        ? esc(figures ? figures + " · resets in " : "resets in ") +
          '<span data-usage-countdown>' + esc(fmtDuration(official.resetsInSeconds)) + "</span>"
        : esc(figures);
      showTrackedRow = true;
    } else if (hasCredit) {
      headline = fmtCount(official.remaining) + " " + (official.currency || "") + " left";
      headlineTone = "credit";
      // Reuse the footer's detail sentence for *what* this is; the subtext's
      // job here is freshness ("last refreshed"), not a repeat of the detail.
      var ago = fmtAgo(official.observedAt);
      subtext = ago ? "Checked " + ago : "";
      showTrackedRow = true;
    } else if (tracked && (tracked.calls || tracked.tasks)) {
      // No official figure exists yet. OPai's own local tally becomes the
      // headline — same size/weight as a real number — so the card reads as
      // informative rather than broken. Still unmistakably not official.
      // A freshness readout matters here: "12 calls" alone could be from
      // weeks ago (OPai only sees traffic it personally routed — activity
      // through the bare CLI never touches this count at all).
      headline = fmtCount(tracked.calls) + " call" + (tracked.calls === 1 ? "" : "s") + " tracked";
      headlineTone = "tracked";
      var lastUsedAgo = fmtAgo(tracked.lastUsedAt);
      subtext =
        (tracked.tasks ? fmtCount(tracked.tasks) + " task" + (tracked.tasks === 1 ? "" : "s") + " · " : "") +
        (tracked.windowLabel || "recent") +
        (lastUsedAgo ? " · last used " + lastUsedAgo : "");
      showTrackedRow = false; // already the headline — don't repeat it below
    } else {
      headline = "No activity yet";
      headlineTone = "tracked";
      subtext = win.label || "";
      showTrackedRow = false;
    }

    var h =
      '<article class="usage2-card" data-usage-provider="' +
      esc(u.provider) +
      '" data-usage-supports-refresh="' +
      (u.supportsRefresh ? "1" : "0") +
      '">';
    // Head: provider name + window chip + status pill.
    h +=
      '<div class="usage2-head"><div class="usage2-titles"><span class="usage2-name">' +
      esc(u.displayName || u.provider) +
      '</span><span class="usage2-window">' +
      esc(win.label || "") +
      '</span></div><span class="usage2-pill ' +
      meta.tone +
      '" data-usage-pill>' +
      esc(meta.label) +
      "</span></div>";

    // Headline + subtext: identical structure and typography for every state.
    h +=
      '<div class="usage2-headline-row"><span class="usage2-headline ' +
      headlineTone +
      '" data-usage-primary>' +
      esc(headline) +
      "</span></div>";
    var subtextInner = subtextHtml != null ? subtextHtml : esc(subtext || "");
    if (subtextInner) {
      h +=
        '<div class="usage2-subtext"' +
        (hasBar && official.resetsAt ? ' data-usage-resets-at="' + esc(official.resetsAt) + '"' : "") +
        ">" +
        subtextInner +
        "</div>";
    }
    if (hasBar) {
      h +=
        '<div class="usage2-track" role="progressbar" aria-label="' +
        esc((u.displayName || u.provider) + " usage") +
        '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
        Math.round(pct) +
        '"><span style="width:' +
        pct +
        '%"></span></div>';
    }

    // OPai-tracked caption — always the same small row, whenever it isn't
    // already the headline above, so it's never presented as official.
    if (showTrackedRow && tracked && (tracked.calls || tracked.tasks)) {
      h +=
        '<div class="usage2-tracked"><span class="usage2-tracked-tag">OPai tracked</span>' +
        esc(
          fmtCount(tracked.calls) +
            " call" +
            (tracked.calls === 1 ? "" : "s") +
            (tracked.tasks
              ? " · " + fmtCount(tracked.tasks) + " task" + (tracked.tasks === 1 ? "" : "s")
              : "") +
            (tracked.tokens ? " · " + fmtCount(tracked.tokens) + " tokens" : "") +
            " · " +
            (tracked.windowLabel || "recent")
        ) +
        "</div>";
    }

    // Footer: explanatory detail + optional "check official usage" link.
    h += '<div class="usage2-foot">';
    h += '<span class="usage2-detail" data-usage-detail>' + esc(u.detail || "") + "</span>";
    if (u.checkUrl && !hasBar) {
      // data-ext (not target=_blank): the app intercepts these document-wide
      // and opens them via the native bridge (QDesktopServices) — a direct
      // navigation is blocked by the page's CSP and would silently no-op.
      h +=
        '<a class="usage2-link" href="' +
        esc(u.checkUrl) +
        '" data-ext="1">Check official usage</a>';
    }
    h += "</div></article>";
    return h;
  }

  function modelUsageHtml(d, ctx) {
    var esc = ctx.esc;
    var usage = Array.isArray(d.providerUsage) ? d.providerUsage : [];
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Usage & Budgets",
          "Understand local usage estimates, provider allowances, and spend boundaries.",
          ["project", "local"]
        );
    if (!usage.length) {
      h +=
        '<div class="callout-card"><div class="callout-body">No providers connected yet. Connect Claude, Codex, Gemini, Kimi, or another provider under ' +
        "Providers &amp; Connections, and their usage windows will appear here.</div></div>";
      return h;
    }
    h +=
      '<div class="set-note">Official usage is read from the provider (your calls’ rate-limit headers, or a safe metadata check). OPai-tracked counts are OPai’s own local tally, shown separately and never presented as the provider’s figure.</div>';
    h += '<div class="usage2-grid" id="modelUsageGrid">';
    usage.forEach(function (u) {
      h += usageCardHtml(esc, u);
    });
    h += "</div>";
    h +=
      '<div class="actions"><button class="btn" id="usageRefresh">Refresh live usage</button></div>';
    h +=
      '<div class="set-note">Live usage is cached for a few minutes and refreshed safely (no prompt is ever sent). Providers without a machine-readable usage endpoint link out to their official usage page.</div>';
    return h;
  }

  function permissionsHtml(d, ctx) {
    var esc = ctx.esc;
    var activeMode = modePresentationLabel(d.prefs.default_mode, MODE_LABELS[d.prefs.default_mode]);
    var active = (d.modePermissions || []).filter(function (mode) {
      return mode.active;
    })[0];
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Safety & Privacy",
          "See what OPai may do, when it asks, and where information may go.",
          ["project", "local"]
        );
    h +=
      '<div class="mode-hero"><div class="mode-hero-body"><div class="mode-hero-label">Current mode for this project</div>' +
      '<div class="mode-hero-value">' +
      esc(activeMode) +
      "</div></div>" +
      (active ? '<div class="mode-hero-summary">' + esc(active.summary) + "</div>" : "") +
      "</div>";
    // Bypass Permissions is a switch, not a mode -- the same shape as Claude
    // Code's --dangerously-skip-permissions. It sits above the per-mode rows
    // because it overrides all of them, and it composes with whichever mode is
    // selected instead of replacing it, so turning it off returns the user to
    // the mode they were already working in.
    var bypassOn = d.prefs.bypass_permissions === true;
    h += '<div class="set-head">Bypass permissions</div>';
    h +=
      '<label class="set-row set-row-toggle"><span class="k">Skip every confirmation' +
      '<span class="set-note">Applies on top of your current mode (' +
      esc(activeMode) +
      '), so edits, commands, pushes and merges all run unattended. ' +
      "Turn it off to return to that mode's own rules.</span></span>" +
      '<input type="checkbox" id="setBypassPermissions" aria-label="Bypass permissions"' +
      (bypassOn ? " checked" : "") +
      "></label>";
    if (bypassOn) {
      h +=
        '<div class="set-note set-warn">Bypass is on: nothing will stop for your approval, ' +
        "including force-push and deletes.</div>";
    }
    h += '<div class="set-head">Tool permissions · ' + esc(activeMode) + "</div>";
    h +=
      '<div class="set-note">What OPai may do this turn under your current run mode. Allow = does it without asking; Ask = pauses for your OK; Blocked = refused.</div>';
    (d.permissions || []).forEach(function (p) {
      h +=
        '<div class="perm"><span class="k">' +
        esc(p.label) +
        (p.note ? '<span class="perm-note">' + esc(modePresentationCopy(p.note)) + "</span>" : "") +
        '</span><span class="s ' +
        p.state +
        '">' +
        esc(p.state) +
        "</span></div>";
    });
    // Per-mode comparison (#239): how the five run modes differ, derived from
    // the same permission rules (not re-invented). The active mode is marked.
    // The meter is presentational: authority grows down the ladder.
    if ((d.modePermissions || []).length) {
      var METER = { ask: 16, plan: 30, "approve-edits": 48, "safe-auto": 64, "auto-edits": 82, "full-auto": 100 };
      h += '<div class="set-head">Run modes</div>';
      h +=
        '<div class="set-note">Switch modes from the composer. Auto-apply acts without asking, and like every mode it stays selected until you change it.</div>';
      d.modePermissions.forEach(function (mode) {
        var width = METER[mode.id] || 20;
        h +=
          '<div class="mode-row' +
          (mode.active ? " active" : "") +
          (mode.id === "full-auto" ? " highest" : "") +
          '"><span class="mode-meter" aria-hidden="true"><span style="width:' +
          width +
          '%"></span></span><span class="k">' +
          esc(modePresentationLabel(mode.id, mode.label)) +
          (mode.active ? ' <span class="mode-active">current</span>' : "") +
          '</span><span class="mode-summary">' +
          esc(mode.summary) +
          "</span></div>";
      });
      h +=
        '<div class="set-note">In Auto-apply, a message with no explicit read-only wording (no "explain", "review only", "do not edit", etc.) is treated as edit-capable by default, so you don\'t have to phrase every request as a command. This mode also pushes, opens and merges pull requests without stopping to confirm; use Safe Auto or Approve Edits if you want those to ask first.</div>';
    }
    return h;
  }

  // Prompt Library and the seven Insights dashboards used to sit in the app
  // sidebar. They now live in the related Plugins, Agents, or Advanced
  // destination while remaining routable from search and deep links.
  function toolsHtml(d, ctx) {
    var esc = ctx.esc;
    var settingsPart = ctx.settingsPart || "all";
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Advanced",
          "Diagnostics, supporting tools, updates, and build information.",
          ["app", "local"]
        );
    var groups = [
      {
        head: "Library",
        items: [
          { go: "prompts", title: "Prompt Library", sub: "Saved prompts you can reuse and edit" },
        ],
      },
      {
        head: "Insights",
        items: [
          { go: "home", title: "Money Saved", sub: "What local-first routing has avoided spending" },
          { go: "firewall", title: "Cost Firewall", sub: "Caps, spend and what stopped a run" },
          { go: "context", title: "Context Waste", sub: "Tokens sent that did not need sending" },
          { go: "benchmark", title: "Benchmark", sub: "How the models compare on your work" },
          { go: "agents", title: "Agents", sub: "Background runs and their outcomes" },
          { go: "proof", title: "Proof Bundle", sub: "Evidence you can hand to someone else" },
          { go: "workflows", title: "Workflows", sub: "Repeatable multi-step tasks" },
        ],
      },
    ];
    groups.forEach(function (group) {
      var items = group.items.filter(function (item) {
        if (settingsPart === "agents") {
          return ["agents", "workflows", "proof"].indexOf(item.go) >= 0;
        }
        if (settingsPart === "plugins") return item.go === "prompts";
        if (settingsPart === "advanced") {
          return ["agents", "workflows", "proof", "prompts"].indexOf(item.go) < 0;
        }
        return true;
      });
      if (!items.length) return;
      h += '<div class="set-head">' + esc(group.head) + "</div>";
      h +=
        '<div class="quick-grid">' +
        items
          .map(function (tile) {
            return (
              '<button class="quick-tile" type="button" data-go-view="' +
              esc(tile.go) +
              '"><span class="quick-body"><span class="quick-title">' +
              esc(tile.title) +
              '</span><span class="quick-sub">' +
              esc(tile.sub) +
              "</span></span></button>"
            );
          })
          .join("") +
        "</div>";
    });
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
    var h = ctx.settingsBodyOnly
      ? ""
      : heroHtml(
          esc,
          "Safety & Privacy",
          "See what OPai may do, when it asks, and where information may go.",
          ["project", "local"]
        );
    h +=
      '<div class="callout-card accent"><div class="callout-title">Data stays on this device</div>' +
      '<div class="callout-body">Redacted saved chat, the ledger, and audit history are kept locally, per workspace.</div></div>';
    h += '<div class="set-head">Privacy &amp; data</div>';
    statements.forEach(function (t) {
      h += '<div class="cb">• ' + esc(t) + "</div>";
    });
    h += '<div class="set-head">Saved chat &amp; recents</div>';
    h +=
      '<div class="set-note">Saved chat is stored redacted on this machine, per workspace.</div>';
    h +=
      '<div class="danger-zone"><div class="danger-body"><strong>Clear previous chats &amp; recents</strong>' +
      '<div class="set-note">Immediate and cannot be undone.</div></div>' +
      '<button class="btn danger" id="settingsClearRecents">Clear previous chats</button></div>';
    return h;
  }

  function appearanceHtml(d, ctx) {
    var esc = ctx.esc;
    var prefs = d.prefs || {};
    var pref = function (snake, camel) {
      return prefs[snake] != null ? prefs[snake] : prefs[camel];
    };
    var density = prefs.density === "compact" ? "compact" : "comfortable";
    var rawResponseDensity = pref("response_density", "responseDensity");
    var responseDensity =
      rawResponseDensity === "compact" || rawResponseDensity === "detailed"
        ? rawResponseDensity
        : "balanced";
    var rawMotion = pref("reduced_motion", "reducedMotion");
    var motion =
      rawMotion === "on" || rawMotion === "off"
        ? rawMotion
        : "system";
    var activityCopy = pref("activity_copy", "activityCopy") === "off" ? "off" : "on";
    var seg = function (key, current, options) {
      return (
        '<div class="seg" role="radiogroup" aria-label="' +
        esc(key.replace(/_/g, " ")) +
        '" data-appearance-key="' +
        esc(key) +
        '">' +
        options
          .map(function (option) {
            var active = option.id === current;
            return (
              '<button type="button" data-value="' +
              esc(option.id) +
              '" role="radio" aria-checked="' +
              (active ? "true" : "false") +
              '" tabindex="' +
              (active ? "0" : "-1") +
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
    var rawComposerStyle = pref("composer_style", "composerStyle");
    var composerStyle =
      rawComposerStyle === "single" || rawComposerStyle === "command"
        ? rawComposerStyle
        : "toolbar";
    // A distinct key (not data-appearance-key) so it routes to the composer
    // controller instead of the document-root appearance handler.
    var composerSeg = function (current, options) {
      return (
        '<div class="seg" role="radiogroup" aria-label="Composer style" data-composer-style-key="composer_style">' +
        options
          .map(function (option) {
            var active = option.id === current;
            return (
              '<button type="button" data-value="' +
              esc(option.id) +
              '" role="radio" aria-checked="' +
              (active ? "true" : "false") +
              '" tabindex="' +
              (active ? "0" : "-1") +
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
    var h = heroHtml(
      esc,
      "Appearance",
      "Choose how OPai looks and presents work.",
      ["app", "instant"]
    );
    h += '<div class="set-head">Appearance</div>';
    // The saved choice, or -- when a payload predates the theme -- whatever
    // the window is actually wearing, so the picker never contradicts it.
    var appliedTheme = global.OPaiTheme ? global.OPaiTheme.current().preference : null;
    h +=
      '<div class="appearance-row appearance-row-theme"><div class="appearance-label"><span class="k">Theme</span><span class="hint">Light is soft daylight. Viber Coder is OPai\'s original night sky. Dark is midnight: all black and grey, no colour. Vesta is warm cream with the logo\'s dusty rose and sky blue. System follows your OS: Light by day, Viber Coder by night.</span></div>' +
      themeChoices(esc, pref("theme", "theme") || appliedTheme) +
      "</div>";
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Composer style</span><span class="hint">How the prompt box is arranged. Toolbar keeps everything one click away; Single line is the smallest footprint; Command bar is keyboard-first with #file, /mode, and @model tokens.</span></div>' +
      composerSeg(composerStyle, [
        { id: "toolbar", label: "Toolbar" },
        { id: "single", label: "Single line" },
        { id: "command", label: "Command bar" },
      ]) +
      "</div>";
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Response detail</span><span class="hint">Compact prioritizes the outcome, Balanced keeps key evidence nearby, and Detailed keeps supporting sections open.</span></div>' +
      seg("response_density", responseDensity, [
        { id: "compact", label: "Compact" },
        { id: "balanced", label: "Balanced" },
        { id: "detailed", label: "Detailed" },
      ]) +
      "</div>";
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
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Copy activity</span><span class="hint">Lets you drag-select and copy the whole AI activity rail — stage line and every step — not just the final answer. Handy for debugging; off restores the app\'s normal no-select chrome there.</span></div>' +
      seg("activity_copy", activityCopy, [
        { id: "on", label: "On" },
        { id: "off", label: "Off" },
      ]) +
      "</div>";
    return h;
  }

  // The theme picker. Each option previews the palette it names by wearing it:
  // the tile sets data-theme on itself, so design-tokens.css paints it with
  // the real tokens and the preview can never drift from the theme. System is
  // the two it switches between, split on a diagonal.
  var THEME_CHOICES = [
    { id: "light", label: "Light", panes: ["light"] },
    { id: "viber-coder", label: "Viber Coder", panes: ["viber-coder"] },
    { id: "dark", label: "Dark", panes: ["dark"] },
    { id: "vesta", label: "Vesta", panes: ["vesta"] },
    { id: "system", label: "System", panes: ["light", "viber-coder"] },
  ];

  function normalizeTheme(value) {
    for (var i = 0; i < THEME_CHOICES.length; i += 1) {
      if (THEME_CHOICES[i].id === value) return value;
    }
    return "viber-coder";
  }

  function themeChoices(esc, value) {
    var current = normalizeTheme(value);
    var pane = function (theme) {
      return (
        '<span class="theme-preview-pane" data-theme="' + esc(theme) + '">' +
        '<span class="tp-rail"></span>' +
        '<span class="tp-main"><span class="tp-accent"></span><span class="tp-line"></span><span class="tp-line tp-short"></span>' +
        '<span class="tp-composer"><span class="tp-send"></span></span></span>' +
        "</span>"
      );
    };
    return (
      '<div class="theme-choices" role="radiogroup" aria-label="Theme" data-appearance-key="theme">' +
      THEME_CHOICES.map(function (option) {
        var active = option.id === current;
        return (
          '<button type="button" class="theme-choice' + (active ? " active" : "") +
          '" data-value="' + esc(option.id) +
          '" role="radio" aria-checked="' + (active ? "true" : "false") +
          '" tabindex="' + (active ? "0" : "-1") + '">' +
          '<span class="theme-preview" aria-hidden="true">' + option.panes.map(pane).join("") + "</span>" +
          '<span class="theme-choice-label">' + esc(option.label) + "</span>" +
          "</button>"
        );
      }).join("") +
      "</div>"
    );
  }

  // Read-only Settings projection of the canonical application-wide updater.
  // Actions remain available in the persistent bottom-left control.
  function updateStatusHtml(esc, update) {
    var u = update || {};
    var operation = u.operation || {};
    var candidate = operation.candidate || {};
    var state = String(operation.state || "idle");
    var labels = {
      idle: "Not checked yet", checking: "Checking for updates", up_to_date: "You're on the latest version",
      available: "Update available", downloading: "Downloading update", verifying: "Verifying update",
      ready_to_install: "Ready to restart", waiting_for_idle: "Waiting for active work",
      install_on_quit: "Installs on quit", deferred: "Update deferred", failed_retriable: "Update can be retried",
      failed_terminal: "Update blocked by verification", policy_blocked: "Managed by update policy",
      unsupported_install: "Manual update required", restarting: "Restarting into update",
      health_checking: "Checking updated application", rollback_pending: "Recovery required",
      needs_attention: "Update needs attention", rolled_back: "Update rolled back", completed: "Update completed",
      unavailable: "Update status unavailable",
    };
    var description = operation.safe_diagnostic || (candidate.version ? "Target OPai " + candidate.version + "." : "");
    var discovery = u.discovery || {};
    var summary = discovery.summary || {};
    var devCheckout = (u.installed || {}).install_type === "source_checkout";
    // Ownership decides whether applying is even possible here, exactly as it
    // does on the persistent control: an install another tool owns must not be
    // offered a button that would act on it.
    var applyAllowed = devCheckout && discovery.self_updatable !== false;

    return (
      '<div class="update-card ' + (state === "available" || state === "ready_to_install" ? "available" : "unknown") + '" data-update-status="' + esc(state) + '">' +
      '<div class="update-head"><span class="update-dot"></span><span class="update-title">' +
      esc(summary.title || labels[state] || "Update status") +
      "</span></div>" +
      // One plain sentence, from the backend. No timings, no cache provenance,
      // no shell commands: those are diagnostics and live in
      // `opai update doctor`.
      '<div class="update-desc">' + esc(summary.message || description) + "</div>" +
      '<div class="actions"><button class="btn ghost" id="settingsCheckUpdate">Check now</button>' +
      (state === "unsupported_install" && applyAllowed
        ? ' <button class="btn ghost" id="settingsApplyUpdate">Update now</button>'
        : "") +
      // An update sitting on disk is not running yet, and this card is where
      // someone goes to check. Offered only when the app has established it
      // can start itself again.
      (state === "completed" && u.restart_available
        ? ' <button class="btn ghost" id="settingsRestartUpdate">Restart now</button>'
        : "") +
      "</div>" +
      "</div>"
    );
  }

  function autoUpdateHtml(esc, d) {
    var update = (d.about && d.about.update) || {};
    var policy = update.policy || {};
    var on = !!policy.automatic_downloads;
    var install = !!policy.automatic_install_on_quit;
    // The same switch means something different per install type, and saying
    // "downloads" to someone running from git would hide what it actually
    // does to their working tree.
    var downloadHint = (update.installed || {}).install_type === "source_checkout"
      ? "Discovery stays on. When enabled, OPai fast-forwards this checkout to origin/main by itself — only with a clean working tree, only as a fast-forward, and never while work is running."
      : "Discovery stays on. When enabled, signed packaged updates download and verify in the background.";
    var option = function (value, label, active, disabled) {
      return (
        '<button type="button" class="seg-btn' + (active ? " active" : "") + '"' +
        ' data-value="' + value + '" role="radio" aria-checked="' + (active ? "true" : "false") + '"' +
        ' tabindex="' + (active ? "0" : "-1") + '"' +
        (disabled ? ' disabled aria-disabled="true"' : "") + '>' +
        esc(label) + "</button>"
      );
    };
    return (
      '<div class="appearance-row" data-update-policy="automatic_downloads">' +
      '<div class="appearance-label"><span class="k">Automatic downloads</span>' +
      '<span class="hint">' + esc(downloadHint) + "</span></div>" +
      '<div class="seg" role="radiogroup" aria-label="Automatic updates">' +
      option("off", "Off", !on) +
      option("on", "On", on) +
      "</div></div>" +
      '<div class="appearance-row" data-update-policy="automatic_install_on_quit">' +
      '<div class="appearance-label"><span class="k">Install on quit</span>' +
      '<span class="hint">Explicit opt-in. Requires automatic downloads, installs only at a safe quit boundary, and active work is never interrupted silently.</span></div>' +
      '<div class="seg" role="radiogroup" aria-label="Install updates on quit">' +
      option("off", "Off", !install, false) + option("on", "On", install, !on) +
      "</div></div>"
    );
  }

  function aboutHtml(d, ctx) {
    var esc = ctx.esc;
    if (!(d.about && d.about.version)) return "";
    var build = d.about.build || {};
    var identity = d.about.release_identity || {};
    var fingerprint = String(build.assetFingerprint || "");
    var runtimeSource = String(build.runtimeSource || "").replace(/_/g, " ");
    var artifact = [
      identity.platform,
      identity.architecture,
      identity.install_type
        ? String(identity.install_type).replace(/_/g, " ")
        : "",
    ]
      .filter(Boolean)
      .join(" · ");
    return (
      (ctx.settingsBodyOnly
        ? ""
        : heroHtml(
            esc,
            "Advanced",
            "Diagnostics, supporting tools, updates, and build information.",
            ["app", "local"]
          )) +
      '<div class="set-head">About</div>' +
      '<div class="stat-grid two">' +
      statTile(esc, { label: "Version", value: d.about.version, mono: true }) +
      statTile(esc, { label: "Release stage", value: d.about.release_stage || "—" }) +
      "</div>" +
      '<div class="set-head">Runtime build</div>' +
      '<div class="stat-grid two">' +
      statTile(esc, {
        label: "Build identity",
        value: identity.build_id || "unknown",
        mono: true,
      }) +
      statTile(esc, {
        label: "Artifact",
        value: artifact || "unknown",
      }) +
      "</div>" +
      '<div class="set-head">Hosted build</div>' +
      '<div class="stat-grid two">' +
      statTile(esc, {
        label: "Asset build",
        value: fingerprint ? fingerprint.slice(0, 12) : "unknown",
        mono: true,
      }) +
      statTile(esc, {
        label: "Runtime source",
        value: runtimeSource || "unknown",
      }) +
      "</div>" +
      (fingerprint
        ? '<div class="set-note">Full asset fingerprint: <span class="mono">' +
          esc(fingerprint) +
          "</span> · " +
          esc(build.assetCount || 0) +
          " hosted files</div>"
        : "") +
      '<div class="set-head">Updates</div>' +
      '<div id="settingsUpdateCard">' +
      updateStatusHtml(esc, d.about.update) +
      "</div>" +
      autoUpdateHtml(esc, d) +
      // Replay the first-run tour on demand (#250).
      '<div class="set-head">Tour</div>' +
      '<div class="set-note">New here, or want a refresher? Replay the three-step welcome tour.</div>' +
      '<div class="actions"><button class="btn" id="settingsReplayTour">Replay tour</button></div>'
    );
  }

  function generalHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "General",
        "Set the defaults OPai uses when you begin new work.",
        ["project", "instant"]
      ) +
      settingsSubsection(
        ctx.esc,
        "defaults",
        "Defaults for new tasks",
        modelsHtml(d, settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "general" }))
      )
    );
  }

  function modelsRoutingHtml(d, ctx) {
    var firewall = d.firewall || {};
    var profile =
      '<div class="set-head">Routing preference</div>' +
      '<div class="default-row"><div class="default-label"><span class="k">Current profile</span>' +
      '<span class="hint">The active policy that balances capability, availability, and cost.</span></div>' +
      '<span class="v">' +
      ctx.esc(firewall.profile || "Not reported") +
      "</span></div>";
    return (
      heroHtml(
        ctx.esc,
        "Models & Routing",
        "Choose available intelligence and understand how OPai selects a route.",
        ["project", "local"]
      ) +
      settingsSubsection(ctx.esc, "routing", "Routing preference", profile) +
      settingsSubsection(
        ctx.esc,
        "models",
        "Models",
        modelsHtml(d, settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "routing" }))
      )
    );
  }

  function connectionsHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "Connections",
        "Connect AI services and resolve problems where they occur.",
        ["app", "local"]
      ) +
      settingsSubsection(
        ctx.esc,
        "connections",
        "Provider connections",
        providersHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      )
    );
  }

  function usageBudgetsHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "Usage & Budgets",
        "See what is being consumed, which values are estimates, and where limits apply.",
        ["project", "local"]
      ) +
      settingsSubsection(
        ctx.esc,
        "budgets",
        "Budgets & limits",
        firewallHtml(
          d,
          settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "financial" })
        )
      ) +
      settingsSubsection(
        ctx.esc,
        "provider-usage",
        "Provider usage",
        modelUsageHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      ) +
      settingsSubsection(
        ctx.esc,
        "balances",
        "Provider balances",
        balanceHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      )
    );
  }

  function safetyPrivacyHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "Safety & Privacy",
        "See what OPai may do, when it asks, and where information may go.",
        ["project", "local"]
      ) +
      settingsSubsection(
        ctx.esc,
        "cloud",
        "Cloud boundaries",
        firewallHtml(d, settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "safety" }))
      ) +
      settingsSubsection(
        ctx.esc,
        "permissions",
        "Agent permissions",
        permissionsHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      ) +
      settingsSubsection(
        ctx.esc,
        "privacy",
        "Data & privacy",
        privacyHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      )
    );
  }

  function agentsHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "Agents",
        "Review agent work and the repeatable workflows that coordinate it.",
        ["project", "local"]
      ) +
      settingsSubsection(
        ctx.esc,
        "agent-tools",
        "Agent tools",
        toolsHtml(
          d,
          settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "agents" })
        )
      )
    );
  }

  function pluginsHtml(d, ctx) {
    var esc = ctx.esc;
    return (
      heroHtml(
        esc,
        "Plugins",
        "Keep reusable tools and connected capabilities in one place.",
        ["app", "local"]
      ) +
      settingsSubsection(
        esc,
        "built-in-tools",
        "Built-in tools",
        toolsHtml(
          d,
          settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "plugins" })
        )
      ) +
      settingsSubsection(
        esc,
        "plugin-connections",
        "Plugin connections",
        '<div class="set-head">Connected capabilities</div>' +
          '<div class="set-note">Installable plugin management is not available in this build. Provider and GitHub integrations remain available in Connections.</div>' +
          '<div class="actions"><button class="btn" type="button" data-settings-target="connections">Open Connections</button></div>'
      )
    );
  }

  function workspaceHtml(d, ctx) {
    var esc = ctx.esc;
    var state = ctx.state || {};
    var boot = state.boot || {};
    var workspace = boot.workspace || {};
    var prefs = d.prefs || {};
    var github = d.github || {};
    var accounts = d.accounts || boot.accounts || [];
    var connectedCount = accounts.filter(function (account) {
      return !!account.connected;
    }).length;
    var activeMode = (boot.modes || []).find(function (mode) {
      return mode.id === (prefs.default_mode || boot.selectedMode || "safe-auto");
    });
    var projectName = workspace.label || workspace.name || "Current project";
    var projectRoot = workspace.root || "No project selected";
    var branch = workspace.branch || "Not reported";
    var fileCount = Number(workspace.file_count || workspace.fileCount || 0);
    var modeLabel = modePresentationLabel(
      activeMode && activeMode.id,
      activeMode && activeMode.label
    );
    var githubValue = github.connected
      ? github.login
        ? "Connected as " + github.login
        : "Connected"
      : "Not connected";

    function workspaceRow(label, hint, value, action) {
      return (
        '<div class="workspace-setting-row"><div class="workspace-setting-copy"><span class="k">' +
        esc(label) +
        '</span><span class="hint">' +
        esc(hint) +
        '</span></div><div class="workspace-setting-control"><span class="v">' +
        esc(value) +
        "</span>" +
        (action || "") +
        "</div></div>"
      );
    }

    var tabs = [
      ["projects", "general", "Projects"],
      ["terminal", "advanced", "Terminal"],
      ["git", "models", "Git & GitHub"],
      ["rules", "safety", "Rules"],
      ["environment", "plugins", "Environment"],
    ];
    var tabHtml =
      '<nav class="workspace-tabs" id="workspaceTabs" aria-label="Workspace settings">' +
      tabs
        .map(function (tab, index) {
          return (
            '<button class="workspace-tab' +
            (index === 0 ? " active" : "") +
            '" type="button" data-workspace-tab="' +
            esc(tab[0]) +
            '" aria-pressed="' +
            (index === 0 ? "true" : "false") +
            '"><span class="workspace-tab-icon" aria-hidden="true">' +
            (ICONS[tab[1]] || "") +
            "</span>" +
            esc(tab[2]) +
            "</button>"
          );
        })
        .join("") +
      "</nav>";

    return (
      heroHtml(
        esc,
        "Workspace",
        "Configure your development environment and how OPai works with your code."
      ) +
      tabHtml +
      settingsSubsection(
        esc,
        "projects",
        "Projects",
        '<div class="set-head">Projects</div><div class="set-note">Set up how OPai opens and identifies your projects.</div>' +
          workspaceRow(
            "Current project",
            projectName,
            projectRoot,
            '<button class="btn" id="settingsOpenWorkspace" type="button">Browse</button>'
          ) +
          workspaceRow("Current branch", "Reported by Git", branch, "")
      ) +
      settingsSubsection(
        esc,
        "terminal",
        "Terminal",
        '<div class="set-head">Terminal</div><div class="set-note">Choose how OPai approaches command-line work.</div>' +
          workspaceRow("Default shell", "Inherited from this device", "System default", "") +
          workspaceRow(
            "Command approval mode",
            "Controls when OPai pauses before running commands.",
            modeLabel || "Auto",
            '<button class="btn ghost" type="button" data-settings-target="safety">Review rules</button>'
          )
      ) +
      settingsSubsection(
        esc,
        "git",
        "Git & GitHub",
        '<div class="set-head">Git &amp; GitHub</div><div class="set-note">Repository identity and remote collaboration.</div>' +
          workspaceRow("Repository branch", "Current checkout", branch, "") +
          workspaceRow(
            "GitHub",
            "Push and pull request access is managed as a connection.",
            githubValue,
            '<button class="btn ghost" type="button" data-settings-target="connections">Manage</button>'
          )
      ) +
      settingsSubsection(
        esc,
        "rules",
        "Rules",
        '<div class="set-head">Rules</div><div class="set-note">Project permissions remain explicit and reviewable.</div>' +
          workspaceRow(
            "Active run mode",
            "Applied to new tasks in this project.",
            modeLabel || "Auto",
            '<button class="btn ghost" type="button" data-settings-target="safety">Open Safety &amp; Privacy</button>'
          )
      ) +
      settingsSubsection(
        esc,
        "environment",
        "Environment",
        '<div class="set-head">Environment</div><div class="set-note">A concise view of the local project context OPai can use.</div>' +
          workspaceRow("Indexed files", "Available project context", fileCount ? String(fileCount) : "Not indexed", "") +
          workspaceRow("AI connections", "Available provider accounts", String(connectedCount), "")
      )
    );
  }

  function advancedHtml(d, ctx) {
    return (
      heroHtml(
        ctx.esc,
        "Advanced",
        "Open supporting tools, inspect this build, and manage updates.",
        ["app", "local"]
      ) +
      settingsSubsection(
        ctx.esc,
        "tools",
        "Tools & Insights",
        toolsHtml(
          d,
          settingsContext(ctx, { settingsBodyOnly: true, settingsPart: "advanced" })
        )
      ) +
      settingsSubsection(
        ctx.esc,
        "about",
        "About & updates",
        aboutHtml(d, settingsContext(ctx, { settingsBodyOnly: true }))
      )
    );
  }

  var SEARCH_ITEMS = {
    general: [
      { label: "Default run mode", group: "Task defaults", selector: '[data-default-pref="default_mode"]', keywords: "approval autonomy ask plan safe auto" },
      { label: "Task focus", group: "Task defaults", selector: '[data-default-pref="default_task_mode"]', keywords: "coding writing general" },
      { label: "Output format", group: "Task defaults", selector: '[data-default-pref="default_output_format"]', keywords: "concise normal response" },
    ],
    models: [
      { label: "Default model", group: "Models", selector: '[data-default-pref="default_model"]', keywords: "model intelligence auto provider" },
      { label: "Routing preference", group: "Routing", subsectionId: "routing", keywords: "profile cost balanced highest intelligence firewall" },
      { label: "Model picker", group: "Models", subsectionId: "models", keywords: "show hide available provider models" },
      { label: "Add a custom model", group: "Models", selector: "[data-add-custom-model]", keywords: "custom model id capability" },
      { label: "Reset model picker", group: "Models", selector: "[data-reset-model-overrides]", keywords: "restore models defaults" },
      { label: "Route order", group: "Routing", subsectionId: "models", keywords: "local first fallback provider priority" },
    ],
    agents: [
      { label: "Agents", group: "Agent tools", selector: '[data-go-view="agents"]', keywords: "background runs outcomes delegation" },
      { label: "Workflows", group: "Agent tools", selector: '[data-go-view="workflows"]', keywords: "repeatable multi step tasks automation" },
      { label: "Proof Bundle", group: "Agent tools", selector: '[data-go-view="proof"]', keywords: "evidence handoff results" },
    ],
    plugins: [
      { label: "Prompt Library", group: "Built-in tools", selector: '[data-go-view="prompts"]', keywords: "saved prompt template reusable" },
      { label: "Connections", group: "Plugin connections", selector: '[data-settings-target="connections"]', keywords: "integration provider github capability" },
    ],
    workspace: [
      { label: "Current project", group: "Projects", subsectionId: "projects", keywords: "workspace folder directory browse files" },
      { label: "Terminal", group: "Terminal", subsectionId: "terminal", keywords: "shell command approval" },
      { label: "Git & GitHub", group: "Git & GitHub", subsectionId: "git", keywords: "repository branch push pull request" },
      { label: "Rules", group: "Rules", subsectionId: "rules", keywords: "permission run mode project" },
      { label: "Environment", group: "Environment", subsectionId: "environment", keywords: "indexed files providers local context" },
    ],
    connections: [
      { label: "Provider connections", group: "Connections", subsectionId: "connections", keywords: "provider account api key credential sign in connect subscription" },
      { label: "Connection Doctor", group: "Connections", selector: ".connection-doctor", keywords: "health test repair failed degraded cli" },
      { label: "GitHub connection", group: "Connections", selector: "[data-github-card]", keywords: "github push pull request pat" },
      { label: "Disconnect provider", group: "Connections", selector: "[data-disconnect-account]", keywords: "remove sign out account" },
    ],
    usage: [
      { label: "Current spend", group: "Usage", subsectionId: "budgets", keywords: "spent today month estimate cost" },
      { label: "Daily cap", group: "Budgets & limits", subsectionId: "budgets", keywords: "firewall daily budget spending limit" },
      { label: "Monthly cap", group: "Budgets & limits", subsectionId: "budgets", keywords: "firewall monthly budget spending limit" },
      { label: "Per-task cap", group: "Budgets & limits", subsectionId: "budgets", keywords: "firewall task budget spending limit" },
      { label: "Per-model limits", group: "Budgets & limits", selector: "[data-model-id]", keywords: "usage token soft limit model" },
      { label: "Provider usage", group: "Usage", subsectionId: "provider-usage", keywords: "quota allowance rate window reset requests tokens" },
      { label: "Provider balances", group: "Usage", subsectionId: "balances", keywords: "balance credit remaining top up recharge funds money" },
      { label: "Manual balance", group: "Provider balances", selector: "[data-save-balance]", keywords: "enter currency save tracked" },
    ],
    safety: [
      { label: "Local-only mode", group: "Cloud boundaries", selector: "#setPanic", keywords: "panic firewall block cloud offline local" },
      { label: "Paid cloud requests", group: "Cloud boundaries", subsectionId: "cloud", keywords: "cloud gate confirm network paid" },
      { label: "Bypass permissions", group: "Agent permissions", selector: "#setBypassPermissions", keywords: "permissions skip confirmation autonomy dangerous" },
      { label: "Tool permissions", group: "Agent permissions", subsectionId: "permissions", keywords: "allow ask block file edit shell command network push" },
      { label: "Run modes", group: "Agent permissions", subsectionId: "permissions", keywords: "ask plan manual auto accept edits" },
      { label: "Data storage", group: "Data & privacy", subsectionId: "privacy", keywords: "privacy local telemetry prompts history redacted" },
      { label: "Clear previous chats", group: "Data & privacy", selector: "#settingsClearRecents", keywords: "delete saved chat recents history" },
    ],
    appearance: [
      { label: "Theme", group: "Appearance", selector: '[data-appearance-key="theme"]', keywords: "light mode dark mode night mode midnight black oled viber coder vesta cream rose pink sky blue pastel warm system theme colour color day bright" },
      { label: "Composer style", group: "Appearance", selector: '[data-composer-style-key="composer_style"]', keywords: "toolbar single line command bar" },
      { label: "Response detail", group: "Appearance", selector: '[data-appearance-key="response_density"]', keywords: "compact balanced detailed output" },
      { label: "Density", group: "Appearance", selector: '[data-appearance-key="density"]', keywords: "comfortable compact spacing" },
      { label: "Reduced motion", group: "Appearance", selector: '[data-appearance-key="reduced_motion"]', keywords: "animation accessibility system" },
      { label: "Copy activity", group: "Appearance", selector: '[data-appearance-key="activity_copy"]', keywords: "select log work" },
    ],
    advanced: [
      { label: "Insights", group: "Tools & Insights", subsectionId: "tools", keywords: "money saved context benchmark dashboard" },
      { label: "Update status", group: "About & updates", selector: "#settingsUpdateCard", keywords: "update version latest check restart" },
      { label: "Automatic downloads", group: "About & updates", selector: '[data-update-policy="automatic_downloads"]', keywords: "update download policy" },
      { label: "Install on quit", group: "About & updates", selector: '[data-update-policy="automatic_install_on_quit"]', keywords: "update restart policy" },
      { label: "Build information", group: "About & updates", subsectionId: "about", keywords: "about version release artifact fingerprint runtime source" },
      { label: "Replay tour", group: "About & updates", selector: "#settingsReplayTour", keywords: "onboarding welcome help" },
    ],
  };

  var SECTION_ALIASES = {
    overview: { sectionId: "general", subsectionId: "defaults" },
    providers: { sectionId: "connections", subsectionId: "connections" },
    balance: { sectionId: "usage", subsectionId: "balances" },
    firewall: { sectionId: "usage", subsectionId: "budgets" },
    permissions: { sectionId: "safety", subsectionId: "permissions" },
    privacy: { sectionId: "safety", subsectionId: "privacy" },
    tools: { sectionId: "advanced", subsectionId: "tools" },
    about: { sectionId: "advanced", subsectionId: "about" },
  };

  function resolveSettingsTarget(id) {
    var clean = String(id || "").toLowerCase().replace(/[^\w-]/g, "");
    if (SECTION_ALIASES[clean]) {
      return {
        sectionId: SECTION_ALIASES[clean].sectionId,
        subsectionId: SECTION_ALIASES[clean].subsectionId,
      };
    }
    var known = [
      "general",
      "appearance",
      "models",
      "agents",
      "plugins",
      "usage",
      "workspace",
      "connections",
      "safety",
      "advanced",
    ];
    return {
      sectionId: known.indexOf(clean) >= 0 ? clean : "general",
      subsectionId: null,
    };
  }

  // Rail icons (static, self-authored SVG — the one trusted-html escape hatch).
  var svg = function (paths) {
    return (
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      paths +
      "</svg>"
    );
  };
  var ICONS = {
    back: svg('<path d="m15 18-6-6 6-6"/><path d="M9 12h10"/>'),
    general: svg('<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M3.5 9h17M9 9v11.5"/>'),
    models: svg('<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M8.2 6H14a4 4 0 0 1 0 8H9.8"/>'),
    agents: svg('<circle cx="8" cy="9" r="3"/><circle cx="17" cy="8" r="2.5"/><path d="M3.5 19c.5-3.2 2.3-5 4.5-5s4 1.8 4.5 5M13 15c1-.9 2.2-1.3 3.5-1.3 2.1 0 3.5 1.7 4 4.3"/>'),
    plugins: svg('<path d="M8 3h3v4h2V3h3v4h1.5A2.5 2.5 0 0 1 20 9.5V12h-4v2h4v.5a2.5 2.5 0 0 1-2.5 2.5H14v4h-4v-4H6.5A2.5 2.5 0 0 1 4 14.5V11h4V9H4A2 2 0 0 1 6 7h2V3Z"/>'),
    workspace: svg('<path d="M3.5 6.5A2.5 2.5 0 0 1 6 4h4l2 2h6A2.5 2.5 0 0 1 20.5 8.5v8A2.5 2.5 0 0 1 18 19H6a2.5 2.5 0 0 1-2.5-2.5v-10Z"/>'),
    connections: svg('<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
    usage: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>'),
    safety: svg('<path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>'),
    appearance: svg('<path d="M4 8h9M4 16h3M17 16h3"/><circle cx="16" cy="8" r="2.4"/><circle cx="10" cy="16" r="2.4"/>'),
    advanced: svg('<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>'),
  };

  // The registry: rail label + group + keywords + the section's content
  // builder. Groups become uppercase labels in the rail (Settings redesign).
  var sections = [
    {
      id: "general",
      group: "OPai",
      title: "General",
      summary: "Defaults for new tasks",
      keywords: "general defaults task mode focus output format overview",
      searchItems: SEARCH_ITEMS.general,
      render: generalHtml,
    },
    {
      id: "appearance",
      group: "OPai",
      title: "Appearance",
      summary: "Layout, density, and motion",
      keywords: "theme density response compact balanced detailed motion animation reduced dark",
      searchItems: SEARCH_ITEMS.appearance,
      render: appearanceHtml,
    },
    {
      id: "models",
      group: "AI",
      title: "Models & Routing",
      summary: "Models, routing, and fallback order",
      keywords: "model default routing focus profile provider priority fallback local first",
      searchItems: SEARCH_ITEMS.models,
      render: modelsRoutingHtml,
    },
    {
      id: "agents",
      group: "AI",
      title: "Agents",
      summary: "Agent work, workflows, and evidence",
      keywords: "agents background work workflows proof outcomes",
      searchItems: SEARCH_ITEMS.agents,
      render: agentsHtml,
    },
    {
      id: "plugins",
      group: "AI",
      title: "Plugins",
      summary: "Reusable tools and integrations",
      keywords: "plugins extensions prompts integrations connected capabilities",
      searchItems: SEARCH_ITEMS.plugins,
      render: pluginsHtml,
    },
    {
      id: "usage",
      group: "AI",
      title: "Usage & Budgets",
      summary: "Consumption, balances, and limits",
      keywords: "usage balance credit cost firewall budget spend cap quota rate limit remaining requests tokens daily monthly",
      searchItems: SEARCH_ITEMS.usage,
      render: usageBudgetsHtml,
    },
    {
      id: "workspace",
      group: "Development",
      title: "Workspace",
      summary: "Projects, terminal, Git, and environment",
      keywords: "workspace project terminal shell git github rules environment directory folder",
      searchItems: SEARCH_ITEMS.workspace,
      render: workspaceHtml,
    },
    {
      id: "connections",
      group: "Development",
      title: "Connections",
      summary: "Provider accounts, keys, and health",
      keywords: "provider connection account api key credential sign in github doctor codex",
      searchItems: SEARCH_ITEMS.connections,
      render: connectionsHtml,
    },
    {
      id: "safety",
      group: "Trust",
      title: "Safety & Privacy",
      summary: "Approvals, cloud access, and local data",
      keywords: "permission permissions safety privacy data telemetry local cloud firewall panic approval",
      searchItems: SEARCH_ITEMS.safety,
      render: safetyPrivacyHtml,
    },
    {
      id: "advanced",
      group: "System",
      title: "Advanced",
      summary: "Tools, updates, and build details",
      keywords: "advanced tools insights update about version release asset build diagnostics",
      searchItems: SEARCH_ITEMS.advanced,
      render: advancedHtml,
    },
  ];

  function render(page, ctx) {
    var d = ctx.d || {};
    var esc = ctx.esc;
    var present = sections.filter(function (section) {
      var html = section.render(d, ctx);
      section._html = html;
      return !!html;
    });
    var sidebarHeader =
      '<div class="settings-sidebar-head"><div class="settings-sidebar-brand"><button class="settings-back" id="settingsBack" type="button" aria-label="Back to Chat" title="Back to Chat"><span aria-hidden="true">' +
      ICONS.back +
      '</span></button><div class="settings-sidebar-brand-copy"><div class="settings-sidebar-title">OPai</div>' +
      '<div class="settings-sidebar-tagline">Code faster together.</div><span class="settings-a11y-label">Settings</span></div>' +
      '</div><div class="settings-toolbar"><div class="settings-search-field">' +
      '<input id="settingsSearch" type="search" placeholder="Search settings…" aria-label="Search settings" autocomplete="off" spellcheck="false" aria-controls="settingsSearchResults">' +
      '<button id="settingsSearchClear" class="settings-search-clear" type="button" aria-label="Clear settings search" title="Clear search" hidden>×</button>' +
      "</div></div></div>";
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
    var groupOrder = ["OPai", "AI", "Development", "Trust", "System"];
    var rail =
      '<nav class="settings-rail" aria-label="Settings pages">' +
      groupOrder
        .map(function (group) {
          var grouped = present.filter(function (section) {
            return section.group === group;
          });
          if (!grouped.length) return "";
          return (
            '<section class="settings-rail-section" aria-label="' +
            esc(group) +
            '"><div class="settings-rail-group">' +
            esc(group) +
            "</div>" +
            grouped
              .map(function (section) {
                return (
                  '<button class="settings-rail-item" type="button" data-rail-target="' +
                  esc(section.id) +
                  '" aria-controls="set-sec-' +
                  esc(section.id) +
                  '"><span class="settings-rail-icon" aria-hidden="true">' +
                  (ICONS[section.id] || "") +
                  '</span><span class="settings-rail-copy"><span class="settings-rail-label">' +
                  esc(section.title) +
                  '</span><span class="settings-rail-summary">' +
                  esc(section.summary || "") +
                  "</span></span></button>"
                );
              })
              .join("") +
            "</section>"
          );
        })
        .join("") +
      "</nav>";

    page.innerHTML =
      '<div class="settings-layout">' +
      '<aside class="settings-sidebar">' +
      sidebarHeader +
      rail +
      '<div class="settings-search-results" id="settingsSearchResults" aria-label="Settings search results" hidden>' +
      '<div class="settings-result-list" id="settingsResultList"></div>' +
      '<div class="settings-noresults" id="settingsNoResults" role="status" aria-live="polite" hidden>' +
      "<strong>No matching settings</strong><span>Try a label, category, or older term.</span></div></div>" +
      "</aside>" +
      '<div class="settings-content" id="settingsContent">' +
      '<div class="settings-mobile-bar"><button id="settingsMobileBack" type="button" aria-label="Back to Settings">‹ <span>Settings</span></button>' +
      '<span id="settingsMobileTitle"></span></div>' +
      panesHtml +
      "</div></div>";

    var layout = page.querySelector(".settings-layout");
    var sidebar = page.querySelector(".settings-sidebar");
    var content = page.querySelector("#settingsContent");
    var panes = Array.prototype.slice.call(content.querySelectorAll(".settings-pane"));
    var railItems = Array.prototype.slice.call(page.querySelectorAll(".settings-rail-item"));
    var search = page.querySelector("#settingsSearch");
    var clearButton = page.querySelector("#settingsSearchClear");
    var searchResults = page.querySelector("#settingsSearchResults");
    var resultList = page.querySelector("#settingsResultList");
    var noResults = page.querySelector("#settingsNoResults");
    var mobileTitle = page.querySelector("#settingsMobileTitle");
    var mobileBack = page.querySelector("#settingsMobileBack");
    var settingsBack = page.querySelector("#settingsBack");
    var activeId = "general";
    wire(content, ctx);

    function focusDestination(sectionId, subsectionId, selector, focusHeading) {
      var pane = content.querySelector('[data-pane="' + sectionId + '"]');
      if (!pane) return;
      var target = selector ? pane.querySelector(selector) : null;
      if (!target && subsectionId) {
        target = pane.querySelector('[data-settings-subsection="' + subsectionId + '"]');
      }
      var heading = pane.querySelector(".pane-title");
      global.requestAnimationFrame(function () {
        if (target && typeof target.scrollIntoView === "function") {
          target.scrollIntoView({ block: "start", inline: "nearest" });
        }
        var focusTarget =
          target && /^(BUTTON|INPUT|SELECT|A)$/.test(target.tagName) ? target : heading;
        if (focusHeading !== false && focusTarget && typeof focusTarget.focus === "function") {
          focusTarget.focus({ preventScroll: true });
        }
      });
    }

    function activate(id, updateHash, options) {
      var opts = options || {};
      var resolved = resolveSettingsTarget(id);
      activeId = resolved.sectionId;
      var subsectionId =
        opts.subsectionId !== undefined ? opts.subsectionId : resolved.subsectionId;
      panes.forEach(function (pane) {
        pane.classList.toggle("active", pane.dataset.pane === activeId);
      });
      railItems.forEach(function (link) {
        var on = link.dataset.railTarget === activeId;
        link.classList.toggle("active", on);
        if (on) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      var activePane = content.querySelector('[data-pane="' + activeId + '"]');
      if (mobileTitle && activePane) mobileTitle.textContent = activePane.dataset.paneTitle || "";
      layout.classList.toggle("mobile-detail", opts.showDetail !== false);
      if (updateHash !== false) {
        try {
          global.history &&
            global.history.replaceState &&
            global.history.replaceState(null, "", "#settings/" + activeId);
        } catch (_e) {
          /* hash routing is best-effort */
        }
      }
      var scroller = page.closest(".scroll");
      if (scroller) scroller.scrollTop = 0;
      focusDestination(activeId, subsectionId, opts.selector, opts.focus);
    }

    function resultButtons() {
      return Array.prototype.slice.call(
        resultList.querySelectorAll("[data-settings-search-result]")
      );
    }

    function focusResult(index) {
      var buttons = resultButtons();
      if (!buttons.length) return;
      var next = (index + buttons.length) % buttons.length;
      buttons[next].focus();
    }

    function clearSearch(restoreFocus) {
      if (!search) return;
      search.value = "";
      sidebar.classList.remove("searching");
      searchResults.hidden = true;
      clearButton.hidden = true;
      noResults.hidden = true;
      while (resultList.firstChild) resultList.removeChild(resultList.firstChild);
      if (restoreFocus) search.focus();
    }

    function applySearch(value) {
      var query = String(value || "").trim().toLowerCase();
      while (resultList.firstChild) resultList.removeChild(resultList.firstChild);
      var searching = query !== "";
      sidebar.classList.toggle("searching", searching);
      searchResults.hidden = !searching;
      clearButton.hidden = !searching;
      if (!searching) {
        noResults.hidden = true;
        return;
      }
      var matches = [];
      present.forEach(function (section) {
        (section.searchItems || []).forEach(function (item) {
          var haystack = [
            item.label,
            item.description,
            item.group,
            item.keywords,
            section.title,
            section.keywords,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          if (haystack.indexOf(query) >= 0) matches.push({ section: section, item: item });
        });
      });
      matches.slice(0, 16).forEach(function (match, index) {
        var button = el(
          "button",
          {
            class: "settings-result",
            type: "button",
            dataset: { settingsSearchResult: "", resultIndex: String(index) },
          },
          [
            el("span", { class: "settings-result-label", text: match.item.label }),
            el("span", {
              class: "settings-result-path",
              text: match.section.title + " › " + match.item.group,
            }),
          ]
        );
        button.addEventListener("click", function () {
          clearSearch(false);
          activate(match.section.id, true, {
            showDetail: true,
            subsectionId: match.item.subsectionId,
            selector: match.item.selector,
            focus: true,
          });
        });
        button.addEventListener("keydown", function (event) {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            focusResult(index + (event.key === "ArrowDown" ? 1 : -1));
          } else if (event.key === "Escape") {
            event.preventDefault();
            clearSearch(true);
          }
        });
        resultList.appendChild(button);
      });
      noResults.hidden = matches.length > 0;
    }

    railItems.forEach(function (link) {
      link.addEventListener("click", function () {
        clearSearch(false);
        activate(link.dataset.railTarget, true, { showDetail: true, focus: true });
      });
    });
    page.querySelectorAll("[data-settings-target]").forEach(function (link) {
      link.addEventListener("click", function () {
        activate(link.dataset.settingsTarget, true, { showDetail: true, focus: true });
      });
    });
    page.querySelectorAll("[data-workspace-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        var target = content.querySelector(
          '[data-pane="workspace"] [data-settings-subsection="' +
            tab.dataset.workspaceTab +
            '"]'
        );
        page.querySelectorAll("[data-workspace-tab]").forEach(function (item) {
          var selected = item === tab;
          item.classList.toggle("active", selected);
          item.setAttribute("aria-pressed", selected ? "true" : "false");
        });
        if (target && typeof target.scrollIntoView === "function") {
          target.scrollIntoView({ block: "start", inline: "nearest" });
        }
      });
    });
    var openWorkspace = page.querySelector("#settingsOpenWorkspace");
    if (openWorkspace && ctx.bridge && ctx.bridge.openWorkspace) {
      openWorkspace.addEventListener("click", function () {
        ctx.bridge.openWorkspace();
      });
    }
    if (search) {
      search.addEventListener("input", function () {
        applySearch(search.value);
      });
      search.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          clearSearch(true);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          focusResult(0);
        }
      });
    }
    if (clearButton) {
      clearButton.addEventListener("click", function () {
        clearSearch(true);
      });
    }
    if (settingsBack) {
      settingsBack.addEventListener("click", function () {
        if (typeof ctx.switchView === "function") ctx.switchView("chat");
      });
    }
    if (mobileBack) {
      mobileBack.addEventListener("click", function () {
        layout.classList.remove("mobile-detail");
        try {
          global.history &&
            global.history.replaceState &&
            global.history.replaceState(null, "", "#settings");
        } catch (_e) {
          /* hash routing is best-effort */
        }
        var current = page.querySelector(
          '.settings-rail-item[data-rail-target="' + activeId + '"]'
        );
        if (current) current.focus();
      });
    }
    content.querySelectorAll("[data-go-view]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (typeof ctx.switchView === "function") ctx.switchView(button.dataset.goView);
      });
    });

    var hash = (global.location && global.location.hash) || "";
    var match = /^#settings\/([\w-]+)$/.exec(hash);
    var initial = resolveSettingsTarget(match ? match[1] : "general");
    activate(initial.sectionId, false, {
      showDetail: !!match,
      subsectionId: initial.subsectionId,
      focus: false,
    });
    if (ctx.startDoctorRefresh) ctx.startDoctorRefresh();
  }

  // ---- wiring (exact handlers moved from app.js), scoped to `page` -------- //
  function wire(page, ctx) {
    var bridge = ctx.bridge;
    var d = ctx.d || {};
    var esc = ctx.esc;
    var toast = ctx.toast;
    var state = ctx.state;
    var refresh = ctx.refresh;
    var q = function (sel) {
      return page.querySelector(sel);
    };
    var setRadioGroup = function (segment, selected) {
      segment.querySelectorAll('[role="radio"]').forEach(function (option) {
        var active = option === selected;
        option.classList.toggle("active", active);
        option.setAttribute("aria-checked", active ? "true" : "false");
        option.tabIndex = active ? 0 : -1;
      });
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
              if (ctx.applyModelCatalog) ctx.applyModelCatalog(refreshed);
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
          ctx.refreshConnectedModels();
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
          ctx.refreshConnectedModels();
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
          // Through the same map as the initial render. Writing the raw value
          // here is what let a tested-and-rejected provider display a status
          // in a vocabulary nothing else used.
          if (status) status.textContent = authStatusLabel(result.authStatus);
          ctx.updateDoctorCard(id, result);
          ctx.refreshConnectedModels();
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
                ctx.refreshConnectedModels();
                var status = q('[data-account-status="' + id + '"]');
                var dot = q('[data-account-row="' + id + '"] .prov-dot');
                if (status)
                  status.textContent = authStatusLabel("not_configured");
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
    // Credits & Balance: save a manually-entered balance, refresh live ones.
    page.querySelectorAll("[data-save-balance]").forEach(function (button) {
      button.onclick = function () {
        var card = button.closest(".balance-card");
        var input = card.querySelector('input[type="number"]');
        var currency = card.querySelector("select");
        var error = card.querySelector("[data-balance-error]");
        var value = input.value.trim();
        if (value === "" || isNaN(+value) || +value < 0) {
          if (error) {
            error.textContent = "Enter a number of at least 0.";
            error.hidden = false;
          }
          return;
        }
        if (error) error.hidden = true;
        if (!bridge.setProviderBalance) {
          toast("Balance entry is unavailable in this build.");
          return;
        }
        bridge.setProviderBalance(
          button.dataset.saveBalance,
          value,
          currency ? currency.value : "USD",
          function (json2) {
            var result = {};
            try {
              result = JSON.parse(json2);
            } catch (_e) {
              /* keep {} */
            }
            toast(result.ok ? "Balance saved" : result.error || "Could not save balance");
            if (result.ok) refresh();
          }
        );
      };
    });
    var balanceRefresh = q("#balanceRefresh");
    if (balanceRefresh)
      balanceRefresh.onclick = function () {
        if (!bridge.refreshBalances) {
          toast("Live balance refresh is unavailable in this build.");
          return;
        }
        balanceRefresh.disabled = true;
        balanceRefresh.textContent = "Refreshing…";
        bridge.refreshBalances(function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          balanceRefresh.disabled = false;
          balanceRefresh.textContent = "Refresh live balances";
          if (result.ok) refresh();
          else toast(result.error || "Could not refresh balances");
        });
      };
    // The payload itself is cache-only (no network on render); providers with
    // a live balance API get one background refresh per 15-minute window so
    // the page shows current numbers without the user pressing anything.
    // The timestamp guard makes the follow-up refresh() re-render loop-proof.
    if (bridge.refreshBalances && page.querySelector(".balance-card")) {
      var probedAt = (state && state._balanceProbeAt) || 0;
      if (Date.now() - probedAt > 15 * 60 * 1000) {
        if (state) state._balanceProbeAt = Date.now();
        bridge.refreshBalances(function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          if (result.ok) refresh();
        });
      }
    }
    // ---- Model Usage: refresh + live reset countdown ---------------------- //
    // A single 1-second ticker updates every reset countdown on screen (pure
    // UI math, no network). Re-running wire() clears the prior ticker so it
    // never doubles up; when the page has no countdowns it does nothing.
    if (state && state._usageTicker) {
      clearInterval(state._usageTicker);
      state._usageTicker = null;
    }
    function tickUsageCountdowns() {
      var nodes = document.querySelectorAll("[data-usage-resets-at]");
      if (!nodes.length) return;
      var nowSec = Date.now() / 1000;
      nodes.forEach(function (node) {
        var resetsAt = parseFloat(node.getAttribute("data-usage-resets-at"));
        var out = node.querySelector("[data-usage-countdown]");
        if (!out || !isFinite(resetsAt)) return;
        var remaining = resetsAt - nowSec;
        if (remaining <= 0) {
          out.textContent = "moments";
          var card = node.closest(".usage2-card");
          var pill = card && card.querySelector("[data-usage-pill]");
          if (pill && !pill.classList.contains("warn")) {
            pill.className = "usage2-pill warn";
            pill.textContent = "Stale";
          }
        } else {
          out.textContent = fmtDuration(remaining);
        }
      });
    }
    if (page.querySelector("[data-usage-resets-at]") && state) {
      state._usageTicker = setInterval(tickUsageCountdowns, 1000);
    }
    var usageRefresh = q("#usageRefresh");
    if (usageRefresh)
      usageRefresh.onclick = function () {
        if (!bridge.refreshUsage) {
          toast("Live usage refresh is unavailable in this build.");
          return;
        }
        usageRefresh.disabled = true;
        usageRefresh.textContent = "Refreshing…";
        bridge.refreshUsage(function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          usageRefresh.disabled = false;
          usageRefresh.textContent = "Refresh live usage";
          if (result.ok) refresh();
          else toast(result.error || "Could not refresh usage");
        });
      };
    // Cache-only payload on render; providers with a live usage source get one
    // background probe per TTL window so the page shows current numbers without
    // a click. Timestamp-guarded so the follow-up refresh() cannot loop.
    if (bridge.refreshUsage && page.querySelector(".usage2-card[data-usage-supports-refresh='1']")) {
      var usageProbedAt = (state && state._usageProbeAt) || 0;
      if (Date.now() - usageProbedAt > 5 * 60 * 1000) {
        if (state) state._usageProbeAt = Date.now();
        bridge.refreshUsage(function (json2) {
          var result = {};
          try {
            result = JSON.parse(json2);
          } catch (_e) {
            /* keep {} */
          }
          if (result.ok) refresh();
        });
      }
    }
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
    // Manual checks bypass freshness caching. The async result reaches every
    // surface through the shared update event emitted by app.js.
    var updateCard = q("#settingsUpdateCard");
    var manualUpdateCheckPending = false;
    function wireUpdateButtons() {
      var checkBtn = q("#settingsCheckUpdate");
      if (checkBtn)
        checkBtn.onclick = function () {
          manualUpdateCheckPending = true;
          checkBtn.disabled = true;
          checkBtn.textContent = "Checking…";
          bridge.checkForUpdates(true);
        };
      var applyBtn = q("#settingsApplyUpdate");
      if (applyBtn)
        applyBtn.onclick = function () {
          applyBtn.disabled = true;
          applyBtn.textContent = "Updating…";
          bridge.updateAction("developer_apply");
        };
      var restartBtn = q("#settingsRestartUpdate");
      if (restartBtn)
        restartBtn.onclick = function () {
          restartBtn.disabled = true;
          restartBtn.textContent = "Restarting…";
          bridge.updateAction("restart_now");
        };
    }
    wireUpdateButtons();
    if (global.__opaiSettingsUpdateListener) {
      global.removeEventListener("opai-update-state", global.__opaiSettingsUpdateListener);
    }
    global.__opaiSettingsUpdateListener = function (event) {
      var result = event.detail || {};
      if (updateCard) updateCard.innerHTML = updateStatusHtml(esc, result);
      wireUpdateButtons();
      if (result.developer_apply) return; // app.js already toasted the precise outcome
      var operation = result.operation || {};
      if (operation.state === "checking") return;
      var requestedCheck = manualUpdateCheckPending;
      manualUpdateCheckPending = false;
      if (!requestedCheck) return;
      if (operation.state === "available") toast("Update available");
      else if (operation.state === "up_to_date") toast("You're on the latest version");
      else if (operation.safe_diagnostic) toast(operation.safe_diagnostic);
    };
    global.addEventListener("opai-update-state", global.__opaiSettingsUpdateListener);
    // Replay the first-run tour (#250) — reuses the real onboarding overlay.
    var replayBtn = q("#settingsReplayTour");
    if (replayBtn && ctx.replayTour)
      replayBtn.onclick = function () {
        ctx.replayTour();
      };
    // Clear previous chats and recents (#239): a destructive action, gated by the
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
            title: "Clear previous chats & recents?",
            body: "This permanently removes this workspace's previous saved chats and recent-task list. It cannot be undone. Your current chat will be kept.",
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
              if (result.ok && ctx.applyClearedHistory) ctx.applyClearedHistory(result);
              toast(result.ok ? "Previous chats cleared." : result.error || "Could not clear previous chats.");
            });
          });
      };
    // Bypass Permissions: a switch layered over the current mode, persisted
    // like any other preference. Re-render so the warning line and the mode
    // rows below reflect the new authority immediately rather than after a
    // navigation -- a permissions panel that lags is a panel that lies.
    var bypassToggle = page.querySelector("#setBypassPermissions");
    if (bypassToggle) {
      bypassToggle.onchange = function () {
        var on = bypassToggle.checked === true;
        bridge.savePref("bypass_permissions", on ? "true" : "false");
        if (ctx.applyDefaults) ctx.applyDefaults("bypass_permissions", on);
        toast(
          on
            ? "Bypass permissions on — nothing will ask for approval."
            : "Bypass permissions off — your mode's rules apply again."
        );
      };
    }
    // Editable defaults (#238): persist and reflect in the composer instantly.
    page.querySelectorAll("[data-default-pref]").forEach(function (select) {
      select.onchange = function () {
        bridge.savePref(select.dataset.defaultPref, select.value);
        if (ctx.applyDefaults) ctx.applyDefaults(select.dataset.defaultPref, select.value);
      };
    });
    // The override document is deliberately replaced as one validated payload.
    // Merging a handful of DOM changes into an old browser snapshot would make
    // global settings race across workspaces.
    function pickerPayload() {
      var report = d.modelOverrides || {};
      var payload = { providers: {} };
      Object.keys(report.providers || {}).forEach(function (provider) {
        payload.providers[provider] = {
          models: ((report.providers[provider] || {}).models || []).map(function (entry) {
            return Object.assign({}, entry);
          }),
        };
      });
      Object.keys(report.hidden || {}).forEach(function (provider) {
        if (!payload.providers[provider]) payload.providers[provider] = {};
        payload.providers[provider].hide = (report.hidden[provider] || []).slice();
      });
      return payload;
    }
    function savePicker(payload) {
      var error = q("[data-model-override-error]");
      if (!bridge.saveModelOverrides) {
        if (error) { error.textContent = "Model picker editing is unavailable in this build."; error.hidden = false; }
        return;
      }
      bridge.saveModelOverrides(JSON.stringify(payload), function (json2) {
        var result = {};
        try { result = JSON.parse(json2); } catch (_e) { /* keep {} */ }
        if (!result.ok) {
          if (error) { error.textContent = result.error || "Could not save the model picker."; error.hidden = false; }
          return;
        }
        if (error) error.hidden = true;
        if (ctx.applyModelCatalog && result.catalog) ctx.applyModelCatalog(result.catalog);
        toast("Global model picker saved");
      });
    }
    page.querySelectorAll("[data-model-visibility]").forEach(function (toggle) {
      toggle.onchange = function () {
        var payload = pickerPayload();
        var provider = toggle.dataset.modelProvider;
        var modelId = toggle.dataset.modelOverrideId;
        if (!payload.providers[provider]) payload.providers[provider] = {};
        var hidden = payload.providers[provider].hide || [];
        var index = hidden.map(function (id) { return String(id).toLowerCase(); }).indexOf(String(modelId).toLowerCase());
        if (toggle.checked && index >= 0) hidden.splice(index, 1);
        if (!toggle.checked && index < 0) hidden.push(modelId);
        if (hidden.length) payload.providers[provider].hide = hidden;
        else delete payload.providers[provider].hide;
        savePicker(payload);
      };
    });
    var addCustom = q("[data-add-custom-model]");
    if (addCustom) addCustom.onclick = function () {
      var provider = q("[data-custom-provider]").value;
      var id = q("[data-custom-model]").value.trim();
      var label = q("[data-custom-label]").value.trim();
      var capability = q("[data-custom-capability]").value;
      if (!provider || !id || !label) {
        var error = q("[data-model-override-error]");
        if (error) { error.textContent = "Provider, model ID, and label are required."; error.hidden = false; }
        return;
      }
      var payload = pickerPayload();
      if (!payload.providers[provider]) payload.providers[provider] = {};
      var models = payload.providers[provider].models || [];
      models.push({ id: id, display: label, capability: capability });
      payload.providers[provider].models = models;
      savePicker(payload);
    };
    page.querySelectorAll("[data-remove-custom-id]").forEach(function (button) {
      button.onclick = function () {
        var payload = pickerPayload();
        var provider = button.dataset.removeCustomProvider;
        var block = payload.providers[provider] || {};
        block.models = (block.models || []).filter(function (entry) {
          return entry.id !== button.dataset.removeCustomId;
        });
        payload.providers[provider] = block;
        savePicker(payload);
      };
    });
    var resetPicker = q("[data-reset-model-overrides]");
    if (resetPicker) resetPicker.onclick = function () { savePicker({ providers: {} }); };
    // Appearance (#241): persist via savePref and apply to the root instantly.
    page.querySelectorAll("[data-appearance-key]").forEach(function (segment) {
      var key = segment.dataset.appearanceKey;
      segment.querySelectorAll("button").forEach(function (button) {
        button.onclick = function () {
          setRadioGroup(segment, button);
          bridge.savePref(key, button.dataset.value);
          // Remember the choice in the payload this page re-renders from, not
          // only on disk, so a re-render cannot show the previous value.
          if (ctx.d && ctx.d.prefs) ctx.d.prefs[key] = button.dataset.value;
          if (!ctx.applyAppearance) return;
          var current = {};
          page.querySelectorAll("[data-appearance-key]").forEach(function (other) {
            var active = other.querySelector("button.active");
            var camelKeys = {
              reduced_motion: "reducedMotion",
              activity_copy: "activityCopy",
              response_density: "responseDensity",
            };
            var name = camelKeys[other.dataset.appearanceKey] || other.dataset.appearanceKey;
            current[name] = active ? active.dataset.value : "";
          });
          ctx.applyAppearance(current);
        };
      });
    });

    // App-wide updater policy. Workspace switches cannot change this consent.
    page.querySelectorAll("[data-update-policy]").forEach(function (segment) {
      var key = segment.dataset.updatePolicy;
      segment.querySelectorAll("button").forEach(function (button) {
        button.onclick = function () {
          if (button.disabled) return;
          var on = button.dataset.value === "on";
          bridge.setUpdatePolicy(key, on ? "true" : "false", function (json2) {
            var result = {};
            try { result = JSON.parse(json2 || "{}"); } catch (_e) { /* keep {} */ }
            if (result.ok && result.policy) {
              page.querySelectorAll("[data-update-policy]").forEach(function (policySegment) {
                var policyKey = policySegment.dataset.updatePolicy;
                var enabled = !!result.policy[policyKey];
                policySegment.querySelectorAll("button").forEach(function (other) {
                  var active = (other.dataset.value === "on") === enabled;
                  other.classList.toggle("active", active);
                  other.setAttribute("aria-checked", active ? "true" : "false");
                  other.tabIndex = active ? 0 : -1;
                  if (policyKey === "automatic_install_on_quit" && other.dataset.value === "on") {
                    other.disabled = !result.policy.automatic_downloads;
                    other.setAttribute("aria-disabled", other.disabled ? "true" : "false");
                  }
                });
              });
            }
            if (result.ok && global.__opai && global.__opai.renderUpdateBanner) {
              global.__opai.renderUpdateBanner(result);
            }
            if (ctx.toast) ctx.toast(result.ok ? "Update preference saved" : "Could not save update preference");
          });
        };
      });
    });

    // Composer style (Composer Redesign): persist per workspace and switch the
    // live composer direction immediately.
    page.querySelectorAll("[data-composer-style-key]").forEach(function (segment) {
      var key = segment.dataset.composerStyleKey;
      segment.querySelectorAll("button").forEach(function (button) {
        button.onclick = function () {
          setRadioGroup(segment, button);
          bridge.savePref(key, button.dataset.value);
          if (ctx.d && ctx.d.prefs) {
            ctx.d.prefs.composer_style = button.dataset.value;
            ctx.d.prefs.composerStyle = button.dataset.value;
          }
          if (global.OPaiComposer) global.OPaiComposer.setStyle(button.dataset.value);
        };
      });
    });

    page.querySelectorAll('.seg[role="radiogroup"], .theme-choices[role="radiogroup"]').forEach(function (segment) {
      segment.addEventListener("keydown", function (event) {
        if (
          event.key !== "ArrowLeft" &&
          event.key !== "ArrowRight" &&
          event.key !== "ArrowUp" &&
          event.key !== "ArrowDown" &&
          event.key !== "Home" &&
          event.key !== "End"
        ) {
          return;
        }
        var options = Array.prototype.slice
          .call(segment.querySelectorAll('[role="radio"]'))
          .filter(function (option) {
            return !option.disabled;
          });
        if (!options.length) return;
        var current = options.indexOf(document.activeElement);
        if (current < 0) {
          current = options.findIndex(function (option) {
            return option.getAttribute("aria-checked") === "true";
          });
        }
        if (event.key === "Home") current = 0;
        else if (event.key === "End") current = options.length - 1;
        else {
          var delta = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
          current = (current + delta + options.length) % options.length;
        }
        event.preventDefault();
        options[current].focus();
        options[current].click();
      });
    });
  }

  var api = {
    el: el,
    sections: sections,
    render: render,
    doctorSummary: doctorSummary,
    resolveSettingsTarget: resolveSettingsTarget,
    MODE_LABELS: MODE_LABELS,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.OPaiSettings = api;
})(typeof window !== "undefined" ? window : this);
