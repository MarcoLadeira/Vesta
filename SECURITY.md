# Security Policy

OPai is pre-alpha. Treat it as powerful local automation that should be reviewed before production use.

## Supported Version

Only the latest `main` branch and latest tagged pre-alpha release receive security fixes.

## Report A Security Issue

Please create a private security advisory on GitHub if available, or contact the maintainer through the repository owner profile.

Do not post secrets, live tokens, private logs, or private repository data in public issues.

## Safety Defaults

- Cloud models and paid APIs are disabled by default.
- Destructive shell, Git, deploy, and publish actions require confirmation.
- Prompt storage is disabled by default.
- Logs and command output should be redacted before sharing.
- MCP servers should be enabled per project with narrow path permissions.

## Maintainer Response Goal

For pre-alpha, the goal is to acknowledge reproducible security reports within 7 days.
