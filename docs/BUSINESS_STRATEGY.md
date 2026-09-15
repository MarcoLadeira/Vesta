# Archived Pre-Free-Launch Business Strategy

Last updated: 2026-06-20

> **Archived on 2026-07-12.** This research and packaging proposal assumes a
> paid/open-core launch. Vesta's alpha is fully free, so this document must not
> be used to introduce a checkout, entitlement, license, private-access
> requirement, or paid feature gate. Retain it only as a post-launch hypothesis
> bank, to revisit after real free-alpha evidence exists.
>
> This document must not be used as current launch policy.

Vesta should become the AI coding cost firewall: the local-first control plane developers install before they use Claude, Codex, Copilot, Cursor, Cline, OpenHands, Kilo, or future coding agents.

The business goal is not to win by being another agent. The business goal is to own the layer underneath agents: activation, policy, context slimming, routing, spend visibility, safety gates, evidence, and team governance.

## Executive Thesis

AI coding tools are moving from autocomplete to autonomous workflows. That creates three new buyer pains:

- Spend becomes variable because agentic sessions consume input, output, cached context, tool calls, and frontier-model credits.
- Risk becomes harder to govern because every developer may use a different agent, MCP server, prompt, plugin, or cloud workspace.
- Proof becomes scarce because teams need to know what the agent saw, what it changed, why it escalated, and whether it followed policy.

Vesta's wedge is to solve those pains without asking users to abandon the agents they already like.

The category to own:

```text
AI coding control plane
```

The market-facing phrase:

```text
The AI coding cost firewall.
```

The product promise:

```text
Use any AI coding agent. Vesta makes it cheaper, safer, and project-aware by default.
```

## What Vesta Is Exactly

Vesta is a local-first operating layer for AI-assisted software development.

It is:

- A CLI and installer that activates projects and AI clients.
- A cross-client policy layer for Claude, Codex, Copilot, and other agents.
- A context firewall that keeps generated caches and irrelevant files out of model prompts.
- A model-routing advisor that starts with deterministic evidence and escalates only when justified.
- A safety layer for destructive commands, paid tools, cloud calls, deployments, and oversized context.
- A registry layer for tools, agents, workflows, skills, MCP servers, budgets, and permissions.
- An evidence layer for guarded workflows such as mobile release readiness.

It is not:

- Not a model.
- Not an IDE.
- Not a replacement for Claude Code, Codex, Cursor, Cline, or Copilot.
- Not a cloud-first agent runtime.
- Not an app-builder like Lovable or Replit.
- Not a generic LLM gateway like LiteLLM.

The simplest analogy:

```text
Vesta is the local policy, routing, and evidence layer for agentic coding.
```

## Market Reality

The AI coding market is already large, fast-growing, and economically intense.

- Precedence Research estimates the AI code tools market at USD 10.12B in 2026, growing to USD 91.09B by 2035 at a 27.65% CAGR. It identifies autonomous AI coding agents as the fastest-growing tool type. Source: <https://www.precedenceresearch.com/ai-code-tools-market>
- GitHub moved Copilot to usage-based billing on June 1, 2026. GitHub AI Credits are based on tokens, including input, output, and cached tokens; 1 credit equals USD 0.01. Source: <https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-organizations-and-enterprises>
- OpenAI moved Codex teams toward pay-as-you-go usage, with Codex-only seats billed on token consumption, and reported more than 2M weekly Codex builders plus 6x growth in Business and Enterprise Codex users since January 2026. Source: <https://openai.com/index/codex-flexible-pricing-for-teams/>
- Claude's individual plans range from Pro at USD 20/month to Max 20x at USD 200/month, which shows that serious AI power users will pay for more capacity. Source: <https://support.claude.com/en/articles/11049762-choose-a-claude-plan>
- Cursor publicly said it crossed USD 1B in annualized revenue and serves millions of developers plus many major engineering organizations. Source: <https://cursor.com/blog/series-d>
- Replit says users from 85% of the Fortune 500 are building with it and that it is on track to hit USD 1B run-rate revenue by the end of 2026. Source: <https://replit.com/blog/replit-raises-400-million-dollars>
- Lovable reported USD 100M ARR in 2025, and TechCrunch reported it surpassed USD 500M annualized revenue run rate in June 2026. Sources: <https://lovable.dev/blog/agent> and <https://techcrunch.com/2026/06/09/lovable-says-it-has-hit-500m-in-annualized-revenue-with-1-million-new-projects-a-week/>
- Cognition reported USD 492M run-rate revenue in May 2026, tied to enterprise usage of Devin. Source: <https://cognition.ai/blog/series-d>

The macro conclusion: the agent layer is huge, but agent usage is becoming expensive enough that control, governance, and evidence become their own product category.

## Competitor Map

| Category | Examples | What They Sell | What Vesta Should Avoid | Vesta Opening |
| --- | --- | --- | --- | --- |
| Agent/editor | Cursor, Claude Code, Codex, Cline, Kilo, Copilot | The coding agent experience itself | Competing head-on as another editor | Make every agent cheaper and safer |
| App builder | Lovable, Replit, Bolt | Prompt-to-app creation and hosting | Becoming a generic app-builder too early | Help app builders and devs avoid costly agent chaos |
| Model gateway | LiteLLM, Portkey, OpenRouter | Provider routing, keys, budgets, spend tracking | Becoming only API infrastructure | Apply routing to real coding workflows and repo context |
| Enterprise agent platform | Devin/Cognition, OpenHands Enterprise, Tabnine | Controlled autonomous engineering workflows | Starting cloud-first before trust is earned | Local-first install, then team governance |
| Code review/security | CodeRabbit, Qodo, Secure Code Warrior, Snyk, Checkmarx | Review, code quality, security governance | Narrowing Vesta to review only | Become the broader pre-agent control layer |

The important insight is that Vesta's best path is not "beat Cursor at Cursor." The best path is "make Cursor, Claude, Codex, Copilot, and Cline easier to govern."

## Where The Money Is

The market has five revenue pools. They are not equal.

### 1. Enterprise Governance And Control

This is the highest-quality revenue. Buyers pay because uncontrolled AI coding creates security, compliance, IP, cost, and audit risk.

Evidence:

- Secure Code Warrior positions Trust Agent: AI as the first control layer for AI-assisted software development, with governance at the point of code creation. Source: <https://www.securecodewarrior.com/product/trust-agent-ai>
- Tabnine's Agentic Platform is USD 59/user/month annually and emphasizes architecture context, organizational standards, MCP tools, and safe production operation. Source: <https://www.tabnine.com/pricing/>
- Qodo Enterprise adds SSO/SAML, audit logs, governance analytics, BYOK, single-tenant SaaS or on-prem, and dedicated support. Source: <https://www.qodo.ai/pricing/>

Vesta implication:

Enterprise Vesta should sell governance, audit, private registries, signed evidence, and self-hosted/team policy. This can become USD 24k-250k+ annual contract value if Vesta proves value in teams first.

### 2. Team-Level Seat Revenue

This is the most reachable near-term revenue. Teams already understand per-seat developer tooling.

Evidence:

- Cursor Teams is USD 40/user/month and includes team billing, team marketplace, cloud agents, usage analytics, privacy mode, and SSO. Source: <https://cursor.com/pricing>
- GitHub Copilot Business and Enterprise are usage-based around organization pools and budgets, with AI credits tied to token consumption. Source: <https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-organizations-and-enterprises>
- Gemini Code Assist charges USD 22.80/user/month for Standard monthly and USD 54/user/month for Enterprise monthly. Source: <https://codeassist.google/products/business>
- Amazon Q Developer Pro is USD 19/user/month, with pooled transformation line allocations and overage pricing. Source: <https://aws.amazon.com/q/developer/pricing/>
- CodeRabbit Pro is USD 24/user/month annually and Pro Plus is USD 48/user/month annually. Source: <https://www.coderabbit.ai/pricing>

Vesta implication:

Team Vesta should start around USD 19/user/month, then add a higher Governance tier around USD 29-39/user/month once audit, dashboards, shared policies, and private registries exist.

### 3. Usage, Credits, And Spend Management

This is huge but dangerous. Usage revenue can scale, but it also creates infrastructure exposure.

Evidence:

- OpenAI Codex-only seats have no rate limits and are billed on token consumption. Source: <https://openai.com/index/codex-flexible-pricing-for-teams/>
- GitHub AI Credits meter token-heavy agent interactions, and long frontier-model sessions cost more because they do more work. Source: <https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-organizations-and-enterprises>
- LiteLLM tracks spend across 100+ LLMs and supports provider budget routing. Sources: <https://docs.litellm.ai/docs/proxy/cost_tracking> and <https://docs.litellm.ai/docs/proxy/provider_budget_routing>

Vesta implication:

Vesta should not start by reselling tokens. It should start by reducing token waste. Later, Vesta can optionally integrate LiteLLM/OpenRouter/provider keys and take revenue from team/enterprise controls, not from encouraging more spend.

### 4. App Builder And Hosting Revenue

This is massive but less natural for Vesta today.

Evidence:

- Lovable and Replit show that app-building platforms can scale to hundreds of millions in annualized revenue when they own the creation workflow, hosting, and user distribution.

Vesta implication:

Do not chase this first. Vesta can later support "one prompt to build, one prompt to ship" workflows, but the immediate wedge is control and cost around existing coding agents.

### 5. Marketplace And Workflow Packs

This can become meaningful after distribution exists.

Vesta implication:

Signed registries, workflow packs, MCP profiles, mobile readiness packs, release preflight packs, and security packs should become the marketplace foundation. Do not launch a marketplace before Vesta has active installs and a trust story.

## How Vesta Stands Out

Vesta should own a position no competitor can easily copy without changing its business model:

```text
Agent-neutral, local-first, cost-aware governance for AI coding.
```

The strongest differentiators:

- Vesta is cross-client. It does not require users to choose one AI vendor.
- Vesta starts local. That makes it safer for developers and more believable for security-conscious teams.
- Vesta reduces spend instead of monetizing spend first.
- Vesta has a real install surface: wrappers, status, project instructions, AI ignore files, skills, and `vesta doctor`.
- Vesta has a registry architecture for tools, agents, workflows, MCP servers, model routing, permissions, and budgets.
- Vesta has a guarded workflow proof point in mobile readiness: two readiness scores, bounded remediation queues, path locks, signed evidence, CI lanes, and no auto-push/upload/submit.

The most important product story:

```text
Before Vesta, each agent sees too much and costs too much.
After Vesta, every agent starts from the same local evidence, budget, and safety policy.
```

## Buyer Segments

### Segment 1: AI Power Users

Profile:

- Solo developers, founders, freelancers, indie hackers, senior engineers, and AI-heavy builders.

Pain:

- They use Claude/Codex/Cursor/Cline heavily and feel usage limits, context bloat, repeated local checks, and credit anxiety.

Offer:

- Free core plus Vesta Pro.

Message:

```text
Stop burning credits on context your machine can collect locally.
```

Target conversion:

- Install page to `vesta status`.
- `vesta slim` and `vesta route` to first savings proof.
- Savings report to Pro.

### Segment 2: Small Engineering Teams

Profile:

- 5-50 developer teams already using multiple AI tools.

Pain:

- Shadow AI, inconsistent rules, unpredictable spend, and no shared policy.

Offer:

- Vesta Team.

Message:

```text
Let every developer use their favorite agent under one team policy.
```

Target conversion:

- Pilot with 3-10 seats.
- Shared policy and team savings report.
- Convert to Team after 30 days.

### Segment 3: Enterprise Engineering And Security

Profile:

- Regulated organizations, platform teams, security leaders, financial services, healthcare, government contractors, and large product orgs.

Pain:

- AI code provenance, model usage, MCP/tool permissions, audit readiness, spend controls, and policy enforcement.

Offer:

- Vesta Enterprise.

Message:

```text
Govern AI coding without banning the tools developers already use.
```

Target conversion:

- Team proof first.
- Enterprise discovery call.
- Self-host/private registry/audit pilot.

## Product Strategy

Vesta should evolve in five layers.

### Layer 1: Vesta Core

Free and open core.

- Installer.
- Project activation.
- AI-client instructions and ignore files.
- `vesta doctor`.
- `vesta status`.
- `vesta slim`.
- `vesta route`.
- Basic model recommendation.
- Local-first default policy.

Success test:

- A new user can install Vesta, activate a project, and understand what Vesta changed in under 10 minutes.

### Layer 2: Vesta Savings

Power-user Pro tier.

- Route history.
- Usage ledger.
- Savings report.
- Before/after context report.
- Local dashboard.
- Advanced policy profiles.
- Local model setup helpers.

Success test:

- A user can see estimated tokens avoided, cloud escalations avoided, and contexts slimmed.

### Layer 3: Vesta Team

Paid team tier.

- Shared policies.
- Pooled budgets.
- Team route analytics.
- Private registries.
- Approved MCP profiles.
- CI policy checks.
- Team install repair report.

Success test:

- A team lead can answer: who used what, which agents followed policy, what spend was avoided, and which repos are safe.

### Layer 4: Vesta Trust

Enterprise tier.

- SSO/RBAC.
- Signed registries.
- Audit exports.
- Self-host/private deployment.
- Signed evidence packets.
- AI model and tool provenance.
- SOC 2-ready controls and security docs.

Success test:

- A security or platform team can allow AI coding at scale with evidence and enforceable defaults.

### Layer 5: Vesta Marketplace

Later-stage ecosystem.

- Signed workflow packs.
- MCP profiles.
- Tool profiles.
- Agent packs.
- Vertical readiness packs.
- Team-shared internal packs.

Success test:

- Vesta becomes the place teams standardize AI coding workflows, not just the tool they install.

## Product Packaging

Pricing hypothesis:

| Tier | Price | Buyer | Included |
| --- | --- | --- | --- |
| Free Core | USD 0 | Solo devs, OSS, early adopters | install, activate, status, doctor, slim, basic route, AI ignores |
| Pro | USD 12/month or USD 99/year | AI power users | savings dashboard, route history, advanced local policy, workflow packs |
| Team | USD 19/user/month | small teams | shared policies, pooled budgets, team reports, private registries |
| Team Governance | USD 29-39/user/month | security-aware teams | audit logs, approved MCP profiles, CI gates, evidence exports |
| Enterprise | custom, starting around USD 24k/year | platform/security orgs | SSO/RBAC, self-host/private, signed registries, support, compliance docs |

Keep the free core strong. Vesta needs trust and distribution before it can charge deeply.

Do not put basic local safety behind a paywall. Charge for history, governance, dashboards, shared policy, audit, private registries, signed evidence, hosted sync, and support.

## Go-To-Market Strategy

### First Positioning

Lead with the painful thing users already feel:

```text
AI coding is getting powerful, but the bill and risk are getting weird.
```

Then offer the simple fix:

```text
Install Vesta once. Make every coding agent start local-first.
```

### First Funnel

The first funnel is already in the repo at `site/index.html`.

It should drive this path:

```text
page visit -> install command -> vesta status -> vesta doctor -> vesta route -> first savings proof
```

The page should not try to explain every feature. It should make one thing unforgettable:

```text
Vesta is the AI coding cost firewall.
```

### Content Engine

Create proof-driven content, not vague AI hype:

- "Claude Code with Vesta: same bug, less context."
- "Cursor with Vesta: stop sending generated caches to your model."
- "Codex with Vesta: local evidence before cloud escalation."
- "Copilot usage-based billing means token discipline matters."
- "What your coding agent saw before it changed your repo."
- "How Vesta blocks risky commands during agentic coding."
- "Mobile release readiness: a guarded AI workflow that does not push, upload, or submit."

### Launch Channels

- GitHub README and releases.
- Hacker News: cost-control story.
- Reddit: Claude Code, Cursor, GitHub Copilot, local LLM, programming, indie hacking.
- YouTube/TikTok/Shorts: before/after sessions.
- LinkedIn: team governance and platform engineering angle.
- Direct outreach: small engineering teams already using multiple coding agents.

## Sales Strategy

### Land

Win solo developers with free install and visible savings.

Required proof:

- `vesta status` works.
- `vesta doctor` is clear.
- `vesta slim` finds context bloat.
- `vesta route` explains local-first decisions.

### Expand

Turn power users into team champions.

Required proof:

- Savings report.
- Team policy profile.
- Shared install repair.
- Before/after demo in a real repo.

### Enterprise

Sell governance after team traction.

Required proof:

- Private registry.
- Audit logs.
- Signed evidence.
- SSO/RBAC plan.
- Security architecture document.
- Case study or pilot metrics.

## Product Roadmap

### 0-30 Days: Proof Of Install And Positioning

- Make `site/index.html` the public install page.
- Link the strategy and install page from README.
- Make install/status/doctor reliable across moved paths.
- Tighten the phrase "AI coding cost firewall" everywhere.
- Create first before/after demo script.
- Publish a public roadmap connected to issues #35-#40.

### 30-60 Days: Savings Report MVP

- Add route history.
- Add usage ledger.
- Add `vesta savings`.
- Show context bloat avoided.
- Show estimated cloud escalations avoided.
- Keep prompt storage off by default.
- Produce five real savings screenshots from local projects.

### 60-90 Days: Team Pilot

- Add policy profiles: `solo-cheap`, `solo-balanced`, `team-safe`, `enterprise-strict`.
- Add team policy file and validation.
- Add local dashboard for savings and policy.
- Add private registry concept.
- Recruit 2-3 team pilots.

### 90-180 Days: Governance

- Add audit export.
- Add signed registry verification.
- Add approved MCP profiles.
- Add CI policy check.
- Add self-host/team docs.
- Package mobile readiness as the first guarded workflow case study.

### 180-365 Days: Enterprise And Marketplace

- Add SSO/RBAC if hosted/team mode exists.
- Add signed internal workflow packs.
- Add marketplace metadata and trust scoring.
- Add compliance/security packet.
- Build partner integrations with LiteLLM, OpenRouter, local model runtimes, and CI providers.

## What To Build First

Priority order:

1. Activation reliability.
2. Savings report.
3. Policy profiles.
4. Team dashboard.
5. Guarded workflow contract.
6. Private registry and audit trail.
7. Enterprise packaging.

Do not build first:

- A new coding agent.
- A cloud IDE.
- A generic app builder.
- A token resale gateway.
- A marketplace before distribution exists.

## Strategic Moats

Vesta can build moats in places that agent vendors have weaker incentives to own:

- Agent neutrality: every vendor wants lock-in; Vesta should support many clients.
- Spend reduction: model vendors benefit from usage; Vesta benefits from trust and savings.
- Local-first trust: users can verify Vesta behavior on disk.
- Policy artifacts: instructions, ignore files, route history, budgets, permissions, evidence.
- Workflow evidence: signed packets and audit bundles for release readiness.
- Registry graph: tools, agents, MCP servers, workflows, skills, models, permissions, and budgets in one system.

## Key Risks

- If Vesta is hard to install, nothing else matters.
- If Vesta cannot show savings, the cost-firewall message becomes weak.
- If Vesta tries to become a full agent too early, it enters the most crowded part of the market.
- If Vesta stores sensitive prompts or telemetry by default, it loses its trust advantage.
- If team features are only docs and not working controls, enterprise buyers will not believe the story.
- If the product is too broad, users will not understand what to do first.

## Metrics

Product metrics:

- Install success rate.
- `vesta status` success rate.
- AI-client activation success rate.
- `vesta slim` context reduced.
- `vesta route` runs per active repo.
- Estimated tokens avoided.
- Cloud escalations avoided.
- Doctor repair success rate.

Business metrics:

- Weekly active repos.
- Install page conversion.
- Free-to-Pro conversion.
- Pro churn.
- Team pilot count.
- Team seats.
- Enterprise discovery calls.
- Annual recurring revenue.

Trust metrics:

- Number of projects with policy profiles.
- Number of guarded workflow runs.
- Number of signed evidence packets.
- Number of denied risky commands.
- Number of audit exports generated.

## CEO Decision

Vesta should commit to this identity:

```text
Vesta is the local-first control plane for AI coding.
```

It should lead publicly with this message:

```text
The AI coding cost firewall.
```

It should monetize in this order:

1. Free core for distribution and trust.
2. Pro savings for AI power users.
3. Team governance for predictable recurring revenue.
4. Enterprise trust for high-value contracts.
5. Marketplace/workflow packs after distribution exists.

The most important next product promise:

```text
Install Vesta, run one command, and see what AI spend and risk it helped you avoid.
```

If Vesta can prove that promise, it becomes more than a utility. It becomes the neutral control layer for a world where every software team uses multiple AI agents.
