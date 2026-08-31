"""
Exhaustive ANSI / VT100 terminal emulation edge case test suite.
Tests ANSIParser and TerminalState against rich control sequences, cursor movements,
erase modes, line/char insertions, DECSTBM scrolling regions, SGR styling/colors,
OSC window titles, split UTF-8 byte chunk streaming, and boundary clamps.
"""

import unittest
from termreel.emulator.state import TerminalState, CharCell
from termreel.emulator.parser import ANSIParser
from termreel.emulator.colors import truecolor_rgb


class TestCursorMovements(unittest.TestCase):
    """Tests for ANSI/VT100 cursor movement sequences."""

    def setUp(self):
        self.state = TerminalState(rows=10, cols=20)
        self.parser = ANSIParser(self.state)

    def test_cursor_home(self):
        """Home (ESC [ H) moves cursor to (0, 0)."""
        self.state.cursor.row = 7
        self.state.cursor.col = 15
        self.parser.feed("\x1b[H")
        self.assertEqual(self.state.cursor.row, 0)
        self.assertEqual(self.state.cursor.col, 0)

    def test_cursor_position_cup(self):
        """CUP (ESC [ row ; col H) sets 1-indexed row and column."""
        self.parser.feed("\x1b[5;12H")
        self.assertEqual(self.state.cursor.row, 4)
        self.assertEqual(self.state.cursor.col, 11)

        # 1;1 returns to (0, 0)
        self.parser.feed("\x1b[1;1H")
        self.assertEqual(self.state.cursor.row, 0)
        self.assertEqual(self.state.cursor.col, 0)

        # Missing params default to 1 (0-indexed 0)
        self.parser.feed("\x1b[4H")
        self.assertEqual(self.state.cursor.row, 3)
        self.assertEqual(self.state.cursor.col, 0)

    def test_cursor_up_cuu(self):
        """CUU (ESC [ count A) moves cursor up with boundary clamping."""
        self.state.cursor.row = 6
        self.parser.feed("\x1b[A")  # default count 1
        self.assertEqual(self.state.cursor.row, 5)

        self.parser.feed("\x1b[3A")
        self.assertEqual(self.state.cursor.row, 2)

        # Clamp at top (row 0)
        self.parser.feed("\x1b[10A")
        self.assertEqual(self.state.cursor.row, 0)

    def test_cursor_down_cud(self):
        """CUD (ESC [ count B) moves cursor down with boundary clamping."""
        self.state.cursor.row = 2
        self.parser.feed("\x1b[B")  # default count 1
        self.assertEqual(self.state.cursor.row, 3)

        self.parser.feed("\x1b[4B")
        self.assertEqual(self.state.cursor.row, 7)

        # Clamp at bottom (rows - 1 = 9)
        self.parser.feed("\x1b[50B")
        self.assertEqual(self.state.cursor.row, 9)

    def test_cursor_forward_cuf(self):
        """CUF (ESC [ count C) moves cursor forward with boundary clamping."""
        self.state.cursor.col = 3
        self.parser.feed("\x1b[C")  # default count 1
        self.assertEqual(self.state.cursor.col, 4)

        self.parser.feed("\x1b[5C")
        self.assertEqual(self.state.cursor.col, 9)

        # Clamp at right margin (cols - 1 = 19)
        self.parser.feed("\x1b[50C")
        self.assertEqual(self.state.cursor.col, 19)

    def test_cursor_backward_cub(self):
        """CUB (ESC [ count D) moves cursor backward with boundary clamping."""
        self.state.cursor.col = 15
        self.parser.feed("\x1b[D")  # default count 1
        self.assertEqual(self.state.cursor.col, 14)

        self.parser.feed("\x1b[6D")
        self.assertEqual(self.state.cursor.col, 8)

        # Clamp at left margin (col 0)
        self.parser.feed("\x1b[50D")
        self.assertEqual(self.state.cursor.col, 0)

    def test_cursor_horizontal_absolute_cha(self):
        """CHA (ESC [ col G) sets column position (1-indexed)."""
        self.parser.feed("\x1b[8G")
        self.assertEqual(self.state.cursor.col, 7)

        self.parser.feed("\x1b[G")  # default 1 -> col 0
        self.assertEqual(self.state.cursor.col, 0)

        self.parser.feed("\x1b[999G")  # clamp to cols - 1 = 19
        self.assertEqual(self.state.cursor.col, 19)

    def test_line_position_absolute_vpa(self):
        """VPA (ESC [ row d) sets row position (1-indexed)."""
        self.parser.feed("\x1b[7d")
        self.assertEqual(self.state.cursor.row, 6)

        self.parser.feed("\x1b[d")  # default 1 -> row 0
        self.assertEqual(self.state.cursor.row, 0)

        self.parser.feed("\x1b[999d")  # clamp to rows - 1 = 9
        self.assertEqual(self.state.cursor.row, 9)


class TestEraseOperations(unittest.TestCase):
    """Tests for Erase in Display (ED) and Erase in Line (EL)."""

    def setUp(self):
        self.state = TerminalState(rows=4, cols=10)
        self.parser = ANSIParser(self.state)

    def _populate_grid(self):
        self.state.clear()
        for r in range(4):
            self.state.cursor.row = r
            self.state.cursor.col = 0
            for c in range(10):
                self.state.write_char(str(c))

    def test_erase_in_line_el0_cursor_to_end(self):
        """EL 0 (ESC [ K or ESC [ 0 K): erase from cursor to end of line."""
        self._populate_grid()
        self.state.cursor.row = 1
        self.state.cursor.col = 4
        self.parser.feed("\x1b[K")
        self.assertEqual(self.state.get_line_text(1), "0123")
        # Line 0 and Line 2 untouched
        self.assertEqual(self.state.get_line_text(0), "0123456789")
        self.assertEqual(self.state.get_line_text(2), "0123456789")

    def test_erase_in_line_el1_start_to_cursor(self):
        """EL 1 (ESC [ 1 K): erase from start of line to cursor inclusive."""
        self._populate_grid()
        self.state.cursor.row = 2
        self.state.cursor.col = 4
        self.parser.feed("\x1b[1K")
        # 0..4 replaced with spaces, 5..9 intact
        self.assertEqual(self.state.get_line_text(2), "     56789")
        self.assertEqual(self.state.get_line_text(0), "0123456789")

    def test_erase_in_line_el2_entire_line(self):
        """EL 2 (ESC [ 2 K): erase entire line."""
        self._populate_grid()
        self.state.cursor.row = 2
        self.state.cursor.col = 5
        self.parser.feed("\x1b[2K")
        self.assertEqual(self.state.get_line_text(2), "")
        self.assertEqual(self.state.get_line_text(1), "0123456789")

    def test_erase_in_display_ed0_cursor_to_end(self):
        """ED 0 (ESC [ J or ESC [ 0 J): erase from cursor to end of screen."""
        self._populate_grid()
        self.state.cursor.row = 1
        self.state.cursor.col = 5
        self.parser.feed("\x1b[J")
        self.assertEqual(self.state.get_line_text(0), "0123456789")
        self.assertEqual(self.state.get_line_text(1), "01234")
        self.assertEqual(self.state.get_line_text(2), "")
        self.assertEqual(self.state.get_line_text(3), "")

    def test_erase_in_display_ed1_start_to_cursor(self):
        """ED 1 (ESC [ 1 J): erase from start of screen to cursor inclusive."""
        self._populate_grid()
        self.state.cursor.row = 2
        self.state.cursor.col = 4
        self.parser.feed("\x1b[1J")
        self.assertEqual(self.state.get_line_text(0), "")
        self.assertEqual(self.state.get_line_text(1), "")
        self.assertEqual(self.state.get_line_text(2), "     56789")
        self.assertEqual(self.state.get_line_text(3), "0123456789")

    def test_erase_in_display_ed2_entire_screen(self):
        """ED 2 (ESC [ 2 J): clear entire screen and reset cursor."""
        self._populate_grid()
        self.parser.feed("\x1b[2J")
        self.assertEqual(self.state.get_rendered_text(), "")
        self.assertEqual(self.state.cursor.row, 0)
        self.assertEqual(self.state.cursor.col, 0)

    def test_erase_in_display_ed3_clear_screen_and_scrollback(self):
        """ED 3 (ESC [ 3 J): clear entire screen and purge scrollback history."""
        self._populate_grid()
        # Trigger scrollback by feeding lines past rows
        for i in range(6):
            self.parser.feed(f"\r\nExtra line {i}")
        self.assertGreater(len(self.state.scrollback), 0)

        self.parser.feed("\x1b[3J")
        self.assertEqual(self.state.get_rendered_text(), "")
        self.assertEqual(len(self.state.scrollback), 0)


class TestLineAndCharacterOperations(unittest.TestCase):
    """Tests for Insert Line (IL), Delete Line (DL), Insert Char (ICH), Delete Char (DCH)."""

    def setUp(self):
        self.state = TerminalState(rows=4, cols=10)
        self.parser = ANSIParser(self.state)

    def test_insert_line_il(self):
        """IL (ESC [ count L): insert blank lines at cursor row, shifting lines down."""
        self.parser.feed("Row 0\r\nRow 1\r\nRow 2\r\nRow 3")
        # Position cursor at Row 1
        self.parser.feed("\x1b[2;1H")
        self.parser.feed("\x1b[L")  # Insert 1 line
        self.assertEqual(self.state.get_line_text(0), "Row 0")
        self.assertEqual(self.state.get_line_text(1), "")  # Newly inserted blank line
        self.assertEqual(self.state.get_line_text(2), "Row 1")
        self.assertEqual(self.state.get_line_text(3), "Row 2")
        # Row 3 pushed off bottom

    def test_delete_line_dl(self):
        """DL (ESC [ count M): delete lines at cursor row, shifting lines up."""
        self.parser.feed("Row 0\r\nRow 1\r\nRow 2\r\nRow 3")
        # Position cursor at Row 1
        self.parser.feed("\x1b[2;1H")
        self.parser.feed("\x1b[M")  # Delete 1 line
        self.assertEqual(self.state.get_line_text(0), "Row 0")
        self.assertEqual(self.state.get_line_text(1), "Row 2")
        self.assertEqual(self.state.get_line_text(2), "Row 3")
        self.assertEqual(self.state.get_line_text(3), "")  # Blank row at bottom

    def test_insert_character_ich(self):
        """ICH (ESC [ count @): insert spaces at cursor col, shifting chars right."""
        self.parser.feed("ABCDEFGHIJ")
        # Position cursor at col 2 ('C')
        self.parser.feed("\x1b[1;3H")
        self.parser.feed("\x1b[2@")  # Insert 2 spaces, shifting right and dropping 'I', 'J'
        self.assertEqual(self.state.get_line_text(0), "AB  CDEFGH")

    def test_delete_character_dch(self):
        """DCH (ESC [ count P): delete chars at cursor col, shifting chars left."""
        self.parser.feed("ABCDEFGH")
        # Position cursor at col 2 ('C')
        self.parser.feed("\x1b[1;3H")
        self.parser.feed("\x1b[2P")  # Delete 2 characters ('C', 'D')
        self.assertEqual(self.state.get_line_text(0), "ABEFGH")


class TestScrollingRegions(unittest.TestCase):
    """Tests for DECSTBM (Set Top and Bottom Margins)."""

    def setUp(self):
        self.state = TerminalState(rows=6, cols=12)
        self.parser = ANSIParser(self.state)

    def test_decstbm_scrolling_region(self):
        """DECSTBM (ESC [ top ; bot r) restricts scrolling to designated rows."""
        # Populate initial content
        self.parser.feed(
            "HEADER\r\n"
            "Item 1\r\n"
            "Item 2\r\n"
            "Item 3\r\n"
            "Item 4\r\n"
            "FOOTER"
        )
        self.assertEqual(self.state.get_line_text(0), "HEADER")
        self.assertEqual(self.state.get_line_text(5), "FOOTER")

        # Set scrolling region to rows 2 through 5 (0-indexed rows 1 through 4)
        self.parser.feed("\x1b[2;5r")
        self.assertEqual(self.state.top_margin, 1)
        self.assertEqual(self.state.bottom_margin, 4)

        # Move cursor to bottom of scrolling region and feed newline
        self.parser.feed("\x1b[5;1H\r\nItem 5")

        # HEADER and FOOTER must be completely preserved outside the scrolling region
        self.assertEqual(self.state.get_line_text(0), "HEADER")
        self.assertEqual(self.state.get_line_text(5), "FOOTER")

        # Scrolling region contents must have shifted up
        self.assertEqual(self.state.get_line_text(1), "Item 2")
        self.assertEqual(self.state.get_line_text(2), "Item 3")
        self.assertEqual(self.state.get_line_text(3), "Item 4")
        self.assertEqual(self.state.get_line_text(4), "Item 5")

        # Reset scrolling region to full screen
        self.parser.feed("\x1b[r")
        self.assertEqual(self.state.top_margin, 0)
        self.assertEqual(self.state.bottom_margin, 5)


class TestColorsAndAttributes(unittest.TestCase):
    """Tests for TrueColor 24-bit RGB, 256 colors, and SGR text styling attributes."""

    def setUp(self):
        self.state = TerminalState(rows=5, cols=30)
        self.parser = ANSIParser(self.state)

    def test_truecolor_rgb(self):
        """TrueColor SGR (ESC [ 38;2;R;G;Bm and ESC [ 48;2;R;G;Bm)."""
        self.parser.feed("\x1b[38;2;120;200;250m\x1b[48;2;10;20;30mRGB\x1b[0m")
        cell = self.state.grid[0][0]
        expected_fg = (120 / 255.0, 200 / 255.0, 250 / 255.0)
        expected_bg = (10 / 255.0, 20 / 255.0, 30 / 255.0)
        self.assertAlmostEqual(cell.fg[0], expected_fg[0], places=3)
        self.assertAlmostEqual(cell.fg[1], expected_fg[1], places=3)
        self.assertAlmostEqual(cell.fg[2], expected_fg[2], places=3)
        self.assertAlmostEqual(cell.bg[0], expected_bg[0], places=3)
        self.assertAlmostEqual(cell.bg[1], expected_bg[1], places=3)
        self.assertAlmostEqual(cell.bg[2], expected_bg[2], places=3)

    def test_ansi_256_colors(self):
        """256-color SGR (ESC [ 38;5;Nm and ESC [ 48;5;Nm)."""
        # Standard color index 1 (Red)
        self.parser.feed("\x1b[38;5;1mStdRed\x1b[0m ")
        cell_red = self.state.grid[0][0]
        self.assertEqual(cell_red.fg, self.state.palette.get_256_color(1))

        # Color cube index 196 (Bright Red)
        self.parser.feed("\x1b[38;5;196mCube196\x1b[0m ")
        cell_cube = self.state.grid[0][7]
        self.assertEqual(cell_cube.fg, self.state.palette.get_256_color(196))

        # Grayscale index 244
        self.parser.feed("\x1b[48;5;244mGrayBG\x1b[0m")
        cell_gray = self.state.grid[0][15]
        self.assertEqual(cell_gray.bg, self.state.palette.get_256_color(244))

    def test_text_attributes_and_resets(self):
        """Bold, dim, italic, underline, reverse, and attribute resets."""
        # Bold (1m), Dim (2m), Italic (3m), Underline (4m), Reverse (7m)
        self.parser.feed(
            "\x1b[1mB\x1b[22m"
            "\x1b[2mD\x1b[22m"
            "\x1b[3mI\x1b[23m"
            "\x1b[4mU\x1b[24m"
            "\x1b[7mR\x1b[27m"
        )
        cell_b = self.state.grid[0][0]
        cell_d = self.state.grid[0][1]
        cell_i = self.state.grid[0][2]
        cell_u = self.state.grid[0][3]
        cell_r = self.state.grid[0][4]

        self.assertTrue(cell_b.bold)
        self.assertFalse(cell_b.dim)

        self.assertTrue(cell_d.dim)
        self.assertFalse(cell_d.bold)

        self.assertTrue(cell_i.italic)
        self.assertTrue(cell_u.underline)
        self.assertTrue(cell_r.reverse)

        # Test effective colors for reverse video
        self.assertEqual(cell_r.effective_fg, cell_r.bg)
        self.assertEqual(cell_r.effective_bg, cell_r.fg)

        # Full reset (0m)
        self.parser.feed("\x1b[1;3;4mStyled\x1b[0mPlain")
        cell_styled = self.state.grid[0][5]
        cell_plain = self.state.grid[0][11]
        self.assertTrue(cell_styled.bold)
        self.assertTrue(cell_styled.italic)
        self.assertTrue(cell_styled.underline)

        self.assertFalse(cell_plain.bold)
        self.assertFalse(cell_plain.italic)
        self.assertFalse(cell_plain.underline)


class TestOSCTitleSequences(unittest.TestCase):
    """Tests for OSC window title sequences."""

    def setUp(self):
        self.state = TerminalState(rows=3, cols=20)
        self.parser = ANSIParser(self.state)

    def test_osc_title_bel_terminated(self):
        """OSC 0 title terminated by BEL character (\x07)."""
        self.parser.feed("\x1b]0;TermReel Bel Title\x07Text")
        self.assertEqual(self.parser.window_title, "TermReel Bel Title")
        # Text after OSC should be written to terminal
        self.assertEqual(self.state.get_line_text(0), "Text")

    def test_osc_title_st_terminated(self):
        """OSC 2 title terminated by String Terminator (ESC \\)."""
        self.parser.feed("\x1b]2;TermReel ST Title\x1b\\Output")
        self.assertEqual(self.parser.window_title, "TermReel ST Title")
        self.assertEqual(self.state.get_line_text(0), "Output")


class TestStreamingUTF8(unittest.TestCase):
    """Tests for multibyte UTF-8 characters split across streaming byte chunks."""

    def setUp(self):
        self.state = TerminalState(rows=3, cols=30)
        self.parser = ANSIParser(self.state)

    def test_four_byte_emoji_split_across_chunks(self):
        """4-byte emoji (🚀: F0 9F 9A 80) split with 3 bytes in packet 1 and 1 byte in packet 2."""
        raw_rocket = "🚀".encode("utf-8")  # b'\xf0\x9f\x9a\x80'
        self.assertEqual(len(raw_rocket), 4)

        # Packet 1: prefix text and first 3 bytes of emoji
        packet1 = b"Launching: " + raw_rocket[:3]
        self.parser.feed(packet1)

        # At this point, the 4th byte is pending; only prefix should be visible
        rendered_mid = self.state.get_rendered_text()
        self.assertEqual(rendered_mid, "Launching:")

        # Packet 2: final byte of emoji and suffix text
        packet2 = raw_rocket[3:] + b" Liftoff!"
        self.parser.feed(packet2)

        rendered_final = self.state.get_rendered_text()
        self.assertEqual(rendered_final, "Launching: 🚀 Liftoff!")
        self.assertTrue(self.state.contains("🚀"))

    def test_three_byte_cjk_split_across_chunks(self):
        """3-byte CJK character (中: E4 B8 AD) split across chunks."""
        raw_cjk = "中文".encode("utf-8")
        self.assertEqual(len(raw_cjk), 6)

        # Send 1st byte
        self.parser.feed(raw_cjk[:1])
        self.assertEqual(self.state.get_rendered_text(), "")

        # Send remaining 5 bytes
        self.parser.feed(raw_cjk[1:])
        self.assertEqual(self.state.get_rendered_text(), "中文")

    def test_stream_byte_by_byte(self):
        """Stream an emoji-rich UTF-8 string one byte at a time."""
        text = "TermReel ✨ 100% 🎯"
        raw_bytes = text.encode("utf-8")

        for byte_val in raw_bytes:
            self.parser.feed(bytes([byte_val]))

        self.assertEqual(self.state.get_rendered_text(), text)


class TestStateBoundaries(unittest.TestCase):
    """Tests for boundary conditions: cursor clamping, tab stops, and wide chars."""

    def setUp(self):
        self.state = TerminalState(rows=3, cols=10)
        self.parser = ANSIParser(self.state)

    def test_cursor_clamp_out_of_bounds(self):
        """Cursor coordinates must never exceed grid boundaries."""
        # Direct property setters
        self.state.cursor_row = 100
        self.assertEqual(self.state.cursor.row, 2)  # clamped to rows - 1 = 2

        self.state.cursor_row = -10
        self.assertEqual(self.state.cursor.row, 0)

        self.state.cursor_col = 50
        self.assertEqual(self.state.cursor.col, 9)  # clamped to cols - 1 = 9

        self.state.cursor_col = -5
        self.assertEqual(self.state.cursor.col, 0)

        # Via ANSI sequences
        self.parser.feed("\x1b[999;999H")
        self.assertEqual(self.state.cursor.row, 2)
        self.assertEqual(self.state.cursor.col, 9)

    def test_tab_stops(self):
        """Tab characters advance cursor to multiples of tab_width (8)."""
        state = TerminalState(rows=2, cols=30)
        state.tab_width = 8

        # From col 0 -> advances to 8
        state.write_char("\t")
        self.assertEqual(state.cursor.col, 8)

        # From col 8 + 3 chars = 11 -> advances to 16
        for ch in "abc":
            state.write_char(ch)
        self.assertEqual(state.cursor.col, 11)
        state.write_char("\t")
        self.assertEqual(state.cursor.col, 16)

        # Check line text contains spaces up to col 8 then 'abc'
        line = state.get_line_text(0)
        self.assertEqual(line, "        abc")

    def test_wide_character_handling_and_auto_wrap(self):
        """CJK and wide characters are stored accurately and wrap at col margin."""
        state = TerminalState(rows=3, cols=6)
        parser = ANSIParser(state)

        # 4 CJK characters: each takes 1 cell in state grid
        parser.feed("北京欢迎")
        self.assertEqual(state.get_line_text(0), "北京欢迎")

        # Adding 3 more chars causes wrap to next line
        parser.feed("你世界")
        self.assertEqual(state.get_line_text(0), "北京欢迎你世")
        self.assertEqual(state.get_line_text(1), "界")

        # Verify search and regex match wide characters
        self.assertTrue(state.contains("欢迎"))
        self.assertTrue(state.search_regex(r"北京\w+"))


if __name__ == "__main__":
    unittest.main()
