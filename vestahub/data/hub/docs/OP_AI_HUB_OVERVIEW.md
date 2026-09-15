# Vesta / Vesta Hub Overview

Vesta 0.2.0 alpha.1 is the branded CLI and install layer for Vesta Hub. Vesta Hub is a modular, local-first AI tools platform. It is not a single giant agent. It is a registry and routing layer that helps decide which tool, agent, workflow, MCP server, or model tier should handle a task.

The hub builds on the existing OPcoding system:

- OPcoding runs local project scans, tests, GitOps, tools, context, and model-routing preparation.
- Vesta Hub organizes the wider tool ecosystem: coding, research, browser, files, data, security, deployment, productivity, local models, cloud models, and MCP servers.

Core rule: use deterministic/local tools before AI reasoning, and ask before paid, cloud, or risky actions.

Phase 1 provides metadata and inspection. The Phase 2 foundation adds project overlays, registry validation, safe workflow planning, MCP rendering, health history, local model discovery, and dashboard generation.

Phase 3 adds the public Vesta brand CLI, local install scripts, tool discovery, analytics summaries, command sandbox classification, manual schedules, static HTML dashboard output, and disabled-by-default team/cloud config.

Start with:

```sh
vesta install
vesta doctor
vesta dashboard --html
vesta hub discover tools
```
