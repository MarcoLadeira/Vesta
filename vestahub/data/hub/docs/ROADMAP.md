# Vesta Roadmap

## Free Public Alpha

Every implemented Vesta alpha capability is free. The roadmap distinguishes
work that is available now from work that still needs safe implementation; it
does not define paid access tiers or self-declared entitlements.

## Milestone: Cost-Control Control Plane (in progress)

Foundations delivered toward "Vesta is the AI coding cost firewall":

- **#35 Activation reliability** — five-client detection (Claude, Codex,
  Copilot, Cursor, Cline) with active/broken/missing readiness, stale-path
  detection, `vesta doctor`/`status`, and `vesta update`/`uninstall`.
- **#36 Cost firewall & savings** — local usage ledger, cost model, and
  `vesta savings` (privacy-safe: hashes, not prompts).
- **#37 Routing & policy** — `solo-cheap`/`solo-balanced`/`team-safe`/
  `enterprise-strict` profiles, cloud/paid gating, loopback validation, and an
  offline model-eval harness (`vesta models eval`).
- **#38 Free-alpha availability** — commercial edition gates removed; `vesta
  edition` remains a compatibility availability diagnostic.
- **#39 Guarded workflows** — shared contract, fail-closed action gates, path
  locks, evidence packets, and reusable templates (`vesta guard`).
- **#40 Market proof** — cost-firewall positioning, grounded before/after proof,
  quickstart, and launch checklist.
- **Benchmarking layer** — `vesta benchmark` compares normal AI use with
  Vesta-routed local-first use and produces a Vesta Efficiency Score.

## Near Term

- Publish Vesta as an installable Python package.
- Add `vesta tool install <id>` with per-tool confirmations.
- Add richer project adapters for Node, Python, .NET, Go, Rust, and full-stack apps.
- Add a local dashboard server with no telemetry.
- Expand workflow execution mappings for safe L0 commands.
- Add a usage ledger for model calls and optional API adapters.

## Advanced

- Marketplace-style registry with signatures and duplicate detection.
- Auto-discovery for installed MCP servers, local models, CLIs, browser tools, and databases.
- Local model orchestration through Ollama, LM Studio, llama.cpp, and OpenAI-compatible local endpoints.
- Browser automation profiles using Playwright.
- Sandboxed command runner with allowlists, deny rules, timeouts, and path restrictions.
- Team mode with shared registries and optional cloud sync.
- Optional cloud sync mode that is disabled by default and requires explicit confirmation.
- Dashboard analytics for health, cost, workflows, model routing, and project readiness.
