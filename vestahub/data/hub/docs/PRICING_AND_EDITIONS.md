# Vesta Free Public Alpha

Vesta launches as a **Free Public Alpha**. Every implemented capability in this
release is available for `$0`: there is no checkout, licence, invitation,
payment link, or feature gate.

This stable path replaces the earlier pre-launch pricing proposal so existing
links remain useful. Future pricing is post-launch product discovery, not an
alpha requirement. Any future paid offering needs a separate, explicit product
decision; it cannot be activated by a configuration value or hidden behind an
alpha workflow.

## Included in the free alpha

- Local-first routing, context/evidence collection, and a privacy-safe local
  usage ledger.
- Savings reports and local Markdown exports with `vesta savings --export`.
- Client activation, policy profiles, guarded workflows, audit evidence, and
  the implemented team/governance controls already present in this repository.
- Local proof bundles and signatures where the relevant command is available.

Cloud providers remain opt-in and may charge according to their own terms.
That is a provider cost decision, not Vesta access pricing.

## Availability, not entitlement

`vesta edition show` remains as a compatibility diagnostic. It reports the
single Free Public Alpha launch state and distinguishes implemented work from
planned work. The legacy selection subcommand is a harmless no-op: it cannot
unlock, lock, or persist access.

If a capability is listed as planned, Vesta reports that it has not yet been
implemented safely. It never asks the user to upgrade or choose a paid tier.

## Future work

Hosted identity, supported private deployment packaging, private registry
distribution, and retained model-evaluation history are planned work. They are
not paid alpha tiers and are not promised as released functionality.
