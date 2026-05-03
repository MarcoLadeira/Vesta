# Morph Fast Apply Adapter

OPcoding includes a Morph adapter for fast code-apply style edits. It is disabled by default so it cannot spend credits accidentally.

## Setup

Set the API key outside the repo:

```powershell
$env:MORPH_API_KEY = "your-key"
```

For persistent setup, use your normal secret manager or user environment. Do not commit keys to this repo.

Check configuration:

```powershell
python -m opcoding morph . doctor
```

Prepare a request without spending:

```powershell
python -m opcoding morph . apply-snippet `
  --instruction "Add error handling" `
  --code "async function fetchUser(id) { const res = await fetch('/api/users/' + id); return res.json(); }" `
  --update "async function fetchUser(id) { const res = await fetch('/api/users/' + id); if (!res.ok) throw new Error('Failed to fetch user'); return res.json(); }"
```

Execute only when you explicitly want to spend Morph usage:

```powershell
python -m opcoding morph . apply-snippet `
  --instruction "Add error handling" `
  --code "..." `
  --update "..." `
  --execute `
  --confirm-spend
```

## Policy

- Never store `MORPH_API_KEY` in git.
- Never send secret-looking code.
- Never execute without `--execute --confirm-spend`.
- Prefer local patches for simple edits.

Source: [Morph documentation](https://docs.morphllm.com/) and [Morph API reference](https://docs.morphllm.com/api-reference/endpoint/apply).
