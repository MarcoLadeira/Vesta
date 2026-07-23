# OPai Quickstart

OPai is the AI coding cost firewall. The alpha launches fully free: there is no
checkout, license, invitation, or private-access requirement for alpha
functionality.

## 1. Check public release availability

There is **no public package or desktop artifact installation command yet**.
When the platform release proof is complete, use only the current verified path
published on [GitHub Releases](https://github.com/MarcoLadeira/OPai/releases).
Until then, a source contributor can install the checkout they already have;
never treat a payment or private checkout link as an installation prerequisite:

```sh
python -m pip install -e .
```

After either a verified release install or the contributor install above,
restart your terminal once. If `op` is not yet on `PATH`, use
`python -m opai ...` for any command below.

## 2. Confirm activation across your AI clients

```sh
opai doctor
```

`readiness` is `ready` when Claude, Codex, Copilot, Gemini, Cursor, and Cline all show
`active`. Anything `broken` or `missing` comes with a concrete repair command:

```sh
opai activate --repair
```

## 3. Route a task (read-only by default)

```sh
opai route "fix the failing test in the auth module"
```

OPai collects local evidence (git diff, tests, project profile) and picks the
cheapest safe tier. It does **not** write anything unless you ask it to.

## 4. Prove the savings

Record routes, then read the report:

```sh
opai route "fix the failing test" --record
opai route "summarize the git diff" --record
opai savings --markdown
```

You'll see estimated AI spend saved, cloud calls avoided, and context tokens
saved — all local, all private.

## 5. Choose a policy profile

```sh
opai policy show                 # current profile + budgets
opai policy set solo-cheap       # or solo-balanced / team-safe / enterprise-strict
```

Profiles control tier ceilings, budget caps, confirmation gates, and fail-closed
behavior. Any cloud/paid model always requires confirmation.

## 6. Keep autonomy bounded

```sh
opai guard list                  # reusable guarded-workflow templates
opai guard action "git push"     # fail-closed: denied without --confirm
```

## Everyday commands

| Command | What it does |
| --- | --- |
| `opai status` | Activation + per-client integration state |
| `opai doctor` | Branded readiness check (read-only) |
| `opai route "<task>"` | Cheapest safe route with local evidence |
| `opai savings` | Estimated spend saved (cost firewall) |
| `opai policy show\|set` | Cost/safety policy profile |
| `opai models eval` | Offline routing scorecard |
| `opai guard list\|check\|action` | Guarded-workflow contract |
| `opai slim --clean` | Strip generated context bloat |
| `opai update` / `opai update --apply` | Check for an update, or fetch/fast-forward/reinstall it |
| `opai uninstall` | Cleanly remove OPai |

Troubleshooting PATH, Superpowers discovery, and aliases is covered in
[INSTALL_PROOF.md](INSTALL_PROOF.md).
