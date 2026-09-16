# Phase 3 Foundation

This phase finishes the practical Vesta platform spine so the project can grow toward an OpenClaw-style local AI hub without becoming expensive or hard to maintain.

## Added Foundations

- Branded `vesta` package and CLI.
- Local install scripts for Windows and POSIX shells.
- Vesta registry entry and brand metadata.
- Tool discovery that inspects local commands and environment variables without installing anything.
- Static HTML dashboard generation.
- Local analytics summary with zero telemetry.
- Manual schedule registry for recurring workflow intent.
- Sandbox command classifier based on risky command policy.
- Team/cloud configuration files with cloud disabled by default.
- Templates for adding tools, agents, and workflows.

## Still Intentionally Deferred

- Published package release.
- Long-running scheduler daemon.
- Live dashboard server.
- Sandboxed command execution runtime.
- Multi-user team sync.
- Cloud registry sync.
- Automatic installer marketplace.

These are deferred because each can create cost, security, or maintenance risk if shipped before the local registry layer is mature.
