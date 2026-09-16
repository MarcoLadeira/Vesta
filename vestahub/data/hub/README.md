# Vesta Hub

Vesta Hub is the registry-first layer on top of OPcoding. It is designed to organize hundreds of AI tools, agents, MCP servers, workflows, prompts, and project integrations while staying local-first and cost-aware.

Phase 1 is intentionally simple:

- YAML registries for tools, agents, workflows, MCP servers, and models.
- Cost, context, and security policies.
- Low-token prompt library.
- MCP example config.
- Minimal `op-hub` CLI for listing registries and safe health checks.

Run:

```powershell
python -m vestahub scan
python -m vestahub list-tools
python -m vestahub list-agents
python -m vestahub list-workflows
python -m vestahub skills list
python -m vestahub models recommend "fix failing tests cheaply"
python -m vestahub tool health --id ruff
python -m vestahub doctor
```

The hub does not call paid models, cloud APIs, or destructive commands by default.
