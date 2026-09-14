# Discovery, Analytics, And Sandbox

Vesta 0.1.1 pre-alpha includes low-risk foundations for operating a larger hub.

## Discovery

```sh
opai hub discover tools
```

Discovery reads `hub/registry/tools.yaml`, checks whether referenced command names exist on PATH, and reports whether required environment variables are present. It does not install packages, call APIs, or start services.

## Analytics

```sh
opai hub analytics status
```

Analytics are local only. The summary includes registry counts, enabled tools, enabled MCP servers, latest health failures, and an estimated spend placeholder. No telemetry leaves the machine.

## Sandbox Classification

```sh
opai hub sandbox check --command "git reset --hard"
```

The sandbox classifier reads `hub/security/risky_commands.yaml` and returns one of:

- `allow`
- `confirm`
- `deny`

It is a policy classifier in pre-alpha, not a full execution sandbox.
