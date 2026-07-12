# Publishing and Free-Alpha Release Status

> **Current status (2026-07-12):** No public package, desktop artifact, or GitHub installation command is available. This is a maintainer release guide,
> not a public download page. Do not invent or advertise an install command
> until a verified release artifact is actually published.

OPai is released from this repository, `MarcoLadeira/OPai`. It must not be
released from a parent website repository or a copied working directory.

## Maintainer checks

Run the local release evidence before requesting a production artifact run:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests
python -m opaihub validate
python -m opcoding tools . run bandit
python -m opcoding tools . run pip-audit
python -m opcoding tools . run detect-secrets
python scripts\smoke-install.py
python -m opai benchmark run --suite max --mode both
python -m opai benchmark gate --min-effectiveness-index 95 --require-risk-blocks
```

Follow [the desktop artifact runbook](DESKTOP_ARTIFACT_RELEASE.md) for the
separate signing, credential-free smoke, and attestation gates. A release is
not public merely because source checks pass.

## Contributor installation

Maintainers and contributors who already have a trusted source checkout can
use an editable installation:

```powershell
python -m pip install -e .
op activate --repair --shell-aliases
op status
```

The normal editable install resolves declared core runtime dependencies. Use
`--no-deps` only for an intentional maintainer check that supplies dependencies
separately. Both `op` and `opai` launch OPai; the legacy OPcoding CLI remains
available as `opcoding`.

## Public release handoff

After the protected release workflow has completed the platform signing,
credential-free smoke, and workflow-pinned GitHub attestation gates, a release
operator may publish its verified assets. The public announcement must link to
the release asset and tell users to verify the attestation before extraction.
Until then, say that free-alpha release availability is pending; do not offer a
source, package, or desktop download command as though it were a published
product path.

## Superpowers

OPai activation treats Superpowers as part of OPai when Superpowers exists
under:

```text
~/.codex/superpowers/skills
```

Activation ensures the bridge exists at:

```text
~/.agents/skills/superpowers
```

Restart AI clients after first activation so skills are rediscovered.
