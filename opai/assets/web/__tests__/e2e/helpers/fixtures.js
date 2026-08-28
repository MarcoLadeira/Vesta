// Mirrors the production IA (opai/gui_nav.py): a ChatGPT-simple unlabeled top
// level, one folded Insights group, and Settings living in the sidebar footer
// (so it is intentionally NOT a nav item here).
export const FULL_NAV = [
  {
    group: "",
    collapsed: false,
    items: [
      { id: "chat", label: "Chat", kind: "view" },
      { id: "prompts", label: "Prompt Library", kind: "view" },
    ],
  },
  {
    group: "Insights",
    collapsed: true,
    items: [
      { id: "home", label: "Money Saved", kind: "dashboard", section: "home" },
      { id: "firewall", label: "Cost Firewall", kind: "dashboard", section: "firewall" },
      { id: "context", label: "Context Waste", kind: "dashboard", section: "context" },
      { id: "benchmark", label: "Benchmark", kind: "dashboard", section: "benchmark" },
      { id: "agents", label: "Agents", kind: "dashboard", section: "agents" },
      { id: "proof", label: "Proof Bundle", kind: "dashboard", section: "proof" },
      { id: "workflows", label: "Workflows", kind: "dashboard", section: "workflows" },
    ],
  },
];

export const MODELS = [
  {
    id: "account:claude:opus",
    label: "Claude · Opus 4.8",
    advanced_label: "Claude Opus 4.8 (slower · highest · $$$)",
    kind: "account",
    group: "claude",
    provider: "claude",
    paid: true,
    available: true,
    badge: "slower · highest · $$$",
  },
  {
    id: "account:codex:gpt-5.5",
    label: "Codex · GPT-5.5",
    advanced_label: "Codex GPT-5.5 (fast · high · $$)",
    kind: "account",
    group: "codex",
    provider: "codex",
    paid: true,
    available: true,
    badge: "fast · high · $$",
  },
  {
    id: "account:copilot:gpt-5.4",
    label: "Copilot · GPT-5.4",
    advanced_label: "Copilot GPT-5.4 (fast · high · $$)",
    kind: "account",
    group: "copilot",
    provider: "copilot",
    paid: true,
    available: true,
    badge: "fast · high · $$",
  },
  {
    id: "free:gemini:gemini-3.1-flash-lite",
    label: "Gemini · 3.1 Flash-Lite (free tier)",
    advanced_label: "Google Gemini 3.1 Flash-Lite via Google AI API (free-tier eligible)",
    kind: "free",
    group: "free",
    provider: "gemini",
    paid: false,
    available: true,
    badge: "free tier",
  },
  {
    id: "free:groq:openai/gpt-oss-120b",
    label: "Groq · GPT-OSS 120B (free tier)",
    advanced_label: "OpenAI GPT-OSS 120B via Groq (free-tier eligible)",
    kind: "free",
    group: "free",
    provider: "groq",
    paid: false,
    available: false,
    disabled_reason: "Set GROQ_API_KEY to enable Groq · GPT-OSS 120B (free tier)",
    badge: "free tier",
  },
  {
    id: "ollama:qwen2.5-coder",
    label: "Qwen 2.5 Coder · local",
    advanced_label: "Qwen 2.5 Coder via ollama on this device",
    kind: "local",
    group: "local",
    provider: "local",
    paid: false,
    available: true,
    badge: "private · free",
  },
  {
    id: "auto",
    label: "OPai · Auto mode",
    advanced_label: "Automatic local-first routing",
    kind: "auto",
    group: "routing",
    provider: "auto",
    paid: false,
    available: true,
    badge: "routes cheapest · $0 when local",
  },
];

export const CONNECTED_ACCOUNTS = [
  { id: "claude", label: "Claude", connected: true, authenticated: true, status: "connected" },
  { id: "codex", label: "Codex", connected: true, authenticated: true, status: "connected" },
  { id: "copilot", label: "Copilot", connected: true, authenticated: true, status: "connected" },
];

export const DISCONNECTED_ACCOUNTS = CONNECTED_ACCOUNTS.map((account) => ({
  ...account,
  connected: false,
  authenticated: false,
  status: "not connected",
}));

export const DASHBOARDS = {
  home: {
    title: "Money Saved",
    subtitle: "Observed and estimated values are kept separate.",
    hero: { headline: "$12.34", caption: "Estimated savings across captured routes", severity: "success" },
    kpis: [
      { label: "Spent today", value: "$0.42", severity: "neutral" },
      { label: "Paid calls avoided", value: "7", severity: "success" },
      { label: "Context reduced", value: "18,400 tokens", severity: "accent" },
    ],
    cards: [
      {
        title: "Latest receipt",
        status: "RECORDED",
        severity: "success",
        body: "Local route avoided one estimated paid call.",
        metrics: [
          { label: "Actual spend", value: "$0.0000", severity: "neutral" },
          { label: "Estimated savings", value: "$0.0840", severity: "success" },
          { label: "Confidence", value: "estimated", severity: "warning" },
        ],
      },
    ],
    actions: [{ id: "copy_savings", label: "Copy savings report", command: "opai savings --markdown" }],
  },
  firewall: {
    title: "Cost Firewall",
    subtitle: "Paid and network routes require policy approval.",
    kpis: [
      { label: "Profile", value: "solo-balanced", severity: "info" },
      { label: "Daily budget", value: "$0.42 / $2.00", severity: "success" },
      { label: "Panic mode", value: "off", severity: "neutral" },
    ],
    cards: [
      { title: "Expensive request warning", body: "Frontier route estimated at $0.31. Confirmation required.", severity: "warning" },
      { title: "Recently blocked", body: "Deploy request blocked before spend.", severity: "danger" },
    ],
    actions: [
      { id: "panic_toggle", label: "Enable panic mode" },
      { id: "copy_budget", label: "Copy budget status", command: "opai budget status" },
    ],
  },
  context: {
    title: "Context Waste",
    subtitle: "Large generated folders can consume model context.",
    kpis: [
      { label: "Estimated wasted tokens", value: "46,200 tokens", severity: "warning" },
      { label: "Largest source", value: "node_modules", severity: "danger" },
      { label: "Potential reduction", value: "12.0x", severity: "success" },
      { label: "Estimated cost if sent", value: "$0.13", description: "Not money spent — projection for uncompressed context.", severity: "neutral" },
    ],
    cards: [
      { title: "node_modules", body: "Generated dependency files", metrics: [{ label: "Estimated tokens", value: "41,000", severity: "warning" }] },
      { title: "dist", body: "Generated build output", metrics: [{ label: "Estimated tokens", value: "5,200", severity: "warning" }] },
    ],
    actions: [{ id: "copy_context", label: "Copy profile command", command: "opai context profile" }],
  },
  benchmark: {
    title: "Benchmark Proof",
    subtitle: "Local fixture benchmark, not an official external leaderboard.",
    hero: { headline: "99.4", caption: "OPai effectiveness index", severity: "success" },
    kpis: [
      { label: "Context reduction", value: "50x", severity: "success" },
      { label: "Paid calls avoided", value: "16", severity: "success" },
      { label: "Risk blocks", value: "6", severity: "warning" },
    ],
    cards: [{ title: "Approved claim", body: "OPai reduced context by 50x and avoided 16 paid calls on the 16-task local benchmark suite." }],
    actions: [{ id: "copy_benchmark", label: "Copy benchmark command", command: "opai benchmark run --suite max --mode both" }],
  },
  agents: {
    title: "Agent Readiness",
    subtitle: "Know which AI clients are active and capturable.",
    cards: [
      { title: "Claude", status: "ACTIVE", severity: "success", metrics: [{ label: "Capture", value: "selective proxy", severity: "success" }] },
      { title: "Codex", status: "ACTIVE", severity: "success", metrics: [{ label: "Wrapper", value: "installed", severity: "success" }] },
      { title: "Copilot", status: "READY", severity: "info", metrics: [{ label: "Account", value: "connected", severity: "success" }] },
    ],
    actions: [{ id: "safe_repair", label: "Repair clients" }],
  },
  proof: {
    title: "Proof Bundle",
    subtitle: "Private, signed, and redacted evidence.",
    kpis: [
      { label: "Signature", value: "verified", severity: "success" },
      { label: "Artifacts", value: "4", severity: "info" },
      { label: "Prompt storage", value: "none", severity: "success" },
    ],
    cards: [
      { title: "What changed", items: ["2 files changed", "12 tests passed"] },
      { title: "What it cost", body: "$0.0420 actual spend; no savings claimed." },
      { title: "Privacy guarantee", body: "Task hash only. No raw prompt or secret." },
    ],
    actions: [{ id: "copy_proof", label: "Copy proof command", command: "opai proof bundle" }],
  },
  workflows: {
    title: "Guarded Workflows",
    subtitle: "Risky actions remain approval-gated.",
    cards: [
      { title: "PR review", status: "READ-ONLY", severity: "success", body: "Reviews changes and produces evidence." },
      { title: "Security audit", status: "READ-ONLY", severity: "success", body: "Checks secrets, dependencies, and permissions." },
      { title: "Release preflight", status: "CONFIRM", severity: "warning", body: "Never pushes, deploys, or publishes automatically." },
    ],
    actions: [{ id: "copy_workflow", label: "Copy workflow command", command: "opai workflow plan release_preflight" }],
  },
};

export const PROMPTS = [
  { id: "explain_repo", title: "Explain this repo", category: "Coding", desc: "High-level tour of the codebase.", template: "Give me a high-level tour of this codebase.", mode: "explain", tags: ["overview"] },
  { id: "write_tests", title: "Write tests for a file", category: "Testing", desc: "Add focused unit tests.", template: "Write focused unit tests for <file>.", mode: "test", tags: ["unit"] },
  { id: "security_audit", title: "Security review", category: "Security", desc: "Audit for risks, read-only.", template: "Audit <area> for security risks. Do not modify files.", mode: "review", tags: ["audit"] },
];

export const SETTINGS = {
  prefs: { default_model: "auto", default_mode: "safe-auto" },
  firewall: {
    profile: "solo-balanced", panic: false, spent_today: 0.42, cloud_gate: true,
    caps: { daily_usd_limit: 2, monthly_usd_limit: null, per_task_hard_limit_usd: 0.5 },
    spent_month: 3.1, remaining: { today_usd: 1.58, month_usd: null },
    local_first: "deterministic tools -> cache -> local model -> confirmed cloud",
  },
  permissions: [
    { label: "Read files", state: "allow", note: "Anywhere in the repo" },
    { label: "Edit files", state: "ask", note: "Pauses for your OK" },
    { label: "Push to a remote", state: "ask", note: "Asks every time in this mode" },
    { label: "Network / web", state: "block" },
  ],
  modePermissions: [
    { id: "ask", label: "Ask", summary: "1 allowed · 0 ask · 6 blocked", active: false },
    { id: "plan", label: "Plan", summary: "1 allowed · 0 ask · 6 blocked", active: false },
    { id: "safe-auto", label: "Safe Auto", summary: "3 allowed · 3 ask · 1 blocked", active: true },
    { id: "approve-edits", label: "Approve Edits", summary: "2 allowed · 4 ask · 1 blocked", active: false },
    { id: "full-auto", label: "Full Auto", summary: "9 allowed · 0 ask · 0 blocked", active: false },
  ],
  privacy: {
    prompts_stored: false,
    statements: [
      "No telemetry — nothing leaves your machine.",
      "Raw prompts are never stored; the local ledger keeps one-way task hashes and counts only.",
      "Saved chat is redacted and kept per workspace on this machine; clear it any time below or from the sidebar.",
      "Local-first routing; a cloud model is used only after you confirm it.",
    ],
  },
  accounts: CONNECTED_ACCOUNTS,
  // Model Usage: one provider per state so the page's variety is exercised.
  providerUsage: [
    {
      provider: "claude", displayName: "Claude", kind: "account", configured: true,
      status: "unavailable",
      window: { type: "rolling", label: "5-hour session window", seconds: 18000, metric: "session" },
      official: { available: false },
      opaiTracked: { calls: 12, tokens: 48000, tasks: 4, windowLabel: "All time via OPai", lastUsedAt: Date.now() / 1000 - 18 * 24 * 3600 },
      detail: "Claude subscriptions meter a rolling 5-hour session window; the exact percentage is only visible in Claude directly. OPai's own count below only includes messages sent through OPai's chat — not the claude CLI used directly.",
      checkUrl: "https://claude.ai/settings/usage", supportsRefresh: false,
    },
    {
      provider: "gemini", displayName: "Gemini", kind: "free", configured: true,
      status: "live",
      window: { type: "daily", label: "Daily requests (free tier)", seconds: 86400, metric: "requests" },
      official: {
        available: true, source: "provider", metric: "requests",
        limit: 1500, remaining: 1230, used: 270, percent: 18,
        resetsAt: 4102444800, resetsInSeconds: 7200, observedAt: 4102437600, stale: false,
      },
      opaiTracked: { calls: 270, tokens: 120000, tasks: 33, windowLabel: "Today" },
      detail: "Reported by the provider on your recent calls.",
      checkUrl: "https://aistudio.google.com", supportsRefresh: true,
    },
    {
      provider: "kimi", displayName: "Kimi (Moonshot)", kind: "free", configured: true,
      status: "live",
      window: { type: "balance", label: "Prepaid credit", seconds: null, metric: "credit" },
      official: {
        available: false, source: "provider", metric: "credit",
        limit: null, remaining: 8.42, used: null, percent: null, currency: "USD",
        resetsAt: null, resetsInSeconds: null, observedAt: 4102437600, stale: false,
      },
      opaiTracked: { calls: 5, tokens: 9000, tasks: 2, windowLabel: "All time" },
      detail: "Prepaid credit remaining, reported by the provider.",
      checkUrl: "https://platform.moonshot.ai", supportsRefresh: true,
    },
    {
      provider: "groq", displayName: "Groq", kind: "free", configured: false,
      status: "not_configured",
      window: { type: "daily", label: "Daily requests (free tier)", seconds: 86400, metric: "requests" },
      official: { available: false },
      opaiTracked: { calls: 0, tokens: 0, tasks: 0, windowLabel: "Today" },
      detail: "Not connected. Connect Groq to see usage.",
      checkUrl: "https://console.groq.com/settings/limits", supportsRefresh: true,
    },
  ],
  about: {
    version: "0.2.0a1",
    release_stage: "alpha.1",
    build: {
      assetFingerprint: "7ac9f12b4e88aabbccddeeff00112233445566778899aabbccddeeff00112233",
      assetCount: 9,
      runtimeSource: "source_checkout",
    },
    update: {
      operation: { state: "up_to_date", candidate: null, safe_diagnostic: null },
      policy: {
        discovery_enabled: true, automatic_downloads: false,
        automatic_install_on_quit: false, channel: "stable", owner: "opai",
      },
      installed: { version: "0.2.0a1", build_id: "test-build", install_type: "portable" },
    },
  },
};

function merge(base, override) {
  if (!override || typeof override !== "object" || Array.isArray(override)) return override === undefined ? base : override;
  const result = { ...base };
  for (const [key, value] of Object.entries(override)) {
    result[key] = value && typeof value === "object" && !Array.isArray(value)
      ? merge(base && base[key] ? base[key] : {}, value)
      : value;
  }
  return result;
}

export function fullScenario(overrides = {}) {
  const base = {
    boot: {
      navGroups: FULL_NAV,
      models: MODELS,
      selectedModel: "auto",
      modes: [
        { id: "ask", label: "Ask" },
        { id: "plan", label: "Plan" },
        { id: "safe-auto", label: "Safe Auto" },
        { id: "approve-edits", label: "Approve Edits" },
        { id: "full-auto", label: "Full Auto" },
      ],
      // onboardingSeen defaults true so the existing suite behaves as returning
      // users; onboarding.spec.js overrides it to false to drive the tour (#250).
      prefs: { model: "auto", mode: "safe-auto", focus: "general", format: "normal", showPanel: true, onboardingSeen: true },
      taskModes: [
        { id: "general", label: "General" },
        { id: "coding", label: "Coding" },
        { id: "writing", label: "Writing" },
      ],
      outputFormats: [
        { id: "normal", label: "Normal" },
        { id: "concise", label: "Concise" },
      ],
      accounts: CONNECTED_ACCOUNTS,
      // The prompt list feeds the composer's Up-arrow history; `conversations`
      // is what the sidebar lists. They are deliberately different data.
      recents: ["third prompt", "second prompt", "first prompt"],
      conversations: [
        { id: "c2", title: "How does routing work?", message_count: 4, updated_at: "2026-08-02" },
        { id: "c1", title: "Explain the budget guard", message_count: 2, updated_at: "2026-08-01" },
      ],
      status: { on: true, line: "Auto · Safe Auto · $0.42 today · $12.34 saved" },
      inspector: {
        rows: [
          { label: "Model", value: "Auto" },
          { label: "Run mode", value: "Safe Auto" },
          { label: "Workspace", value: "3 files indexed · main" },
        ],
        budget: { pct: 21, text: "$0.42 / $2.00 today" },
        permissions: SETTINGS.permissions,
        privacy: [
          { label: "Local-first · cloud on confirm", tone: "info" },
          { label: "No telemetry", tone: "safe" },
          { label: "Raw build prompts not logged", tone: "safe" },
        ],
      },
    },
    dashboards: DASHBOARDS,
    prompts: PROMPTS,
    settings: SETTINGS,
  };
  return merge(base, overrides);
}
