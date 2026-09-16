import tempfile
import unittest
from io import StringIO
from pathlib import Path

from vesta.integrations import render_statusline
from vesta.terminal_ui import (
    BLUE,
    RESET,
    SOFT_BLUE,
    build_animation_frames,
    build_welcome,
    mascot_asset_path,
    play_animation,
    render_graphic,
)


class VestaTerminalUITests(unittest.TestCase):
    def test_statusline_is_blue_and_right_aligned(self):
        self.assertEqual(
            render_statusline(width=24), f"             {BLUE}Using Vesta{RESET}"
        )

    def test_statusline_can_be_plain_for_machine_consumers(self):
        self.assertEqual(
            render_statusline(width=24, color=False), "             Using Vesta"
        )

    def test_mascot_asset_exists(self):
        path = mascot_asset_path()
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_ascii_graphic_fallback_is_blue(self):
        graphic = render_graphic(mode="ascii")
        self.assertIn(SOFT_BLUE, graphic)
        self.assertIn("Vesta", graphic)

    def test_ansi_graphic_renders_from_image(self):
        graphic = render_graphic(mode="ansi", width=12)
        try:
            import PIL  # noqa: F401
        except ModuleNotFoundError:
            self.assertIn("Vesta", graphic)
        else:
            self.assertIn("\033[48;2;", graphic)
        self.assertGreater(len(graphic.splitlines()), 3)

    def test_inline_graphic_uses_image_file(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"demo-image")
            image = Path(handle.name)
        try:
            graphic = render_graphic(image_path=image, mode="kitty")
            self.assertIn("_G", graphic)
            self.assertIn("ZGVtby1pbWFnZQ", graphic)
        finally:
            image.unlink(missing_ok=True)

    def test_welcome_compact_includes_badge_and_graphic(self):
        welcome = build_welcome(width=40, compact=True, image_mode="ascii")
        self.assertIn(f"{BLUE}Using Vesta{RESET}", welcome)
        self.assertIn("Vesta", welcome)

    def test_animation_frames_move_the_model(self):
        frames = build_animation_frames(
            width=40, compact=True, image_mode="ascii", frames=4
        )

        self.assertEqual(len(frames), 4)
        self.assertNotEqual(frames[0], frames[1])
        self.assertIn(f"{BLUE}Using Vesta{RESET}", frames[0])

    def test_play_animation_uses_terminal_controls(self):
        stream = StringIO()
        play_animation(
            width=40,
            compact=True,
            image_mode="ascii",
            frames=2,
            delay=0,
            stream=stream,
        )

        output = stream.getvalue()
        self.assertIn("\033[?25l", output)
        self.assertIn("\033[?25h", output)
        self.assertIn("Using Vesta", output)


if __name__ == "__main__":
    unittest.main()
