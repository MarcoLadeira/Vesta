---
name: graphify-code-graph
description: Use when a task needs codebase understanding, cross-file impact, or "where is X / what connects A to B" answers. Query the local graphify knowledge graph before grepping or loading whole files.
---
# Graphify Code Graph
Query the project's local knowledge graph (graphify, MIT) instead of reading files into context. Code parsing is tree-sitter AST: free, local, zero model credits. L0 first.

## Workflow
1. Check for `graphify-out/graph.json`. If missing or stale, run `graphify extract . --update` (code-only builds are offline and free).
2. Answer with scoped queries: `graphify query "<question>"`, `graphify path "A" "B"`, `graphify explain "Symbol"`.
3. Read only the files the graph cites (nodes carry source file + line), then answer or escalate with that evidence attached.

## Rules
- If `graphify --version` fails, fall back to the repo-map-context skill; ask before any network install.
- Prefer `--update` over `--force`; never rebuild doc-heavy corpora in paid sessions without confirmation (cloud-model-gate).
- For repeated queries in one session, prefer the MCP server: `python -m graphify.serve graphify-out/graph.json`.
- Keep `graphify-out/` committed so the team shares one map; never commit `graphify-out/cost.json`.
