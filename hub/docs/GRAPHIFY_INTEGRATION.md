# Graphify integration

Vesta treats [graphify](https://github.com/Graphify-Labs/graphify) (MIT License, (c) 2026 Safi Shamsi / Graphify Labs) as the preferred L0 context tool for codebase questions. Graphify maps a project into a local knowledge graph (`graphify-out/graph.json`) so agents answer "where is X / what uses Y / what connects A to B" with a scoped graph query instead of pulling files into model context.

## Why it fits Vesta

- **Free code maps.** Code is parsed with tree-sitter AST — deterministic, fully local, zero model credits. Exactly Vesta's L0 posture.
- **Smaller prompts.** `graphify query` returns a scoped subgraph; agents read only the files the graph points to, which shows up directly as context reduction in `opai metrics`.
- **No vendoring.** Vesta never copies graphify code into this repo. Users install the `graphifyy` package themselves; its MIT copyright notice travels with the package.

## Install and register

```sh
uv tool install graphifyy            # or: pipx install graphifyy
graphify install                     # Claude Code
graphify install --platform codex    # Codex (uses $graphify)
graphify install --platform kimi     # Kimi Code
graphify install --platform agents   # generic ~/.agents/skills (the location Vesta discovery already watches)
```

Then build the graph once per project:

```sh
graphify extract .                   # headless; or run "/graphify ." inside the assistant
```

## Use in Vesta workflows

1. Route "understand / locate / impact" tasks to the graph before any model call:
   - `graphify query "<question>"` — scoped subgraph for a plain-language question
   - `graphify path "A" "B"` — shortest connection between two concepts
   - `graphify explain "Symbol"` — one node's connections, tagged EXTRACTED/INFERRED
2. Load only the files the graph cites (every node carries a source file + line).
3. Escalate to a model only when graph evidence is insufficient; attach the subgraph instead of raw file dumps.

The `graphify-code-graph` skill (registered in `hub/skills/registry.yaml`) teaches Vesta-managed agents this workflow, including the fallback to `repo-map-context` when the CLI is not installed.

## MCP access

For repeated queries in one session, serve the graph over MCP instead of shelling out per query:

```sh
python -m graphify.serve graphify-out/graph.json     # requires: uv tool install "graphifyy[mcp]"
```

`hub/registry/mcp_servers.yaml` carries a disabled-by-default `graphify` entry (read-only, project-scoped paths), and `hub/mcp/mcp_config.example.json` shows the client config. Enable it per project after the first graph build.

## Cost and privacy notes

- Code-only extraction is offline. Docs, PDFs, images, and video go through the active assistant's model (or an explicit API key in headless mode) — confirm before doc-heavy extraction in paid sessions, per the cloud-model-gate skill.
- Commit `graphify-out/` so the team shares one map; keep `graphify-out/cost.json` in `.gitignore`.
- If the `graphify` CLI is absent, agents must fall back to the repo-map-context skill and ask before any network install (tool-installer policy).
