# Secrets Policy

OP AI Hub must never store raw API keys, credentials, private keys, or production secrets.

Rules:

- Keep secrets in environment variables or a dedicated secret manager.
- Do not commit `.env`, private keys, token files, or generated secret baselines with raw values.
- Redact command logs before writing them.
- Run `detect-secrets` before commit and `gitleaks` before release when practical.
- Do not send secret-containing files to cloud models.
- If a secret is pasted into chat or logs, rotate it.

Default environment variables are referenced by name only, such as `MORPH_API_KEY`, never by value.
