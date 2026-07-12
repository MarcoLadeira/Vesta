"""Generate the OPai desktop app icon (reproducible, brand-consistent).

Produces a square ``opai/assets/opai-icon.png`` used by the GUI hosts via
``QApplication.setWindowIcon`` (window, taskbar, Alt-Tab). Regenerate with:

    python scripts/make_app_icon.py

Design: a rounded-square dark card (the same palette as the savings receipt and
web GUI) with the green "OP" monogram in the brand display font (Nunito). Kept
deliberately simple so it stays crisp when Qt scales it down to 16-32 px in the
taskbar. Authoring needs Pillow (the ``terminal-ui`` extra); the committed PNG
is loaded at runtime with no Pillow dependency.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "opai" / "assets" / "fonts" / "Nunito-Variable.ttf"
OUT = ROOT / "opai" / "assets" / "opai-icon.png"

SIZE = 512
RADIUS = 112
BG_TOP = (13, 17, 23)  # #0d1117
BG_BOTTOM = (22, 27, 34)  # #161b22
BORDER = (48, 54, 61)  # #30363d
GREEN = (63, 185, 80)  # #3fb950
FG = (230, 237, 243)  # #e6edf3


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    return mask


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel(
            (0, y),
            tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return grad.resize((size, size))


def _load_font(px: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), px)
    try:  # Nunito is a variable font — pin the heaviest weight for a bold mark.
        font.set_variation_by_axes([900])
    except (AttributeError, OSError, ValueError):
        return font
    return font


def build() -> Path:
    base = _gradient(SIZE, BG_TOP, BG_BOTTOM).convert("RGBA")
    mask = _rounded_mask(SIZE, RADIUS)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)

    draw = ImageDraw.Draw(icon)
    # Inner hairline border, clipped to the rounded card.
    border = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (2, 2, SIZE - 3, SIZE - 3), radius=RADIUS - 2, outline=BORDER, width=3
    )
    icon = Image.alpha_composite(icon, Image.composite(border, icon, mask))
    draw = ImageDraw.Draw(icon)

    # "OP" monogram, centered, green. Measured and centered by bounding box so it
    # is optically centered regardless of font metrics.
    font = _load_font(248)
    text = "OP"
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = (SIZE - tw) / 2 - box[0]
    y = (SIZE - th) / 2 - box[1] - 12
    draw.text((x, y), text, font=font, fill=GREEN)

    # Accent bar under the monogram — a small nod to the brand's green underline.
    bar_w, bar_h = 168, 20
    bx = (SIZE - bar_w) / 2
    by = y + th + 54
    draw.rounded_rectangle((bx, by, bx + bar_w, by + bar_h), radius=bar_h / 2, fill=FG)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT, "PNG")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
