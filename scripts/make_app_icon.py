"""Generate the Vesta desktop app icon from the Vesta logo (reproducible).

Produces a square ``vesta/assets/vesta-icon.png`` used by the GUI hosts via
``QApplication.setWindowIcon`` (window, taskbar, Alt-Tab), the web header, and
the packaged Windows icon. Regenerate with:

    python scripts/make_app_icon.py

Design: the Vesta heart itself, trimmed to its drawn edge and centred on a
transparent square with a small margin, so it fills the tile and stays legible
when Windows scales it down to 16-32 px. The logo artwork lives in
``vesta/assets/vesta-mascot.png``; replace that file to rebrand and rerun this.
Authoring needs Pillow (the ``terminal-ui`` extra); the committed PNG is loaded
at runtime with no Pillow dependency.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "vesta" / "assets" / "vesta-mascot.png"
OUT = ROOT / "vesta" / "assets" / "vesta-icon.png"

SIZE = 1024
MARGIN = 0.04  # of the tile, on each side


def build() -> Path:
    logo = Image.open(LOGO).convert("RGBA")
    bbox = logo.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit(f"{LOGO} is fully transparent")
    mark = logo.crop(bbox)

    inner = round(SIZE * (1 - 2 * MARGIN))
    scale = inner / max(mark.size)
    mark = mark.resize(
        (round(mark.width * scale), round(mark.height * scale)), Image.LANCZOS
    )

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT, "PNG", optimize=True)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
