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
    id: "account:copilot:gpt-5.2",
    label: "Copilot · GPT-5.2",
    advanced_label: "Copilot GPT-5.2 (fast · high · $$)",
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
      { label: "Estimated waste", value: "46,200 tokens", severity: "warning" },
      { label: "Largest source", value: "node_modules", severity: "danger" },
      { label: "Potential reduction", value: "12.0x", severity: "success" },
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
  firewall: { profile: "solo-balanced", panic: false, spent_today: 0.42, cloud_gate: true },
  permissions: [
    { label: "Read files", state: "allow" },
    { label: "Edit files", state: "ask" },
    { label: "Network / web", state: "block" },
  ],
  accounts: CONNECTED_ACCOUNTS,
  about: { version: "0.2.0a1", release_stage: "alpha.1" },
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
      prefs: { model: "auto", mode: "safe-auto", focus: "general", format: "normal", showPanel: true },
      accounts: CONNECTED_ACCOUNTS,
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
          { label: "No secrets stored", tone: "safe" },
        ],
      },
    },
    dashboards: DASHBOARDS,
    prompts: PROMPTS,
    settings: SETTINGS,
  };
  return merge(base, overrides);
}
