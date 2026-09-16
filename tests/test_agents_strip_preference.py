from pathlib import Path
import tempfile

from vesta.gui_web import save_page_preference
from vestahub.gui_preferences import load_gui_preferences


def test_agents_strip_preference_persists_through_the_native_bridge():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assert load_gui_preferences(root)["show_agents_strip"] is True
        save_page_preference(root, "show_agents_strip", "false")
        assert load_gui_preferences(root)["show_agents_strip"] is False
        save_page_preference(root, "show_agents_strip", "true")
        assert load_gui_preferences(root)["show_agents_strip"] is True
