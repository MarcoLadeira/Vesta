# Final Third Implementation

The final layer turns OPcoding from a local helper into an orchestration-ready coding system.

## Added Capabilities

- Batched agent runner with shared context: `op agents "task"`.
- No-spend model prompt preparation and cache: `op ask "task"`.
- Project symbol/file index: `op index . build`, `op index . search AuthService`.
- Local SQLite memory and decisions: `op memory . add note title body`.
- MCP profile validation and Codex config rendering: `op mcp . doctor`, `op mcp . render`.
- One-prompt-to-two-prompt shipping planner: `op ship "build notes app"`.
- Deployment and rollback planner: `op deploy .`.
- Dashboard report: `op dashboard .`.
- Git hook installer for staged secret checks: `op hooks . install`.
- GitHub Actions workflow generator: `op ci . github`.

## Cost Behavior

`op ask` prepares prompts and caches bundles by default. It does not call a model unless `--execute-model` is passed and an `OPCODING_MODEL_L*_COMMAND` environment variable is configured.

GPT-5.5 Max remains blocked unless the task routes to `L4` and `--confirm-expensive` is explicitly provided.

## Superpowers

Superpowers was installed globally through:

```text
C:\Users\Frist\.codex\superpowers
C:\Users\Frist\.agents\skills\superpowers -> C:\Users\Frist\.codex\superpowers\skills
```

Restart Codex to let native skill discovery load it automatically.
