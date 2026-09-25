"""
2D terminal screen state model with character cells, formatting attributes,
cursor tracking, alternate buffers, and buffer operations.

Semantics follow tmux, which the differential tests in
``tests/test_emulator_differential.py`` use as the reference terminal:

* The cursor column may equal ``cols``. That is the "pending wrap" position
  reached by printing into the last column; the next printable character
  wraps, while CR, BS and cursor motion act from the last column.
* LF, VT and FF move down one row and do not return the carriage.
* Every row carries a ``wrapped`` flag meaning "this row soft-wraps into the
  next one", so a logical line split across rows can be reassembled (the
  masking engine depends on this to redact tokens that wrap).
* East Asian wide characters take two cells. The second cell holds an empty
  string. Zero-width combining characters attach to the previous cell.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Pattern, Union, Any
import re
import threading
import unicodedata
from termreel.emulator.colors import RGBColor, DEFAULT_PALETTE, ColorPalette


@dataclass(slots=True)
class CharCell:
    """Represents a single character cell in the 2D terminal grid."""
    char: str = " "
    fg: RGBColor = (0.85, 0.88, 0.96)
    bg: RGBColor = (0.10, 0.10, 0.15)
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    reverse: bool = False
    blink: bool = False
    hidden: bool = False

    @property
    def effective_fg(self) -> RGBColor:
        """Foreground color taking reverse video into account."""
        return self.bg if self.reverse else self.fg

    @property
    def effective_bg(self) -> RGBColor:
        """Background color taking reverse video into account."""
        return self.fg if self.reverse else self.bg

    def copy(self) -> "CharCell":
        # Positional in field order: this runs for every cell of every
        # snapshot (one per rendered frame), and keyword arguments cost
        # about twice as much.
        return CharCell(
            self.char, self.fg, self.bg, self.bold, self.dim, self.italic,
            self.underline, self.strikethrough, self.reverse, self.blink,
            self.hidden,
        )


class Row(list):
    """
    One grid row: a list of CharCell plus a soft-wrap flag.

    ``wrapped`` is True when the text on this row continues on the next row
    because it ran past the right margin (not because of an explicit newline).
    Keeping the flag on the row object means it moves with the row through
    scrolling, line insertion and deletion without separate bookkeeping.
    """

    __slots__ = ("wrapped", "_unmasked_cells")

    def __init__(self, cells=(), wrapped: bool = False):
        super().__init__(cells)
        self.wrapped = wrapped
        self._unmasked_cells = None

    def __setitem__(self, index, value):
        self._unmasked_cells = None
        super().__setitem__(index, value)


@dataclass(slots=True)
class Cursor:
    """Tracks cursor position and state."""
    row: int = 0
    col: int = 0
    visible: bool = True
    saved_row: int = 0
    saved_col: int = 0


# DEC Special Graphics (ESC ( 0) -> Unicode box drawing.
DEC_SPECIAL_GRAPHICS: Dict[str, str] = {
    "`": "◆", "a": "▒", "b": "␉", "c": "␌", "d": "␍", "e": "␊", "f": "°", "g": "±",
    "h": "␤", "i": "␋", "j": "┘", "k": "┐", "l": "┌", "m": "└", "n": "┼", "o": "⎺",
    "p": "⎻", "q": "─", "r": "⎼", "s": "⎽", "t": "├", "u": "┤", "v": "┴", "w": "┬",
    "x": "│", "y": "≤", "z": "≥", "{": "π", "|": "≠", "}": "£", "~": "·",
}

ZERO_WIDTH_JOINER = "\u200d"


def char_width(ch: str) -> int:
    """
    Display width of one code point: 0 (combining), 1, or 2 (wide).

    Uses the Unicode East Asian Width property, which is what tmux and most
    terminals' wcwidth tables are derived from.
    """
    if not ch:
        return 0
    cp = ord(ch)
    if cp < 0x7F:
        return 1 if cp >= 0x20 else 0
    if ch == ZERO_WIDTH_JOINER or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return 0
    if unicodedata.combining(ch):
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


class TerminalState:
    """
    Complete 2D terminal grid maintaining ANSI character state,
    cursor coordinates, alternate screen buffers, and scrollback.
    Thread-safe via re-entrant lock.
    """

    def __init__(
        self,
        rows: int = 30,
        cols: int = 100,
        default_fg: RGBColor = (0.85, 0.88, 0.96),
        default_bg: RGBColor = (0.10, 0.10, 0.15),
        palette: Optional[ColorPalette] = None,
        max_scrollback: int = 1000,
    ):
        self._lock = threading.RLock()
        self.rows = max(1, rows)
        self.cols = max(1, cols)
        self.default_fg = default_fg
        self.default_bg = default_bg
        self.palette = palette or DEFAULT_PALETTE
        self.max_scrollback = max_scrollback

        self.cursor = Cursor(row=0, col=0, visible=True)
        self.primary_grid: List[Row] = self._create_empty_grid()
        self.alt_grid: List[Row] = self._create_empty_grid()
        self.in_alt_buffer = False

        self.scrollback: List[List[CharCell]] = []
        # Monotonic count of rows scrolled off the top of the primary screen
        # (same meaning as tmux's history growth). Row r on screen is absolute
        # line lines_scrolled + r, so a prompt printed at the bottom row after
        # a scroll is distinguishable from the one before it.
        self.lines_scrolled = 0
        self.top_margin = 0
        self.bottom_margin = self.rows - 1

        # Current text formatting attributes
        self.current_fg = default_fg
        self.current_bg = default_bg
        self.current_bold = False
        self.current_dim = False
        self.current_italic = False
        self.current_underline = False
        self.current_strikethrough = False
        self.current_reverse = False
        self.current_blink = False
        self.current_hidden = False

        # Modes
        self.autowrap = True            # DECAWM (?7)
        self.origin_mode = False        # DECOM (?6)
        self.insert_mode = False        # IRM (4)
        self.bracketed_paste = False    # ?2004
        self.app_cursor_keys = False    # DECCKM (?1)

        # Character sets: G0/G1 designations and which one is shifted in.
        self.charsets: List[str] = ["B", "B"]
        self.active_charset = 0

        # Last printed graphic character, for REP (CSI b).
        self.last_printed: Optional[str] = None

        # Tab stops
        self.tab_width = 8
        self.tab_stops = set(range(0, self.cols, self.tab_width))

        self._saved_state: Optional[Dict[str, Any]] = None
        self._alt_saved_cursor: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ grid

    @property
    def grid(self) -> List[Row]:
        """Active screen grid (primary or alternate)."""
        return self.alt_grid if self.in_alt_buffer else self.primary_grid

    @grid.setter
    def grid(self, val: List[List[CharCell]]):
        val = [r if isinstance(r, Row) else Row(r) for r in val]
        if self.in_alt_buffer:
            self.alt_grid = val
        else:
            self.primary_grid = val

    @property
    def cursor_row(self) -> int:
        return self.cursor.row

    @cursor_row.setter
    def cursor_row(self, val: int):
        self.cursor.row = max(0, min(self.rows - 1, val))

    @property
    def cursor_col(self) -> int:
        return self.cursor.col

    @cursor_col.setter
    def cursor_col(self, val: int):
        self.cursor.col = max(0, min(self.cols - 1, val))

    @property
    def cursor_visible(self) -> bool:
        return self.cursor.visible

    @cursor_visible.setter
    def cursor_visible(self, val: bool):
        self.cursor.visible = val

    def display_cursor_col(self) -> int:
        """Cursor column as tmux reports it (may equal ``cols`` when a wrap is pending)."""
        return max(0, min(self.cols, self.cursor.col))

    def _blank(self, bg: Optional[RGBColor] = None) -> CharCell:
        return CharCell(char=" ", fg=self.default_fg, bg=self.default_bg if bg is None else bg)

    def _create_empty_grid(self) -> List[Row]:
        return [self._create_empty_row() for _ in range(self.rows)]

    def _create_empty_row(self, bg: Optional[RGBColor] = None) -> Row:
        return Row(self._blank(bg) for _ in range(self.cols))

    def _erase_bg(self) -> RGBColor:
        # Background colour erase: erased cells take the current background,
        # but never reverse-video's swapped colour.
        return self.current_bg

    def reset_attributes(self):
        """Reset text styling to defaults."""
        self.current_fg = self.default_fg
        self.current_bg = self.default_bg
        self.current_bold = False
        self.current_dim = False
        self.current_italic = False
        self.current_underline = False
        self.current_strikethrough = False
        self.current_reverse = False
        self.current_blink = False
        self.current_hidden = False

    def clear(self):
        """Clear the active screen buffer and reset cursor."""
        self.grid = self._create_empty_grid()
        self.cursor.row = 0
        self.cursor.col = 0

    def clear_all(self):
        """Clear active grid, reset attributes, and reset margins."""
        self.clear()
        self.reset_attributes()
        self.top_margin = 0
        self.bottom_margin = self.rows - 1

    def reset(self):
        """RIS: full reset to initial state (keeps scrollback)."""
        self.in_alt_buffer = False
        self.primary_grid = self._create_empty_grid()
        self.alt_grid = self._create_empty_grid()
        self.cursor = Cursor(row=0, col=0, visible=True)
        self.reset_attributes()
        self.top_margin = 0
        self.bottom_margin = self.rows - 1
        self.autowrap = True
        self.origin_mode = False
        self.insert_mode = False
        self.bracketed_paste = False
        self.app_cursor_keys = False
        self.charsets = ["B", "B"]
        self.active_charset = 0
        self.last_printed = None
        self.tab_stops = set(range(0, self.cols, self.tab_width))
        self._saved_state = None

    def soft_reset(self):
        """DECSTR (CSI ! p)."""
        self.cursor.visible = True
        self.insert_mode = False
        self.origin_mode = False
        self.autowrap = True
        self.top_margin = 0
        self.bottom_margin = self.rows - 1
        self.reset_attributes()
        self.charsets = ["B", "B"]
        self.active_charset = 0
        self._saved_state = None

    def resize(self, new_rows: int, new_cols: int):
        """Resize terminal state grid dynamically."""
        new_rows = max(1, new_rows)
        new_cols = max(1, new_cols)
        if new_rows == self.rows and new_cols == self.cols:
            return

        def _resize_grid(old_grid: List[List[CharCell]]) -> List[Row]:
            new_g: List[Row] = []
            for r in range(new_rows):
                if r < len(old_grid):
                    old_row = old_grid[r]
                    if len(old_row) >= new_cols:
                        new_row = Row(cell.copy() for cell in old_row[:new_cols])
                    else:
                        new_row = Row([cell.copy() for cell in old_row] + [
                            CharCell(fg=self.default_fg, bg=self.default_bg)
                            for _ in range(new_cols - len(old_row))
                        ])
                    new_row.wrapped = bool(getattr(old_row, "wrapped", False)) and len(old_row) == new_cols
                else:
                    new_row = Row(
                        CharCell(fg=self.default_fg, bg=self.default_bg)
                        for _ in range(new_cols)
                    )
                new_g.append(new_row)
            return new_g

        self.primary_grid = _resize_grid(self.primary_grid)
        self.alt_grid = _resize_grid(self.alt_grid)
        self.rows = new_rows
        self.cols = new_cols
        self.top_margin = 0
        self.bottom_margin = self.rows - 1
        self.cursor.row = min(self.cursor.row, self.rows - 1)
        self.cursor.col = min(self.cursor.col, self.cols - 1)
        self.tab_stops = {c for c in self.tab_stops if c < new_cols} | set(
            range(0, new_cols, self.tab_width)
        )

    # --------------------------------------------------------------- writing

    def _new_cell(self, char: str) -> CharCell:
        return CharCell(
            char=char,
            fg=self.current_fg,
            bg=self.current_bg,
            bold=self.current_bold,
            dim=self.current_dim,
            italic=self.current_italic,
            underline=self.current_underline,
            strikethrough=self.current_strikethrough,
            reverse=self.current_reverse,
            blink=self.current_blink,
            hidden=self.current_hidden,
        )

    def _clear_wide_fragments(self, row: Row, start: int, end: int) -> None:
        """Blank the orphaned half of any wide character overlapping [start, end)."""
        cols = self.cols
        if 0 < start < cols and row[start].char == "":
            row[start - 1].char = " "
        if 0 <= end < cols and row[end].char == "" and end > 0:
            row[end].char = " "

    def print_char(self, ch: str) -> None:
        """Print one graphic character at the cursor, honouring width and modes."""
        if self.charsets[self.active_charset] == "0":
            ch = DEC_SPECIAL_GRAPHICS.get(ch, ch)

        width = char_width(ch)
        cols = self.cols
        cur = self.cursor

        if width == 0:
            self._attach_combining(ch)
            return

        # A character following a zero-width joiner joins the previous cluster.
        if (
            self.last_printed is not None
            and self.last_printed.endswith(ZERO_WIDTH_JOINER)
            and (width == 2 or unicodedata.category(ch) == "So")
        ):
            if self._attach_combining(ch):
                return

        if width > cols:
            return

        if cur.col + width > cols:
            if self.autowrap:
                row = self.grid[cur.row]
                if width == 2 and cur.col == cols - 1:
                    # A wide character that does not fit leaves the last
                    # column empty (not a space) and wraps.
                    self._clear_wide_fragments(row, cols - 1, cols)
                    row[cols - 1] = self._new_cell("")
                    row[cols - 1].char = ""
                row.wrapped = True
                cur.col = 0
                self._index()
            else:
                # tmux drops a character that does not fit when autowrap is
                # off rather than moving it left.
                if cur.col + width > cols and width > 1:
                    return
                cur.col = cols - width

        row = self.grid[cur.row]
        col = cur.col

        if self.insert_mode:
            for _ in range(width):
                row.pop()
                row.insert(col, self._blank(self._erase_bg()))
            if row[cols - 1].char != "" and cols >= 2 and char_width(row[cols - 1].char[:1] or " ") == 2:
                row[cols - 1].char = " "

        self._clear_wide_fragments(row, col, col + width)
        row[col] = self._new_cell(ch)
        if width == 2:
            filler = self._new_cell("")
            row[col + 1] = filler
        cur.col = col + width
        if not self.autowrap and cur.col >= cols:
            cur.col = cols - 1
        self.last_printed = ch

    def _attach_combining(self, ch: str) -> bool:
        """Append a zero-width character to the cell before the cursor."""
        row = self.grid[self.cursor.row]
        col = min(self.cursor.col, self.cols) - 1
        while col >= 0 and row[col].char == "":
            col -= 1
        if col < 0:
            return False
        row[col].char += ch
        if self.last_printed is not None:
            self.last_printed += ch
        else:
            self.last_printed = row[col].char
        return True

    def write_char(self, char: str):
        """
        Write one character, interpreting the common C0 controls.

        Kept for direct callers. The ANSI parser dispatches controls itself and
        calls ``print_char`` for graphic characters.
        """
        if char == "\r":
            self.carriage_return()
        elif char in ("\n", "\x0b", "\x0c"):
            self.line_feed()
        elif char == "\b":
            self.backspace()
        elif char == "\t":
            self.tab()
        elif char == "\a":
            return
        elif len(char) == 1 and (ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0):
            return
        else:
            for ch in char:
                self.print_char(ch)

    def carriage_return(self):
        self.cursor.col = 0

    def backspace(self):
        cur = self.cursor
        if cur.col == 0:
            if cur.row > 0 and getattr(self.grid[cur.row - 1], "wrapped", False):
                cur.row -= 1
                cur.col = self.cols - 1
            return
        cur.col = min(cur.col, self.cols) - 1

    def tab(self, count: int = 1):
        cur = self.cursor
        for _ in range(max(1, count)):
            if cur.col >= self.cols - 1:
                return
            nxt = cur.col + 1
            while nxt < self.cols - 1 and nxt not in self.tab_stops:
                nxt += 1
            cur.col = nxt

    def back_tab(self, count: int = 1):
        cur = self.cursor
        cur.col = min(cur.col, self.cols)
        for _ in range(max(1, count)):
            if cur.col <= 0:
                return
            cur.col -= 1
            while cur.col > 0 and cur.col not in self.tab_stops:
                cur.col -= 1

    def set_tab_stop(self):
        if self.cursor.col < self.cols:
            self.tab_stops.add(self.cursor.col)

    def clear_tab_stop(self, mode: int = 0):
        if mode == 0:
            self.tab_stops.discard(self.cursor.col)
        elif mode == 3:
            self.tab_stops.clear()

    def _index(self):
        """Move down one row within the scroll region, scrolling at its bottom."""
        if self.cursor.row == self.bottom_margin:
            self.scroll_up(1)
        elif self.cursor.row < self.rows - 1:
            self.cursor.row += 1

    def line_feed(self):
        """LF/VT/FF: move down one row, scrolling at the bottom margin. Column is kept."""
        row = self.grid[self.cursor.row]
        if isinstance(row, Row):
            row.wrapped = False
        self._index()

    def reverse_index(self):
        if self.cursor.row == self.top_margin:
            self.scroll_down(1)
        elif self.cursor.row > 0:
            self.cursor.row -= 1

    def next_line(self):
        self.carriage_return()
        self.line_feed()

    def scroll_up(self, n: int = 1):
        """Scroll text up in the scrolling region (top_margin..bottom_margin)."""
        n = max(1, n)
        grid = self.grid
        bg = self._erase_bg()
        for _ in range(min(n, self.bottom_margin - self.top_margin + 1)):
            if not self.in_alt_buffer and self.top_margin == 0:
                # Save scrolled off row to scrollback
                self.lines_scrolled += 1
                self.scrollback.append([c.copy() for c in grid[0]])
                if len(self.scrollback) > self.max_scrollback:
                    self.scrollback.pop(0)

            for r in range(self.top_margin, self.bottom_margin):
                grid[r] = grid[r + 1]
            grid[self.bottom_margin] = self._create_empty_row(bg)

    def scroll_down(self, n: int = 1):
        """Scroll text down in the scrolling region (top_margin..bottom_margin)."""
        n = max(1, n)
        grid = self.grid
        bg = self._erase_bg()
        for _ in range(min(n, self.bottom_margin - self.top_margin + 1)):
            for r in range(self.bottom_margin, self.top_margin, -1):
                grid[r] = grid[r - 1]
            grid[self.top_margin] = self._create_empty_row(bg)
        if self.top_margin > 0:
            grid[self.top_margin - 1].wrapped = False
        grid[self.bottom_margin].wrapped = False

    def _line_op_bottom(self) -> int:
        # tmux: inside the scroll region, IL/DL stop at the bottom margin;
        # outside it they act on everything from the cursor to the last row.
        if self.top_margin <= self.cursor.row <= self.bottom_margin:
            return self.bottom_margin
        return self.rows - 1

    def insert_lines(self, n: int = 1):
        """Insert n blank lines at cursor row."""
        bottom = self._line_op_bottom()
        grid = self.grid
        bg = self._erase_bg()
        for _ in range(min(max(1, n), bottom - self.cursor.row + 1)):
            for r in range(bottom, self.cursor.row, -1):
                grid[r] = grid[r - 1]
            grid[self.cursor.row] = self._create_empty_row(bg)

    def delete_lines(self, n: int = 1):
        """Delete n lines at cursor row."""
        bottom = self._line_op_bottom()
        grid = self.grid
        bg = self._erase_bg()
        for _ in range(min(max(1, n), bottom - self.cursor.row + 1)):
            for r in range(self.cursor.row, bottom):
                grid[r] = grid[r + 1]
            grid[bottom] = self._create_empty_row(bg)

    def insert_chars(self, n: int = 1):
        """Insert n spaces at cursor column, shifting existing chars right."""
        c = self.cursor.col
        if c >= self.cols:
            return
        row = self.grid[self.cursor.row]
        self._clear_wide_fragments(row, c, c)
        bg = self._erase_bg()
        for _ in range(min(n, self.cols - c)):
            row.pop()
            row.insert(c, self._blank(bg))
        if row[self.cols - 1].char and char_width(row[self.cols - 1].char[0]) == 2:
            row[self.cols - 1].char = " "

    def delete_chars(self, n: int = 1):
        """Delete n characters at cursor column, shifting chars left."""
        c = self.cursor.col
        if c >= self.cols:
            return
        row = self.grid[self.cursor.row]
        n = min(n, self.cols - c)
        self._clear_wide_fragments(row, c, c + n)
        bg = self._erase_bg()
        for _ in range(n):
            row.pop(c)
            row.append(self._blank(bg))

    def erase_chars(self, n: int = 1):
        """Erase n characters starting at cursor column without shifting."""
        c = self.cursor.col
        if c >= self.cols:
            return
        row = self.grid[self.cursor.row]
        end = min(self.cols, c + max(1, n))
        self._clear_wide_fragments(row, c, end)
        bg = self._erase_bg()
        for col in range(c, end):
            row[col] = self._blank(bg)

    def erase_in_display(self, mode: int = 0):
        """
        Erase display (CSI J). The cursor does not move.
        - 0: cursor to end of screen
        - 1: start of screen to cursor
        - 2: entire screen
        - 3: scrollback only
        """
        bg = self._erase_bg()
        if mode == 0:
            self.erase_in_line(0)
            for r in range(self.cursor.row + 1, self.rows):
                self.grid[r] = self._create_empty_row(bg)
        elif mode == 1:
            self.erase_in_line(1)
            for r in range(0, self.cursor.row):
                self.grid[r] = self._create_empty_row(bg)
        elif mode == 2:
            grid = self.grid
            for r in range(self.rows):
                grid[r] = self._create_empty_row(bg)
        elif mode == 3:
            self.scrollback.clear()

    def erase_in_line(self, mode: int = 0):
        """
        Erase line (CSI K):
        - 0: cursor to end of line
        - 1: start of line to cursor
        - 2: entire line
        """
        r = self.cursor.row
        row = self.grid[r]
        bg = self._erase_bg()
        col = min(self.cursor.col, self.cols)
        if mode == 0:
            self._clear_wide_fragments(row, col, self.cols)
            for c in range(col, self.cols):
                row[c] = self._blank(bg)
            row.wrapped = False
        elif mode == 1:
            end = min(col + 1, self.cols)
            self._clear_wide_fragments(row, 0, end)
            for c in range(0, end):
                row[c] = self._blank(bg)
        elif mode == 2:
            self.grid[r] = self._create_empty_row(bg)

    def fill_with_e(self):
        """DECALN (ESC # 8): fill the screen with 'E', reset margins, home cursor."""
        grid = self.grid
        for r in range(self.rows):
            grid[r] = Row(CharCell(char="E", fg=self.default_fg, bg=self.default_bg) for _ in range(self.cols))
        self.top_margin = 0
        self.bottom_margin = self.rows - 1
        self.cursor.row = 0
        self.cursor.col = 0

    # -------------------------------------------------------- cursor motion

    def move_cursor(self, row: Optional[int] = None, col: Optional[int] = None) -> None:
        """Absolute move with origin-mode awareness (0-based, relative to region in DECOM)."""
        if row is not None:
            if self.origin_mode:
                row = max(self.top_margin, min(self.bottom_margin, row + self.top_margin))
            else:
                row = max(0, min(self.rows - 1, row))
            self.cursor.row = row
        if col is not None:
            self.cursor.col = max(0, min(self.cols - 1, col))

    def cursor_up(self, n: int = 1):
        cur = self.cursor
        top = self.top_margin if cur.row >= self.top_margin else 0
        cur.row = max(top, cur.row - max(1, n))
        cur.col = min(cur.col, self.cols - 1)

    def cursor_down(self, n: int = 1):
        cur = self.cursor
        bottom = self.bottom_margin if cur.row <= self.bottom_margin else self.rows - 1
        cur.row = min(bottom, cur.row + max(1, n))
        cur.col = min(cur.col, self.cols - 1)

    def cursor_forward(self, n: int = 1):
        cur = self.cursor
        cur.col = min(self.cols - 1, min(cur.col, self.cols - 1) + max(1, n))

    def cursor_back(self, n: int = 1):
        cur = self.cursor
        cur.col = max(0, min(cur.col, self.cols - 1) - max(1, n))

    def set_margins(self, top: int, bottom: int) -> None:
        """DECSTBM with 0-based inclusive rows. Invalid regions are ignored."""
        top = max(0, min(self.rows - 1, top))
        bottom = max(0, min(self.rows - 1, bottom))
        if top >= bottom:
            return
        self.top_margin = top
        self.bottom_margin = bottom
        # tmux homes to the absolute top-left, even with origin mode on.
        self.cursor.row = 0
        self.cursor.col = 0

    def save_cursor(self):
        """Save cursor position (and, like DECSC, rendition and charsets)."""
        self.cursor.saved_row = self.cursor.row
        self.cursor.saved_col = self.cursor.col
        self._saved_state = {
            "row": self.cursor.row,
            "col": self.cursor.col,
            "attrs": (
                self.current_fg, self.current_bg, self.current_bold, self.current_dim,
                self.current_italic, self.current_underline, self.current_strikethrough,
                self.current_reverse, self.current_blink, self.current_hidden,
            ),
            "charsets": list(self.charsets),
            "active_charset": self.active_charset,
            "origin_mode": self.origin_mode,
        }

    def restore_cursor(self):
        """Restore cursor position (and rendition/charsets saved by save_cursor)."""
        saved = self._saved_state
        if saved is None:
            self.cursor.row = max(0, min(self.rows - 1, self.cursor.saved_row))
            self.cursor.col = max(0, min(self.cols - 1, self.cursor.saved_col))
            return
        self.cursor.row = max(0, min(self.rows - 1, saved["row"]))
        self.cursor.col = max(0, min(self.cols - 1, saved["col"]))
        (
            self.current_fg, self.current_bg, self.current_bold, self.current_dim,
            self.current_italic, self.current_underline, self.current_strikethrough,
            self.current_reverse, self.current_blink, self.current_hidden,
        ) = saved["attrs"]
        self.charsets = list(saved["charsets"])
        self.active_charset = saved["active_charset"]
        self.origin_mode = saved["origin_mode"]

    def _attrs_tuple(self):
        return (
            self.current_fg, self.current_bg, self.current_bold, self.current_dim,
            self.current_italic, self.current_underline, self.current_strikethrough,
            self.current_reverse, self.current_blink, self.current_hidden,
        )

    def switch_to_alt_buffer(self, save_cursor: bool = True):
        """
        Switch to alternate screen buffer (smcup / ?1049h).

        The cursor saved here is independent of DECSC (ESC 7): tmux keeps the
        two apart, so ``ESC 7`` issued before or inside the alternate screen
        survives the switch. Charsets are not part of it.
        """
        if not self.in_alt_buffer:
            if save_cursor:
                self._alt_saved_cursor = {
                    "row": self.cursor.row,
                    "col": self.cursor.col,
                    "attrs": self._attrs_tuple(),
                }
            self.in_alt_buffer = True
            self.alt_grid = self._create_empty_grid()

    def switch_to_primary_buffer(self, restore_cursor: bool = True):
        """
        Switch back to primary screen buffer (rmcup / ?1049l).

        Like tmux, the cursor saved by ?1049h is restored whenever ?1049l
        arrives, even if the primary screen is already showing, and the saved
        position is kept for later ?1049l sequences.
        """
        saved = self._alt_saved_cursor
        if restore_cursor and saved:
            self.cursor.row = max(0, min(self.rows - 1, saved["row"]))
            self.cursor.col = max(0, min(self.cols - 1, saved["col"]))
            (
                self.current_fg, self.current_bg, self.current_bold, self.current_dim,
                self.current_italic, self.current_underline, self.current_strikethrough,
                self.current_reverse, self.current_blink, self.current_hidden,
            ) = saved["attrs"]
        if self.in_alt_buffer:
            self.in_alt_buffer = False

    # ───────────────────────────────────────────────────────────
    # Text Extraction & Search Utilities
    # ───────────────────────────────────────────────────────────

    def get_line_text(self, row: int, strip_trailing: bool = True) -> str:
        """Get plain text content of a single row."""
        if 0 <= row < self.rows:
            line = "".join(cell.char for cell in self.grid[row])
            return line.rstrip() if strip_trailing else line
        return ""

    def get_logical_lines(self) -> List[str]:
        """Visible text with soft-wrapped rows joined, like ``tmux capture-pane -J``."""
        with self._lock:
            out: List[str] = []
            parts: List[str] = []
            for r in range(self.rows):
                row = self.grid[r]
                text = "".join(cell.char for cell in row)
                if getattr(row, "wrapped", False) and r < self.rows - 1:
                    # tmux only emits cells that were written; a wrapped row
                    # the cursor merely passed through contributes nothing.
                    parts.append(text if text.strip() else "")
                    continue
                parts.append(text.rstrip())
                out.append("".join(parts).rstrip())
                parts = []
            if parts:
                out.append("".join(parts).rstrip())
            return out

    def get_rendered_text(self, strip_trailing: bool = True) -> str:
        """Get all visible screen lines as a newline-separated string."""
        lines = [self.get_line_text(r, strip_trailing=strip_trailing) for r in range(self.rows)]
        # Trim empty trailing lines from the bottom
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def get_full_text(self, history_lines: int = 500, strip_trailing: bool = True) -> str:
        """Get visible screen lines plus recent scrollback lines as a newline-separated string."""
        with self._lock:
            hist = []
            if self.scrollback and history_lines > 0:
                for row in self.scrollback[-history_lines:]:
                    txt = "".join(c.char for c in row)
                    if strip_trailing:
                        txt = txt.rstrip()
                    hist.append(txt)
            vis = [self.get_line_text(r, strip_trailing=strip_trailing) for r in range(self.rows)]
            all_lines = hist + vis
            while all_lines and not all_lines[-1]:
                all_lines.pop()
            return "\n".join(all_lines)

    def contains(self, substring: str, case_sensitive: bool = False, scope: str = "visible") -> bool:
        """Check if substring exists in terminal screen or scrollback history."""
        text = self.get_full_text() if scope.lower() == "all" else self.get_rendered_text()
        if not case_sensitive:
            return substring.lower() in text.lower()
        return substring in text

    def search_regex(self, pattern: Union[str, Pattern], scope: str = "visible") -> bool:
        """Check if regex pattern matches in terminal screen or scrollback history."""
        text = self.get_full_text() if scope.lower() == "all" else self.get_rendered_text()
        if isinstance(pattern, str):
            return bool(re.search(pattern, text))
        return bool(pattern.search(text))

    def apply_redaction(self, patterns: List[Pattern], mask_char: str = "•"):
        """Mask sensitive content in-place across the grid cells."""
        if not patterns:
            return
        for r in range(self.rows):
            line_str = "".join(c.char for c in self.grid[r])
            for pat in patterns:
                for match in pat.finditer(line_str):
                    start, end = match.span()
                    for c in range(start, min(end, self.cols)):
                        self.grid[r][c].char = mask_char

    def snapshot(self, copy_inactive: bool = True) -> "TerminalState":
        """
        Create a thread-safe point-in-time snapshot copy of the terminal grid and cursor.

        This runs once per rendered frame while holding the lock the PTY
        reader needs, so it avoids building throwaway empty grids.

        With ``copy_inactive=False`` only the visible (active) buffer is
        deep-copied; the hidden buffer's rows are shared with the live state
        and must be treated as read-only and possibly changing. Rendering,
        masking for display and screen serialization only use the active
        buffer.
        """
        with self._lock:
            # A 1x1 state is cheap to build; grids are replaced below.
            snap = TerminalState(
                rows=1,
                cols=1,
                default_fg=self.default_fg,
                default_bg=self.default_bg,
                palette=self.palette,
                max_scrollback=self.max_scrollback,
            )
            snap.rows = self.rows
            snap.cols = self.cols
            snap.tab_width = self.tab_width
            snap.lines_scrolled = self.lines_scrolled
            snap.in_alt_buffer = self.in_alt_buffer
            snap.cursor = Cursor(
                row=self.cursor.row,
                col=self.cursor.col,
                visible=self.cursor.visible,
                saved_row=self.cursor.saved_row,
                saved_col=self.cursor.saved_col,
            )

            def _deep(grid):
                return [Row([cell.copy() for cell in row], wrapped=getattr(row, "wrapped", False))
                        for row in grid]

            if copy_inactive or not self.in_alt_buffer:
                snap.primary_grid = _deep(self.primary_grid)
            else:
                snap.primary_grid = list(self.primary_grid)
            if copy_inactive or self.in_alt_buffer:
                snap.alt_grid = _deep(self.alt_grid)
            else:
                snap.alt_grid = list(self.alt_grid)
            snap.top_margin = self.top_margin
            snap.bottom_margin = self.bottom_margin
            snap.bracketed_paste = self.bracketed_paste
            for name in ("current_fg", "current_bg", "current_bold", "current_dim",
                         "current_italic", "current_underline", "current_strikethrough",
                         "current_reverse", "current_blink", "current_hidden",
                         "autowrap", "origin_mode", "insert_mode", "app_cursor_keys",
                         "active_charset"):
                setattr(snap, name, getattr(self, name))
            snap.charsets = list(self.charsets)
            snap.tab_stops = set(self.tab_stops)
            return snap
