# OPai Launch Checklist

Positioning is fixed across every surface: **OPai is the AI coding cost
firewall.** Keep the message identical on GitHub, Product Hunt, Hacker News,
Reddit, and short-form video.

## One-line message (use verbatim)

> OPai is the AI coding cost firewall — it routes every task to the cheapest
> safe path across Claude, Codex, Copilot, Cursor, and Cline, then proves the
> savings locally and privately.

## Pre-launch (grounded claims only)

- [ ] README leads with the cost-firewall positioning.
- [ ] [Quickstart](QUICKSTART.md) reproduces in under 60 seconds.
- [ ] [Before/after proof](PROOF.md) numbers reproduce from the listed commands.
- [ ] [Install proof checklist](INSTALL_PROOF.md) passes on a clean machine.
- [ ] `python -m unittest discover -s tests` is green.
- [ ] [Editions & pricing](../hub/docs/PRICING_AND_EDITIONS.md) are clear and
      honest (no fake billing).
- [ ] Every public claim maps to a shipped command (no vaporware).

## GitHub

- [ ] Repo description = the one-line message.
- [ ] Topics: `ai`, `cost-control`, `llm`, `developer-tools`, `local-first`,
      `claude`, `codex`, `copilot`, `cursor`, `cline`.
- [ ] Pinned issue: the milestone roadmap.
- [ ] Release notes link Quickstart + Proof.

## Product Hunt

- [ ] Tagline: "The AI coding cost firewall."
- [ ] First comment: the before/after `opai savings` table from PROOF.md.
- [ ] Gallery: `opai doctor`, `opai route`, `opai savings --markdown` output.

## Hacker News (Show HN)

- [ ] Title: `Show HN: OPai – the AI coding cost firewall (local-first, private)`.
- [ ] Body: the problem (blind spend across clients), the wedge (cheapest safe
      route + provable savings), and the privacy stance (hashes, not prompts).
- [ ] Be present for technical questions about routing and the cost model.

## Reddit (r/programming, r/LocalLLaMA, r/ChatGPTCoding)

- [ ] Lead with the reproducible savings table, not adjectives.
- [ ] Emphasize local-first + privacy (no telemetry by default).

## Short-form video (60–90s script)

1. Hook (0–10s): "Your AI coding assistant bills you for `git status`. Here's the firewall."
2. Install (10–25s): one command, then `opai doctor` → five clients active.
3. Route (25–45s): `opai route "fix the failing test"` → cheapest safe tier, read-only.
4. Proof (45–70s): `opai savings --markdown` → dollars saved, cloud calls avoided.
5. Trust (70–90s): `opai guard action "git push"` → denied (fail closed); privacy line.

## Post-launch

- [ ] Collect first-user savings tables (with permission) as case studies.
- [ ] Open a feedback issue for pricing validation before any paid launch.
- [ ] Track which clients users activate most (locally reported, opt-in only).
