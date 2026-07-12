# OPai Launch Checklist

Positioning is fixed across every surface: **OPai is the AI coding cost
firewall.** Keep the message identical on GitHub, Product Hunt, Hacker News,
Reddit, and short-form video.

## One-line message (use verbatim)

> OPai is the AI coding cost firewall — it routes the tasks it runs to the
> cheapest safe path across Claude, Codex, Copilot, Gemini, Cursor, and Cline, then
> proves the savings locally and privately.

## Pre-launch (grounded claims only)

- [ ] README leads with the cost-firewall positioning.
- [ ] `site/index.html` is deployed or ready to deploy from Cloudflare Pages.
- [ ] The public site and release notes say the alpha is fully free.
- [ ] No public surface requires a checkout, license, invitation, or private
      access link to install or use alpha functionality.
- [ ] Free access and source-distribution posture are documented separately:
      a distribution decision must not become a payment/access gate.
- [ ] Cloudflare Web Analytics remains optional and does not collect product
      prompts, source, credentials, or private paths.
- [ ] [Quickstart](QUICKSTART.md) reproduces in under 60 seconds.
- [ ] [Before/after proof](PROOF.md) numbers reproduce from the listed commands.
- [ ] [Effectiveness/security audit](EFFECTIVENESS_AND_SECURITY_AUDIT_2026_06_21.md)
      is reviewed and any launch blockers are accepted or fixed.
- [ ] [Install proof checklist](INSTALL_PROOF.md) passes on a clean machine.
- [ ] `python -m unittest discover -s tests` is green.
- [ ] `opai benchmark run --suite max --mode both` is green.
- [ ] `opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks` passes.
- [ ] [Product identity](PRODUCT_IDENTITY.md) and the release notes explain
      that future pricing is post-launch discovery, not an alpha feature gate.
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
2. Install (10–25s, after a verified artifact is published): show the exact
   published command, then `opai doctor` → six clients active. Until then, use
   the release-availability page rather than implying an install command.
3. Route (25–45s): `opai route "fix the failing test"` → cheapest safe tier, read-only.
4. Proof (45–70s): `opai savings --markdown` → dollars saved, cloud calls avoided.
5. Trust (70–90s): `opai guard action "git push"` → denied (fail closed); privacy line.

## Post-launch

- [ ] Collect first-user savings tables (with permission) as case studies.
- [ ] Collect opt-in benchmark proof reports through a privacy-safe support
      channel with no purchase requirement.
- [ ] Review install success, completed-task evidence, recovery friction, and
      support themes before making any pricing decision.
- [ ] Track which clients users activate most (locally reported, opt-in only).
