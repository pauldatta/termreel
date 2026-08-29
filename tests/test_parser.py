import unittest
from termreel.emulator.state import TerminalState
from termreel.emulator.parser import ANSIParser


class TestANSIParser(unittest.TestCase):

    def setUp(self):
        self.state = TerminalState(rows=10, cols=30)
        self.parser = ANSIParser(self.state)

    def test_sgr_styling(self):
        self.parser.feed("Plain \x1b[1mBold\x1b[22m \x1b[3mItalic\x1b[23m \x1b[4mUnderline\x1b[24m")
        rendered = self.state.get_rendered_text()
        self.assertEqual(rendered, "Plain Bold Italic Underline")

    def test_sgr_colors_256_and_truecolor(self):
        self.parser.feed("\x1b[38;5;196mRed256\x1b[0m \x1b[38;2;100;200;50mCustomRGB\x1b[0m")
        self.assertTrue(self.state.contains("Red256"))
        self.assertTrue(self.state.contains("CustomRGB"))

    def test_cursor_movement_csi(self):
        self.parser.feed("Line 1\r\nLine 2")
        # Move cursor to row 1, col 1 (0-indexed 0, 0)
        self.parser.feed("\x1b[1;1HOver")
        self.assertEqual(self.state.get_line_text(0), "Over 1")

    def test_cursor_horizontal_absolute(self):
        self.parser.feed("AAAA")
        self.parser.feed("\x1b[2GB")  # Move to col 2 (0-indexed col 1)
        self.assertEqual(self.state.get_line_text(0), "ABAA")

    def test_erase_screen_and_lines(self):
        self.parser.feed("Row 1\r\nRow 2\r\nRow 3")
        self.assertTrue(self.state.contains("Row 2"))
        self.parser.feed("\x1b[2J")  # Clear screen
        self.assertEqual(self.state.get_rendered_text(), "")

    def test_alternate_buffer_escapes(self):
        self.parser.feed("Main buffer")
        self.parser.feed("\x1b[?1049hAlt buffer")
        self.assertTrue(self.state.in_alt_buffer)
        self.assertTrue(self.state.contains("Alt buffer"))
        self.parser.feed("\x1b[?1049l")
        self.assertFalse(self.state.in_alt_buffer)
        self.assertTrue(self.state.contains("Main buffer"))

    def test_tmux_pane_loading(self):
        raw_pane = "\x1b[32m[SUCCESS]\x1b[0m Task completed\nSecond line"
        self.parser.feed_tmux_pane(raw_pane)
        self.assertTrue(self.state.contains("[SUCCESS] Task completed"))
        self.assertTrue(self.state.contains("Second line"))


if __name__ == "__main__":
    unittest.main()
