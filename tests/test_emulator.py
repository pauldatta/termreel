import unittest
import re
from termreel.emulator.state import CharCell, Cursor, TerminalState


class TestEmulatorState(unittest.TestCase):

    def test_char_cell_reverse(self):
        cell = CharCell(char="A", fg=(1.0, 0.0, 0.0), bg=(0.0, 0.0, 1.0), reverse=False)
        self.assertEqual(cell.effective_fg, (1.0, 0.0, 0.0))
        self.assertEqual(cell.effective_bg, (0.0, 0.0, 1.0))

        cell.reverse = True
        self.assertEqual(cell.effective_fg, (0.0, 0.0, 1.0))
        self.assertEqual(cell.effective_bg, (1.0, 0.0, 0.0))

    def test_grid_initialization_and_writing(self):
        state = TerminalState(rows=5, cols=10)
        self.assertEqual(state.rows, 5)
        self.assertEqual(state.cols, 10)
        self.assertEqual(state.cursor.row, 0)
        self.assertEqual(state.cursor.col, 0)

        for ch in "Hello":
            state.write_char(ch)
        self.assertEqual(state.cursor.col, 5)
        self.assertEqual(state.get_line_text(0), "Hello")

    def test_carriage_return_and_line_feed(self):
        state = TerminalState(rows=5, cols=10)
        for ch in "Line 1\r\nLine 2":
            state.write_char(ch)
        self.assertEqual(state.get_line_text(0), "Line 1")
        self.assertEqual(state.get_line_text(1), "Line 2")

    def test_scrolling(self):
        state = TerminalState(rows=3, cols=10)
        for ch in "L1\r\nL2\r\nL3\r\nL4":
            state.write_char(ch)
        # Should have scrolled up by 1 line
        self.assertEqual(state.get_line_text(0), "L2")
        self.assertEqual(state.get_line_text(1), "L3")
        self.assertEqual(state.get_line_text(2), "L4")
        self.assertEqual(len(state.scrollback), 1)

    def test_erase_in_line(self):
        state = TerminalState(rows=3, cols=10)
        for ch in "ABCDEFGHIJ":
            state.write_char(ch)
        state.cursor.col = 5
        # Erase from cursor to end of line
        state.erase_in_line(0)
        self.assertEqual(state.get_line_text(0), "ABCDE")

    def test_alternate_screen_buffer(self):
        state = TerminalState(rows=5, cols=10)
        for ch in "Primary":
            state.write_char(ch)
        self.assertTrue(state.contains("Primary"))

        state.switch_to_alt_buffer()
        self.assertTrue(state.in_alt_buffer)
        self.assertFalse(state.contains("Primary"))

        for ch in "Alternate":
            state.write_char(ch)
        self.assertTrue(state.contains("Alternate"))

        state.switch_to_primary_buffer()
        self.assertFalse(state.in_alt_buffer)
        self.assertTrue(state.contains("Primary"))

    def test_redaction_application(self):
        secret_sample = "SAMPLE_KEY_" + "9876543210"
        state = TerminalState(rows=3, cols=30)
        for ch in f"Secret: {secret_sample}":
            state.write_char(ch)
        self.assertTrue(state.contains(secret_sample))

        pat = re.compile(r"SAMPLE_KEY_[0-9]+")
        state.apply_redaction([pat], mask_char="•")
        rendered = state.get_rendered_text()
        self.assertNotIn(secret_sample, rendered)
        self.assertIn("Secret: ••••••••••••••••••••", rendered)


if __name__ == "__main__":
    unittest.main()
