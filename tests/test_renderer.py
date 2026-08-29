import unittest
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.renderer.themes import list_themes, get_theme
from termreel.emulator.state import TerminalState


class TestRenderer(unittest.TestCase):

    def test_renderer_frame_buffer_size(self):
        renderer = CairoTerminalRenderer(width=1280, height=720)
        state = TerminalState(rows=renderer.rows, cols=renderer.cols)
        for ch in "Hello World":
            state.write_char(ch)

        frame = renderer.draw_frame(
            state,
            banner_card={"tag": "Test", "title": "Heading", "desc": "Desc"},
            status_left="Status Test",
        )
        self.assertIsInstance(frame, bytes)
        self.assertEqual(len(frame), 1280 * 720 * 4)

    def test_all_themes_render_without_error(self):
        for theme_name in list_themes():
            renderer = CairoTerminalRenderer(width=640, height=360, theme=theme_name)
            state = TerminalState(rows=renderer.rows, cols=renderer.cols)
            state.write_char("T")
            frame = renderer.draw_frame(state)
            self.assertEqual(len(frame), 640 * 360 * 4)


if __name__ == "__main__":
    unittest.main()
