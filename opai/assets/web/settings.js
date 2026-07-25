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
    var h = '<div class="pane-hero"><div class="pane-title">' + esc(title) + "</div>";
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
  }

  // Overview (Settings redesign): the landing page answers "am I safe,
  // connected, and able to keep working?" from the same payload the other
  // pages render — nothing here is invented or cached separately.
  function overviewHtml(d, ctx) {
    var esc = ctx.esc;
    var firewall = d.firewall || {};
    var prefs = d.prefs || {};
    var doctorItems = doctorItemsOf(d);
    var summary = doctorSummary(
      doctorItems.map(function (item) {
        return item.health;
      })
    );
    var connected = doctorItems.filter(function (item) {
      return item.health === "verified" || item.health === "detected";
    }).length;
    var root = (ctx.state.boot.workspace && ctx.state.boot.workspace.root) || "";

    var h = heroHtml(
      esc,
      "Settings",
      "Control how OPai routes work, spends, and keeps you safe — without getting in your way.",
      ["app", "project", "local"]
    );
    if (root) h += '<div class="pane-meta mono">' + esc(root) + "</div>";

    h += '<div class="set-head">OPai status</div>';
    h += '<div class="stat-grid">';
    h += statTile(esc, {
      label: "Protection",
      value: firewall.panic
        ? "Panic — local only"
        : firewall.cloud_gate
          ? "Cloud gate: confirm"
          : "Cloud gate: open",
      sub: firewall.panic ? "Cloud calls refused" : "Firewall active",
      tone: firewall.panic ? "red" : "green",
    });
    h += statTile(esc, {
      label: "Routing profile",
      value: firewall.profile || "—",
      sub: "Local-first",
    });
    h += statTile(esc, {
      label: "Providers",
      value: connected + " connected",
      sub: !doctorItems.length
        ? "None detected yet"
        : summary.attention
          ? summary.attention + " need" + (summary.attention === 1 ? "s" : "") + " attention"
          : "All look good",
      tone: !doctorItems.length ? "" : summary.attention ? "amber" : "green",
    });
    h += statTile(esc, {
      label: "Default run mode",
      value: modePresentationLabel(prefs.default_mode, MODE_LABELS[prefs.default_mode]),
      sub: "For new tasks",
    });
    h += "</div>";

    // Honest attention items only: each one is derived from a real signal in
    // the payload and links to the page where it can be acted on.
    var attention = [];
    if (summary.attention) {
      attention.push({
        tone: "warn",
        title:
          summary.attention +
          " connection" +
          (summary.attention === 1 ? "" : "s") +
          " need" +
          (summary.attention === 1 ? "s" : "") +
          " attention",
        body: "A provider is unavailable, not signed in, or not configured. Routing works around it where it can.",
        go: "providers",
        action: "Review connections",
      });
    }
    if (firewall.panic) {
      attention.push({
        tone: "warn",
        title: "Panic mode is on — every cloud call is refused",
        body: "Routing is local-only until you disable panic mode.",
        go: "firewall",
        action: "Review",
      });
    }
    if (d.codexConfig && d.codexConfig.repairable) {
      attention.push({
        tone: "warn",
        title: "Codex configuration needs repair",
        body: d.codexConfig.message || "Invalid Codex configuration detected.",
        go: "providers",
        action: "Repair",
      });
    }
    (d.usage || []).forEach(function (usage) {
      if (usage.limit != null && +usage.percent >= 90) {
        attention.push({
          tone: "warn",
          title: "A model is near its usage limit",
          body: Math.round(+usage.percent) + "% of the soft limit for this window is used.",
          go: "firewall",
          action: "Review usage",
        });
      }
    });
    h += '<div class="set-head">Needs attention</div>';
    if (attention.length) {
      h += attention
        .map(function (item) {
          return (
            '<div class="attn-item ' +
            item.tone +
            '"><div class="attn-body"><div class="attn-title">' +
            esc(item.title) +
            '</div><div class="attn-text">' +
            esc(item.body) +
            "</div></div>" +
            '<button class="btn ghost" type="button" data-go-page="' +
            esc(item.go) +
            '">' +
            esc(item.action) +
            "</button></div>"
          );
        })
        .join("");
    } else {
      h +=
        '<div class="attn-item ok"><div class="attn-body"><div class="attn-title">Nothing needs your attention right now</div>' +
        '<div class="attn-text">Anything that does — a failing connection, a tripped safety switch, a usage limit — will appear here, never hidden.</div></div></div>';
    }

    h += '<div class="set-head">Quick controls</div>';
    var quick = [
      { go: "models", title: "Models & routing", sub: "Default model, run mode, local-first order" },
      { go: "firewall", title: "Budgets & usage", sub: "Caps, spend, per-model limits" },
      { go: "permissions", title: "Permissions & safety", sub: "What OPai may do on its own" },
      { go: "providers", title: "Connections", sub: "Accounts, API keys & health checks" },
    ];
    h +=
      '<div class="quick-grid">' +
      quick
        .map(function (tile) {
          return (
            '<button class="quick-tile" type="button" data-go-page="' +
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
    return h;
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
    var h = heroHtml(
      esc,
      "Providers & Connections",
      "Keep your model providers healthy. Connection Doctor tests each one safely — it never reads or shows a secret.",
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
      return { id: id, label: modePresentationLabel(id, MODE_LABELS[id]) };
    });
    var focusOptions = (boot.taskModes || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var formatOptions = (boot.outputFormats || []).map(function (m) {
      return { id: m.id, label: m.label };
    });
    var h = heroHtml(
      esc,
      "Models & Routing",
      "Defaults for new tasks and the order OPai tries routes. Changes reflect in the composer instantly.",
      ["project"]
    );
    h += '<div class="set-head">Defaults</div>';
    h += selectRow("Default model", "default_model", modelOptions, prefs.default_model || "auto");
    h += selectRow(
      "Default run mode",
      "default_mode",
      modeOptions,
      MODE_LABELS[prefs.default_mode] ? prefs.default_mode : "safe-auto",
      "Auto-apply can only be pinned from the composer, with an explicit acknowledgement."
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
    var h = heroHtml(
      esc,
      "Cost Firewall",
      "See what you've spent and where the limits are. Spend is estimated locally from the usage ledger; nothing is transmitted.",
      ["project", "local"]
    );
    h += '<div class="set-head">Cost firewall</div>';
    h += '<div class="stat-grid">';
    h += statTile(esc, {
      label: "Spent today",
      value: money(firewall.spent_today || 0),
      sub: "OPai tracked",
      mono: true,
    });
    h += statTile(esc, {
      label: "Spent this month",
      value: money(firewall.spent_month || 0),
      sub: "OPai tracked",
      mono: true,
    });
    h += statTile(esc, {
      label: "Profile",
      value: firewall.profile || "—",
      sub: firewall.panic ? "Panic — local only" : "Guarding spend",
      tone: firewall.panic ? "red" : "green",
    });
    h += "</div>";
    h += '<div class="set-head">Budgets</div>';
    h += capRow("Daily cap", caps.daily_usd_limit, remaining.today_usd);
    h += capRow("Monthly cap", caps.monthly_usd_limit, remaining.month_usd);
    h += capRow("Per-task cap", caps.per_task_hard_limit_usd, null);
    h += '<div class="set-note">Spend is estimated locally from the usage ledger; nothing is transmitted.</div>';
    h += '<div class="set-head">Model usage limits</div>';
    h += usageCardsHtml(d, ctx);
    h += '<div class="set-head">Safety switches</div>';
    h +=
      '<div class="panic-card' +
      (firewall.panic ? " on" : "") +
      '"><div class="panic-body"><div class="panic-title">' +
      (firewall.panic ? "Panic mode is ON — cloud calls paused" : "Panic mode is off") +
      "</div>" +
      '<div class="panic-desc">Panic mode refuses every cloud call and forces local-only routing until you disable it. Local models keep working.</div>' +
      '<div class="panic-facts"><span>Pauses: all paid cloud calls</span><span>Keeps: local models, saved work, history</span></div></div>' +
      '<button class="btn danger" id="setPanic">' +
      (firewall.panic ? "Disable panic" : "Enable panic") +
      "</button></div>";
    h += row(esc, "Cloud gate", firewall.cloud_gate ? "confirm" : "open");
    h += '<div class="cb">• Confirm asks before each paid cloud call; open sends without a per-call confirmation.</div>';
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
    var h = heroHtml(
      esc,
      "Model Usage",
      "How much of each provider's own usage window you've used — Claude's 5-hour session, daily free-tier limits, prepaid credit, and more. Official figures come straight from the provider; OPai never invents a number.",
      ["local"]
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
    var h = heroHtml(
      esc,
      "Permissions & Safety",
      "Choose how much OPai can do on its own. Every step up the ladder grants more authority — you can change it any time.",
      ["project"]
    );
    h +=
      '<div class="mode-hero"><div class="mode-hero-body"><div class="mode-hero-label">Current mode for this project</div>' +
      '<div class="mode-hero-value">' +
      esc(activeMode) +
      "</div></div>" +
      (active ? '<div class="mode-hero-summary">' + esc(active.summary) + "</div>" : "") +
      "</div>";
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
      var METER = { ask: 20, plan: 36, "safe-auto": 56, "approve-edits": 76, "full-auto": 100 };
      h += '<div class="set-head">Run modes</div>';
      h +=
        '<div class="set-note">Switch modes from the composer. Auto-apply acts without asking and must be pinned there with an acknowledgement.</div>';
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
    var h = heroHtml(esc, "Privacy & Data", "Local by default. No telemetry unless you enable it.", [
      "local",
    ]);
    h +=
      '<div class="callout-card accent"><div class="callout-title">Data stays on this device</div>' +
      '<div class="callout-body">Prompts, the ledger, and audit history are kept locally, per workspace.</div></div>';
    h += '<div class="set-head">Privacy &amp; data</div>';
    statements.forEach(function (t) {
      h += '<div class="cb">• ' + esc(t) + "</div>";
    });
    h += '<div class="set-head">Saved chat &amp; recents</div>';
    h +=
      '<div class="set-note">Saved chat is stored redacted on this machine, per workspace.</div>';
    h +=
      '<div class="danger-zone"><div class="danger-body"><strong>Clear saved chat &amp; recents</strong>' +
      '<div class="set-note">Immediate and cannot be undone.</div></div>' +
      '<button class="btn danger" id="settingsClearRecents">Clear saved chat</button></div>';
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
    var composerStyle =
      prefs.composerStyle === "single" || prefs.composerStyle === "command"
        ? prefs.composerStyle
        : "toolbar";
    // A distinct key (not data-appearance-key) so it routes to the composer
    // controller instead of the document-root appearance handler.
    var composerSeg = function (current, options) {
      return (
        '<div class="seg" role="group" data-composer-style-key="composer_style">' +
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
    var h = heroHtml(
      esc,
      "Appearance",
      "Tune the cockpit to your eyes. These are low-risk — they apply instantly and are saved for this workspace.",
      ["app", "instant"]
    );
    h += '<div class="set-head">Appearance</div>';
    h +=
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Composer style</span><span class="hint">How the prompt box is arranged. Toolbar keeps everything one click away; Single line is the smallest footprint; Command bar is keyboard-first with #file, /mode, and @model tokens.</span></div>' +
      composerSeg(composerStyle, [
        { id: "toolbar", label: "Toolbar" },
        { id: "single", label: "Single line" },
        { id: "command", label: "Command bar" },
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
      '<div class="appearance-row"><div class="appearance-label"><span class="k">Theme</span><span class="hint">Dark is the only complete theme; a light theme is not shipped yet.</span></div><span class="v">Dark (default)</span></div>';
    return h;
  }

  // Update status card (Settings redesign): honest states only — never a
  // fabricated "up to date" when the check itself failed (offline, no git
  // checkout). `updateHtml` is also used to build the shell-wide nudge, so
  // wording can never drift between the two.
  function updateStatusHtml(esc, update) {
    var u = update || {};
    if (!u.checked) {
      var reason = u.reason || "Update status is unknown right now.";
      return (
        '<div class="update-card unknown" data-update-status="unknown">' +
        '<div class="update-head"><span class="update-title">Update status unknown</span></div>' +
        '<div class="update-desc">' +
        esc(reason) +
        "</div>" +
        '<div class="actions"><button class="btn" id="settingsCheckUpdate">Check for updates</button></div>' +
        "</div>"
      );
    }
    if (u.up_to_date) {
      return (
        '<div class="update-card ok" data-update-status="up-to-date">' +
        '<div class="update-head"><span class="update-dot"></span><span class="update-title">You\'re on the latest version</span></div>' +
        '<div class="actions"><button class="btn ghost" id="settingsCheckUpdate">Check for updates</button></div>' +
        "</div>"
      );
    }
    return (
      '<div class="update-card available" data-update-status="available">' +
      '<div class="update-head"><span class="update-dot"></span><span class="update-title">Update available' +
      (u.latest_version ? ": " + esc(u.latest_version) : "") +
      "</span></div>" +
      '<div class="update-desc">' +
      esc(
        (u.commits_behind ? u.commits_behind + " change" + (u.commits_behind === 1 ? "" : "s") + " behind. " : "") +
          "OPai fetches, fast-forwards, and reinstalls — nothing is discarded, and it refuses if you have uncommitted local changes."
      ) +
      "</div>" +
      '<div class="actions"><button class="btn primary" id="settingsApplyUpdate">Update now</button><button class="btn ghost" id="settingsCheckUpdate">Check again</button></div>' +
      "</div>"
    );
  }

  function aboutHtml(d, ctx) {
    var esc = ctx.esc;
    if (!(d.about && d.about.version)) return "";
    var build = d.about.build || {};
    var fingerprint = String(build.assetFingerprint || "");
    var runtimeSource = String(build.runtimeSource || "").replace(/_/g, " ");
    return (
      heroHtml(esc, "About", "Version and release information for this build.", null) +
      '<div class="set-head">About</div>' +
      '<div class="stat-grid two">' +
      statTile(esc, { label: "Version", value: d.about.version, mono: true }) +
      statTile(esc, { label: "Release stage", value: d.about.release_stage || "—" }) +
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
      // Replay the first-run tour on demand (#250).
      '<div class="set-head">Tour</div>' +
      '<div class="set-note">New here, or want a refresher? Replay the three-step welcome tour.</div>' +
      '<div class="actions"><button class="btn" id="settingsReplayTour">Replay tour</button></div>'
    );
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
    overview: svg('<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M3.5 9h17M9 9v11.5"/>'),
    providers: svg('<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
    balance: svg('<circle cx="12" cy="12" r="9"/><path d="M8.5 10.5a2 2 0 0 1 2-2h1a2 2 0 1 1 0 4h-1a2 2 0 1 0 0 4h1a2 2 0 0 0 2-2M12 7v1.2M12 15.8V17"/>'),
    models: svg('<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M8.2 6H14a4 4 0 0 1 0 8H9.8"/>'),
    firewall: svg('<path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6l-7-3Z"/><path d="M12.5 8.2h-2a1.3 1.3 0 0 0 0 2.6h1.5a1.3 1.3 0 0 1 0 2.6h-2"/>'),
    usage: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>'),
    permissions: svg('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
    privacy: svg('<path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>'),
    appearance: svg('<path d="M4 8h9M4 16h3M17 16h3"/><circle cx="16" cy="8" r="2.4"/><circle cx="10" cy="16" r="2.4"/>'),
    about: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6h.01"/>'),
  };

  // The registry: rail label + group + keywords + the section's content
  // builder. Groups become uppercase labels in the rail (Settings redesign).
  var sections = [
    {
      id: "overview",
      title: "Overview",
      keywords: "overview status attention quick spend providers protection run mode",
      render: overviewHtml,
    },
    {
      id: "providers",
      title: "Providers & Connections",
      group: "Connect",
      keywords: "provider account api key github connection doctor sign in credential codex",
      render: providersHtml,
    },
    {
      id: "balance",
      title: "Credits & Balance",
      group: "Connect",
      keywords: "balance credit usage remaining left top up recharge funds money euro dollar",
      render: balanceHtml,
    },
    {
      id: "models",
      title: "Models & Routing",
      group: "Connect",
      keywords: "model usage limit default routing focus format",
      render: modelsHtml,
    },
    {
      id: "firewall",
      title: "Cost Firewall",
      group: "Spend & safety",
      keywords: "cost firewall panic budget spend cloud gate profile",
      render: firewallHtml,
    },
    {
      id: "usage",
      title: "Model Usage",
      group: "Spend & safety",
      keywords: "usage limit rate window reset session quota remaining requests tokens weekly daily monthly claude codex gemini kimi",
      render: modelUsageHtml,
    },
    {
      id: "permissions",
      title: "Permissions & Safety",
      group: "Spend & safety",
      keywords: "permission tool safety mode approve",
      render: permissionsHtml,
    },
    {
      id: "privacy",
      title: "Privacy & Data",
      group: "System",
      keywords: "privacy data telemetry redacted local",
      render: privacyHtml,
    },
    {
      id: "appearance",
      title: "Appearance",
      group: "System",
      keywords: "theme density motion animation compact reduced dark",
      render: appearanceHtml,
    },
    {
      id: "about",
      title: "About",
      group: "System",
      keywords: "about version release asset build fingerprint runtime source",
      render: aboutHtml,
    },
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

    // Each pane carries its own hero title (Settings redesign), so the shared
    // header is just the global search.
    var header =
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

    var lastGroup = null;
    var rail =
      '<nav class="settings-rail" aria-label="Settings pages">' +
      present
        .map(function (section) {
          var groupLabel =
            section.group && section.group !== lastGroup
              ? '<div class="settings-rail-group">' + esc(section.group) + "</div>"
              : "";
          if (section.group) lastGroup = section.group;
          return (
            groupLabel +
            '<button class="settings-rail-item" type="button" data-rail-target="' +
            esc(section.id) +
            '"><span class="settings-rail-icon" aria-hidden="true">' +
            (ICONS[section.id] || "") +
            '</span><span class="settings-rail-label">' +
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

    // Overview quick controls and attention items navigate between panes the
    // same way the rail does (Settings redesign).
    content.querySelectorAll("[data-go-page]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (search && search.value) {
          search.value = "";
          applySearch("");
        }
        activate(button.dataset.goPage);
        var scroller = page.closest(".scroll");
        if (scroller) scroller.scrollTop = 0;
      });
    });

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
    // Check for updates / Update now (mandatory-update system): a live check
    // always bypasses the cache (force=true) — a click is explicit intent to
    // know right now, never served stale. Applying an update mutates the
    // working tree (fetch, fast-forward, reinstall), so it goes through the
    // same styled inline confirm every other mutating settings action uses.
    var updateCard = q("#settingsUpdateCard");
    function wireUpdateButtons() {
      var checkBtn = q("#settingsCheckUpdate");
      if (checkBtn)
        checkBtn.onclick = function () {
          checkBtn.disabled = true;
          checkBtn.textContent = "Checking…";
          bridge.checkForUpdates(true, function (json2) {
            var result = {};
            try {
              result = JSON.parse(json2);
            } catch (_e) {
              /* keep {} */
            }
            if (updateCard) updateCard.innerHTML = updateStatusHtml(esc, result);
            wireUpdateButtons();
            if (global.__opai && global.__opai.renderUpdateBanner) global.__opai.renderUpdateBanner(result);
            if (result.checked && !result.up_to_date)
              toast("Update available: " + (result.latest_version || result.branch));
            else if (result.checked) toast("You're on the latest version");
            else toast(result.reason || "Could not check for updates");
          });
        };
      var applyBtn = q("#settingsApplyUpdate");
      if (applyBtn)
        applyBtn.onclick = function () {
          var host = applyBtn.closest(".update-card") || applyBtn.parentElement;
          applyBtn.disabled = true;
          ctx
            .inlineConfirm(host, {
              title: "Update OPai now?",
              body: "OPai fetches the latest version, fast-forwards to it, and reinstalls. It refuses if you have uncommitted local changes — nothing is ever discarded.",
              confirmLabel: "Update now",
            })
            .then(function (ok) {
              if (!ok) {
                applyBtn.disabled = false;
                return;
              }
              applyBtn.textContent = "Updating…";
              bridge.applyUpdate(function (json2) {
                var result = {};
                try {
                  result = JSON.parse(json2);
                } catch (_e) {
                  /* keep {} */
                }
                if (!result.ok) {
                  applyBtn.disabled = false;
                  applyBtn.textContent = "Update now";
                  toast(result.error || "Could not update OPai");
                  return;
                }
                if (updateCard)
                  updateCard.innerHTML =
                    '<div class="update-card ok" data-update-status="restart"><div class="update-head"><span class="update-dot"></span><span class="update-title">Updated to ' +
                    esc(result.installed_version || "the latest version") +
                    ' — restart to finish</span></div><div class="actions"><button class="btn primary" id="settingsRestartOpai">Restart now</button></div></div>';
                if (global.__opai && global.__opai.renderUpdateBanner)
                  global.__opai.renderUpdateBanner({ checked: true, up_to_date: true });
                var restartBtn = q("#settingsRestartOpai");
                if (restartBtn)
                  restartBtn.onclick = function () {
                    restartBtn.disabled = true;
                    restartBtn.textContent = "Restarting…";
                    bridge.restartOPai();
                  };
              });
            });
        };
    }
    wireUpdateButtons();
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

    // Composer style (Composer Redesign): persist per workspace and switch the
    // live composer direction immediately.
    page.querySelectorAll("[data-composer-style-key]").forEach(function (segment) {
      var key = segment.dataset.composerStyleKey;
      segment.querySelectorAll("button").forEach(function (button) {
        button.onclick = function () {
          segment.querySelectorAll("button").forEach(function (other) {
            other.classList.toggle("active", other === button);
            other.setAttribute("aria-pressed", other === button ? "true" : "false");
          });
          bridge.savePref(key, button.dataset.value);
          if (ctx.d && ctx.d.prefs) ctx.d.prefs.composerStyle = button.dataset.value;
          if (global.OPaiComposer) global.OPaiComposer.setStyle(button.dataset.value);
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
