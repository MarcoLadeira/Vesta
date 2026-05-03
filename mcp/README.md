# OPcoding MCP

Phase 1 stores MCP profiles and policy only. Phase 2 should add a launcher that reads these profiles, validates permissions, and starts local MCP servers per project.

Default posture:

- filesystem read-only
- local git only
- testing commands only
- local memory only
- GitHub/browser/database disabled until project profile enables them
