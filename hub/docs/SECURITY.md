# Security

Security defaults:

- Project-root file access only.
- `.env`, `.git`, `.opcoding-tools`, SSH keys, cloud credentials, and private keys are forbidden by default.
- Shell commands are allowlisted or confirmation-gated.
- MCP servers must declare roots and permission level.
- Cloud tools are disabled unless explicitly enabled.
- Logs must redact secrets.

Use:

```powershell
opai hub tool health --id detect-secrets
python -m opcoding tools . run detect-secrets
opai hub sandbox check --command "git reset --hard"
```

Risky commands are listed in `hub/security/risky_commands.yaml`.
