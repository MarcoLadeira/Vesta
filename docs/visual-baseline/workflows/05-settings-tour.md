# Workflow: settings tour

**Recording:** `recordings/04-settings-tour.webm`
**Screens:** `screens/settings/SETTINGS-*.png`, `screens/providers/PROVIDERS-CONNECTED-001-*.png`, `screens/permissions/PERMISSIONS-SAFETY-001-*.png`

1. **Settings** opens from the header (gear) or the sidebar footer. It is a
   paned layout: a labelled rail on the left, one page at a time on the right,
   with a "Search settings…" box and scope pills (**App-wide / This project /
   Local only**) plus the current workspace path (`/demo`).
2. Rail pages, in order, grouped under CONNECT / SPEND & SAFETY / SYSTEM:
   **Overview** (OPai status cards: protection/cloud gate, routing profile,
   providers, default run mode; "Needs attention" list — honestly calm when
   all is well; quick-control tiles), **Providers & Connections** (Connection
   Doctor per account: credential type, CLI installed, last checked, Test /
   Sign-in buttons), **Models & Routing**, **Cost Firewall** (budget caps,
   panic mode), **Model Usage** (per-provider usage: Claude "unavailable"
   session window, Gemini live daily quota 18%, Kimi prepaid balance $8.42,
   Groq not configured), **Permissions & Safety** (per-mode permission
   summaries), **Privacy & Data**, **Appearance**, **Tools & Insights**
   (Prompt Library tile + seven insight dashboards), **About** (asset build
   fingerprint, update policy controls).
3. **Tools & Insights** tiles deep-link into full dashboard views: Money Saved
   (hero $12.34 headline, spent/saved KPIs, latest receipt card), Cost
   Firewall, Agents readiness (per-client capture status), Guarded Workflows
   (approval-gated cards), plus Context Waste, Benchmark and Proof Bundle
   (listed, not all captured).
4. The active rail item carries `aria-current="page"`; the URL hash deep-links
   (`#settings/<id>`).
