# Vesta Product Identity

## What Vesta is

The **cost-aware AI coding cockpit**. One command center — GUI and CLI, one
shared core — over Claude, Codex, Copilot, and local models. Vesta plans,
routes to the cheapest capable model, shows every step while it works, and
hands you an honest receipt.

## What Vesta is not

- Not a Claude/Cursor clone — those are agents; Vesta is the **cockpit around
  your agents**: routing, visibility, protection, proof.
- Not a wrapper that hides which provider ran — providers are first-class,
  visible, and swappable.
- Not a savings-theater dashboard — paid calls are recorded as spend, estimates
  are labeled, receipts are signed (`vesta receipt verify`).

## Why Vesta exists (the manifesto, short)

AI coding tools got powerful and opaque at the same time. They burn tokens you
can't see, run actions you can't inspect, and report "value" you can't verify.
Builders shouldn't have to choose between shipping fast and knowing what it
cost. Vesta exists so you can watch the work, stop it in one keypress, pay only
for what's needed, and prove what you saved. **Every step visible. Every
dollar accounted.**

## The differentiation, concretely

| Competitors' gap | Vesta's answer (already built) |
| --- | --- |
| Opaque token burn | honest ledger, real `total_cost_usd`, budget caps, panic mode |
| Black-box agent actions | activity timeline (GUI) + glyph lines (CLI), real cancel |
| Marketing "savings" | receipts with estimate labels, signed + verifiable |
| GUI-or-CLI, pick one | one pipeline (`handle_gui_message`) behind both; CLI Mirror in the inspector |
| Provider lock-in | Claude + Codex + Copilot + local, one picker |

## Long-term category vision

Own "cost-aware" the way Linear owns "fast": when a developer wonders *"what is
my AI actually doing and what is it costing me?"* — the answer is Vesta.

## Launch and future pricing

Vesta launches fully free. The alpha optimizes for trust, successful tasks, and
measured cost reduction rather than artificial feature gates. Pricing will be
introduced gradually only after real usage identifies future capabilities that
create durable paid value; core safety and honest accounting will never be paid
upgrades.
