# PR #817 review evidence — images only

This branch exists **only to host screenshots** for
[PR #817](https://github.com/MarcoLadeira/OPai/pull/817). It contains no code and is not
intended to be merged into anything.

Open the gallery: [`pr-817/GALLERY.md`](pr-817/GALLERY.md).

Every image under `pr-817/` was captured live by booting the real web UI
(`opai/assets/web/index.html`) through the repository's own e2e mock bridge and deterministic
fixtures, the same harness the committed Playwright gallery uses, at desktop 1440x900,
tablet 768x1024 and phone 390x844.

| Capture | BEFORE | AFTER | Commit on this branch |
| --- | --- | --- | --- |
| 2026-09-10 | `ab4e26e` | `807a067` | `f29750d` |
| **2026-09-13 (current)** | `ab4e26e` | `519069e` | tip of this branch |

Earlier captures stay in this branch's history, so links pinned to `f29750d` keep working.
