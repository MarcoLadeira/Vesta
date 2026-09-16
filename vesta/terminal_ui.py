from __future__ import annotations

import base64
import os
import shutil
import sys
import time
from importlib import resources
from pathlib import Path
from typing import TextIO

from vesta.release_identity import release_version_text


BLUE = "\033[38;5;39m"
SOFT_BLUE = "\033[38;5;75m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def colorize(text: str, color: str = BLUE, enabled: bool = True) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def visible_len(text: str) -> int:
    length = 0
    in_escape = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\033":
            in_escape = True
        elif in_escape and char.isalpha():
            in_escape = False
        elif not in_escape:
            length += 1
        index += 1
    return length


def mascot_asset_path() -> Path:
    return Path(str(resources.files("vesta").joinpath("assets", "vesta-mascot.png")))


def _asset_bytes(path: Path | None = None) -> bytes:
    return (path or mascot_asset_path()).read_bytes()


def terminal_width(default: int = 80) -> int:
    return shutil.get_terminal_size((default, 20)).columns


def detect_image_mode() -> str:
    forced = os.environ.get("VESTA_IMAGE_MODE") or os.environ.get("VESTA_IMAGE_PROTOCOL")
    if forced:
        return forced.lower()
    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if "kitty" in term:
        return "kitty"
    if "iterm" in term_program or "wezterm" in term_program:
        return "iterm"
    return "ansi"


def render_ascii_mascot(color: bool = True) -> str:
    lines = [
        "        .-=========-.",
        "     .-'   Vesta     '-.",
        "    /   .----------.   \\",
        "   |   |   > _ <    |   |",
        "   |   '------------'   |",
        "    \\      .----.      /",
        "     '.___/|____|\\___.'",
        "        /_|  ||  |_\\",
        "       /__|__||__|__\\",
    ]
    return "\n".join(colorize(line, SOFT_BLUE, color) for line in lines)


def render_kitty_image(image_path: Path | None = None) -> str:
    encoded = base64.b64encode(_asset_bytes(image_path)).decode("ascii")
    return f"\033_Gf=100,a=T,t=d;{encoded}\033\\"


def render_iterm_image(image_path: Path | None = None, width_px: int = 360) -> str:
    data = _asset_bytes(image_path)
    encoded = base64.b64encode(data).decode("ascii")
    name = base64.b64encode(
        (image_path or mascot_asset_path()).name.encode("utf-8")
    ).decode("ascii")
    return f"\033]1337;File=inline=1;name={name};width={width_px}px:{encoded}\a"


def _crop_subject(image: object) -> object:
    try:
        from PIL import Image, ImageChops

        if not isinstance(image, Image.Image):
            return image
        background = Image.new("RGB", image.size, (248, 250, 250))
        diff = ImageChops.difference(image.convert("RGB"), background)
        mask = diff.convert("L").point(lambda value: 255 if value > 18 else 0)
        box = mask.getbbox()
        if not box:
            return image
        left, top, right, bottom = box
        pad_x = max(20, int((right - left) * 0.18))
        pad_y = max(20, int((bottom - top) * 0.12))
        box = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(image.width, right + pad_x),
            min(image.height, bottom + pad_y),
        )
        return image.crop(box)
    except Exception:
        return image


def render_ansi_image(image_path: Path | None = None, width: int = 24) -> str:
    try:
        from PIL import Image

        with Image.open(image_path or mascot_asset_path()) as source:
            image = _crop_subject(source.convert("RGB"))
            aspect = image.height / max(1, image.width)
            target_width = max(8, min(width, 40))
            target_height = max(4, min(24, int(target_width * aspect * 0.55)))
            resample = getattr(Image.Resampling, "LANCZOS", Image.BICUBIC)
            resized = image.resize((target_width, target_height), resample)
            lines = []
            for y in range(resized.height):
                chunks = []
                for x in range(resized.width):
                    r, g, b = resized.getpixel((x, y))
                    chunks.append(f"\033[48;2;{r};{g};{b}m  ")
                lines.append("".join(chunks) + RESET)
            return "\n".join(lines)
    except Exception:
        return render_ascii_mascot(color=True)


def render_graphic(
    image_path: Path | None = None,
    mode: str = "auto",
    color: bool = True,
    width: int = 24,
) -> str:
    selected = detect_image_mode() if mode == "auto" else mode.lower()
    if selected == "kitty":
        return render_kitty_image(image_path)
    if selected in {"iterm", "wezterm"}:
        return render_iterm_image(image_path)
    if selected == "ansi":
        return render_ansi_image(image_path, width=width)
    if selected in {"none", "off", "false"}:
        return ""
    return render_ascii_mascot(color=color)


def render_badge(width: int | None = None, color: bool = True) -> str:
    text = "Using Vesta"
    columns = width or terminal_width()
    padding = max(0, columns - len(text))
    return " " * padding + colorize(text, BLUE, color)


def shift_block(block: str, spaces: int) -> str:
    if spaces <= 0 or not block:
        return block
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in block.splitlines())


def build_welcome(
    width: int | None = None,
    compact: bool = False,
    image_mode: str = "auto",
    color: bool = True,
    motion_offset: int = 0,
) -> str:
    columns = width or terminal_width()
    badge = render_badge(columns, color=color)
    graphic_width = 20 if compact else 26
    graphic = shift_block(
        render_graphic(mode=image_mode, color=color, width=graphic_width), motion_offset
    )
    title = colorize(release_version_text(), BLUE, color)
    subtitle = "local-first AI coding hub"

    if compact:
        parts = [item for item in [graphic, badge] if item]
        return "\n".join(parts)

    rules = colorize("-" * min(columns, 72), DIM, color)
    commands = "vesta doctor  |  vesta scan  |  vesta hub analytics status"
    lines = [badge, graphic, rules, f"{title}  {subtitle}", commands, rules]
    return "\n".join(line for line in lines if line)


def build_animation_frames(
    width: int | None = None,
    compact: bool = False,
    image_mode: str = "auto",
    color: bool = True,
    frames: int = 8,
) -> list[str]:
    count = max(1, frames)
    wave = [0, 1, 2, 3, 2, 1]
    return [
        build_welcome(
            width=width,
            compact=compact,
            image_mode=image_mode,
            color=color,
            motion_offset=wave[index % len(wave)],
        )
        for index in range(count)
    ]


def _line_count(text: str) -> int:
    return max(1, len(text.splitlines()))


def play_animation(
    width: int | None = None,
    compact: bool = False,
    image_mode: str = "auto",
    color: bool = True,
    frames: int = 8,
    delay: float = 0.06,
    stream: TextIO | None = None,
) -> None:
    output = stream or sys.stdout
    rendered = build_animation_frames(
        width=width,
        compact=compact,
        image_mode=image_mode,
        color=color,
        frames=frames,
    )
    previous_lines = 0
    output.write("\033[?25l")
    try:
        for index, frame in enumerate(rendered):
            if index:
                output.write(f"\033[{previous_lines}F\033[J")
            output.write(frame)
            output.write("\n")
            output.flush()
            previous_lines = _line_count(frame) + 1
            if delay > 0 and index < len(rendered) - 1:
                time.sleep(delay)
    finally:
        output.write("\033[?25h")
        output.flush()
