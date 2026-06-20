# OPai Business Strategy

OPai should become the AI coding cost firewall: a local-first control plane that makes Claude, Codex, Copilot, Cursor, Cline, and future coding agents cheaper, safer, and more project-aware.

The immediate job is not to replace every agent. The immediate job is to sit underneath them, reduce waste, make routing visible, enforce local-first policy, and give developers a clear install path that proves value quickly.

## Market Reality

- The AI code tools market is large and still accelerating. Precedence Research estimates the market at USD 10.12B in 2026, growing to USD 91.09B by 2035 at a 27.65% CAGR. It also calls autonomous AI coding agents the fastest-growing tool type. Source: <https://www.precedenceresearch.com/ai-code-tools-market>
- Pricing is moving toward usage. GitHub announced that Copilot moved to usage-based billing on June 1, 2026, while keeping base prices at Pro USD 10/month, Pro+ USD 39/month, Business USD 19/user/month, and Enterprise USD 39/user/month. Source: <https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/>
- Cursor sells the agent/editor experience directly, with Individual at USD 20/month and Teams at USD 40/user/month. Its team value includes MCPs, skills, hooks, cloud agents, usage analytics, privacy mode, and SSO. Source: <https://cursor.com/pricing>
- Cline is an open-source agent runtime across editor, terminal, and SDK surfaces, and claims trust from 8M+ developers. Source: <https://cline.bot/>
- Kilo Code competes on free open-source agent use, BYOK/pay-as-you-go inference, and Teams at USD 15/user/month. Source: <https://kilo.ai/pricing>
- OpenHands competes as an open-source, model-agnostic cloud coding agent platform with local/self-hosted control, enterprise integrations, and a large GitHub community. Source: <https://www.openhands.dev/>
- LiteLLM and similar gateways prove that spend tracking, budget routing, and provider controls are valuable, but they are model infrastructure rather than a coding workflow control plane. Sources: <https://docs.litellm.ai/docs/proxy/cost_tracking> and <https://docs.litellm.ai/docs/proxy/provider_budget_routing>
- Revenue in this category can scale quickly. Lovable publicly reported USD 100M ARR in 2025, and TechCrunch later reported it surpassed USD 500M annualized revenue run rate in June 2026. Cognition reported USD 492M run-rate revenue in May 2026. Sources: <https://lovable.dev/blog/agent>, <https://techcrunch.com/2026/06/09/lovable-says-it-has-hit-500m-in-annualized-revenue-with-1-million-new-projects-a-week/>, and <https://cognition.com/blog/series-d>

## Strategic Diagnosis

Most competitors are selling one of four things:

- Agent/editor: Cursor, Cline, Kilo, OpenHands, Claude Code, Codex, Copilot.
- App builder: Lovable, Replit, Bolt, v0-style tools.
- Model gateway: LiteLLM, Portkey, OpenRouter-style routing.
- Enterprise automation: Devin/Cognition and cloud engineering agents.

OPai's opening is different. OPai can become the layer developers install before they use any of those tools. The wedge is not "we generate better code." The wedge is "we stop your agents from wasting context, credits, and risky permissions."

That gives OPai a sharper position:

```text
Use any AI coding agent. OPai makes it cheaper, safer, and project-aware by default.
```

## Differentiation

- Cross-client control: OPai activates projects for multiple AI clients instead of locking users into one editor or runtime.
- Local-first routing: deterministic tools, cached context, local models, and compact evidence happen before paid cloud models.
- Cost visibility: OPai can expose what context was avoided, which route was chosen, and why cloud escalation was or was not needed.
- Privacy posture: no prompt storage by default and no telemetry requirement for the free/core workflow.
- Agent policy: OPai can enforce confirmation gates for destructive commands, paid tools, cloud calls, deploys, and oversized contexts.
- Guarded workflows: mobile readiness proves a safer automation pattern with bounded queues, evidence packets, path controls, and human proof.
- Registry foundation: OPai already has tools, agents, workflows, prompts, model routing, skills, and MCP metadata that can become a signed marketplace later.

## Customer Strategy

Start with AI power users because they feel the pain first and can validate the product quickly.

- Pain: Claude/Codex/Cursor/Copilot sessions burn credits, pull too much repo context, and repeat local work the machine could have done deterministically.
- Offer: free local install, `opai doctor`, `opai route`, `opai slim`, and a savings report.
- Conversion trigger: "OPai saved you X estimated tokens and avoided Y cloud escalations this week."

Move into small teams once the savings story is visible.

- Pain: each developer uses different agents, rules, prompts, MCP tools, and budgets.
- Offer: shared policy profiles, team budgets, private registries, audit reports, and approved client setup.
- Conversion trigger: team leads can see spend, risk, and workflow consistency across repos without banning AI tools.

Enter enterprise after team controls mature.

- Pain: AI coding adoption creates security, compliance, spend, and provenance risk.
- Offer: self-hosted/private control plane, signed registries, SSO/RBAC, audit exports, evidence packets, and support.
- Conversion trigger: OPai becomes the governance layer that lets enterprises allow more AI coding without losing control.

## Product Packaging

Free core should stay useful and trustworthy:

- One-command install.
- Project activation and repair.
- AI-client instruction and ignore generation.
- Compact local-first routing.
- Basic `opai doctor`, `opai status`, `opai slim`, and `opai route`.
- No telemetry and no prompt storage by default.

OPai Pro should cost about USD 12/month or USD 99/year:

- Savings dashboard and longer local usage history.
- Advanced policy profiles.
- Local model setup helpers.
- Premium workflow packs.
- Better before/after reports for solo users.

OPai Team should cost about USD 19/user/month:

- Shared policies and budgets.
- Team usage reports.
- Private tool/agent/workflow registries.
- Approved MCP/client profiles.
- CI budget and safety gates.

Enterprise should be custom annual pricing:

- Self-hosting or private deployment.
- SSO/RBAC.
- Signed registry enforcement.
- Audit exports.
- Compliance/security documentation.
- Priority support and onboarding.

## Go-To-Market Plan

The first public funnel should be a static install page at `site/index.html`. Its job is to turn interest into installation, not to explain every OPai subsystem.

Primary message:

```text
OPai is the AI coding cost firewall.
Install once. Make Claude, Codex, Copilot, Cursor, and Cline cheaper and safer.
```

Launch channels:

- GitHub README and release notes.
- Hacker News launch post focused on usage-based AI coding costs.
- Reddit posts in Claude Code, Cursor, Copilot, local LLM, and programming communities.
- Short videos showing the same task with and without OPai.
- Founder/dev logs showing OPai savings reports from real projects.
- Direct outreach to teams already using multiple AI coding tools.

Proof assets to build next:

- "Before OPai / After OPai" screenshots.
- Savings report examples.
- One-minute install video.
- Five-minute "Claude Code with OPai" demo.
- Team policy demo.
- Mobile readiness guarded workflow demo.

## 90-Day Execution Plan

Days 1-14:

- Publish the static install page.
- Add the strategy doc to the repo.
- Tighten README links to the install page and business strategy.
- Collect 5 real OPai usage examples from active projects.
- Make `opai doctor` and `opai status` the first-run trust commands.

Days 15-45:

- Ship the first savings report.
- Add route history and estimated token/context reduction.
- Create install troubleshooting docs for Windows, macOS, Linux, Claude, Codex, Copilot, Cursor, and Cline.
- Publish a launch checklist and demo script.
- Recruit 10 early external users.

Days 46-90:

- Package OPai Pro boundaries honestly without blocking the free core.
- Build a local dashboard for savings and policy.
- Add team policy profiles.
- Prepare 2-3 small-team pilots.
- Turn mobile readiness into the first premium workflow case study.

## Metrics

- Install conversion from page visit to successful `opai status`.
- Activation success rate by OS and AI client.
- Weekly active repos.
- Estimated tokens avoided.
- Cloud escalations avoided.
- Savings reports generated.
- AI-client repair success rate.
- Free-to-Pro waitlist signups.
- Team pilot requests.

## Strategic Rule

OPai should not become just another AI coding agent. OPai should become the local-first operating layer that makes every AI coding agent cheaper, safer, and easier to govern.
