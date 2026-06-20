# OPai Roadmap

## Milestone: Cost-Control Control Plane (in progress)

Foundations delivered toward "OPai is the AI coding cost firewall":

- **#35 Activation reliability** — five-client detection (Claude, Codex,
  Copilot, Cursor, Cline) with active/broken/missing readiness, stale-path
  detection, `opai doctor`/`status`, and `opai update`/`uninstall`.
- **#36 Cost firewall & savings** — local usage ledger, cost model, and
  `opai savings` (privacy-safe: hashes, not prompts).
- **#37 Routing & policy** — `solo-cheap`/`solo-balanced`/`team-safe`/
  `enterprise-strict` profiles, cloud/paid gating, loopback validation, and an
  offline model-eval harness (`opai models eval`).
- **#38 Open-core editions** — Free/Pro/Team/Enterprise boundaries, feature
  flags, and pricing docs (`opai edition`).
- **#39 Guarded workflows** — shared contract, fail-closed action gates, path
  locks, evidence packets, and reusable templates (`opai guard`).
- **#40 Market proof** — cost-firewall positioning, grounded before/after proof,
  quickstart, and launch checklist.
- **Benchmarking layer** — `opai benchmark` compares normal AI use with
  OPai-routed local-first use and produces an OPai Efficiency Score.

## Near Term

- Publish OPai as an installable Python package.
- Add `opai tool install <id>` with per-tool confirmations.
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
