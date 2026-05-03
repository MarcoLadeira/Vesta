# MCP Setup

MCP servers are registered in `hub/registry/mcp_servers.yaml`.

Each server declares:

- transport
- command and args
- environment variables
- enabled state
- permission level
- cost level
- allowed paths
- forbidden paths
- confirmation requirements

Use:

```powershell
python -m opaihub mcp list
```

Generated examples live in `hub/mcp/mcp_config.example.json`.

Default policy:

- local/read-only first
- project roots only
- GitHub/browser/database disabled until a project opts in
- confirmation before write-capable or cloud MCP tools
