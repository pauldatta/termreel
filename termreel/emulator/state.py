"""
2D terminal screen state model with character cells, formatting attributes,
cursor tracking, alternate buffers, and buffer operations.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Pattern, Union, Any
import re
import threading
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
        return CharCell(
            char=self.char,
            fg=self.fg,
            bg=self.bg,
            bold=self.bold,
            dim=self.dim,
            italic=self.italic,
            underline=self.underline,
            strikethrough=self.strikethrough,
            reverse=self.reverse,
            blink=self.blink,
            hidden=self.hidden,
        )


@dataclass(slots=True)
class Cursor:
    """Tracks cursor position and state."""
    row: int = 0
    col: int = 0
    visible: bool = True
    saved_row: int = 0
    saved_col: int = 0



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
        self.primary_grid: List[List[CharCell]] = self._create_empty_grid()
        self.alt_grid: List[List[CharCell]] = self._create_empty_grid()
        self.in_alt_buffer = False

        self.scrollback: List[List[CharCell]] = []
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

        # Tab stop spacing
        self.tab_width = 8

    @property
    def grid(self) -> List[List[CharCell]]:
        """Active screen grid (primary or alternate)."""
        return self.alt_grid if self.in_alt_buffer else self.primary_grid

    @grid.setter
    def grid(self, val: List[List[CharCell]]):
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

    def _create_empty_grid(self) -> List[List[CharCell]]:
        return [
            [
                CharCell(char=" ", fg=self.default_fg, bg=self.default_bg)
                for _ in range(self.cols)
            ]
            for _ in range(self.rows)
        ]

    def _create_empty_row(self) -> List[CharCell]:
        return [
            CharCell(char=" ", fg=self.default_fg, bg=self.default_bg)
            for _ in range(self.cols)
        ]

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

    def resize(self, new_rows: int, new_cols: int):
        """Resize terminal state grid dynamically."""
        new_rows = max(1, new_rows)
        new_cols = max(1, new_cols)
        if new_rows == self.rows and new_cols == self.cols:
            return

        def _resize_grid(old_grid: List[List[CharCell]]) -> List[List[CharCell]]:
            new_g = []
            for r in range(new_rows):
                if r < len(old_grid):
                    old_row = old_grid[r]
                    if len(old_row) >= new_cols:
                        new_row = [cell.copy() for cell in old_row[:new_cols]]
                    else:
                        new_row = [cell.copy() for cell in old_row] + [
                            CharCell(fg=self.default_fg, bg=self.default_bg)
                            for _ in range(new_cols - len(old_row))
                        ]
                else:
                    new_row = [
                        CharCell(fg=self.default_fg, bg=self.default_bg)
                        for _ in range(new_cols)
                    ]
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

    def write_char(self, char: str):
        """Writes a single character to the grid at current cursor position."""
        if char == "\r":
            self.cursor.col = 0
            return
        if char == "\n":
            self.cursor.col = 0
            self.line_feed()
            return
        if char == "\b":
            if self.cursor.col > 0:
                self.cursor.col -= 1
            return
        if char == "\t":
            spaces = self.tab_width - (self.cursor.col % self.tab_width)
            for _ in range(spaces):
                self.write_char(" ")
            return
        if char == "\a":  # Bell
            return

        if self.cursor.col >= self.cols:
            # Auto-wrap to next line
            self.cursor.col = 0
            self.line_feed()

        cell = CharCell(
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
        self.grid[self.cursor.row][self.cursor.col] = cell
        self.cursor.col += 1

    def line_feed(self):
        """Move cursor down one row, scrolling if at bottom margin."""
        if self.cursor.row == self.bottom_margin:
            self.scroll_up(1)
        elif self.cursor.row < self.rows - 1:
            self.cursor.row += 1

    def scroll_up(self, n: int = 1):
        """Scroll text up in the scrolling region (top_margin..bottom_margin)."""
        for _ in range(n):
            if not self.in_alt_buffer and self.top_margin == 0:
                # Save scrolled off row to scrollback
                self.scrollback.append([c.copy() for c in self.grid[0]])
                if len(self.scrollback) > self.max_scrollback:
                    self.scrollback.pop(0)

            for r in range(self.top_margin, self.bottom_margin):
                self.grid[r] = self.grid[r + 1]
            self.grid[self.bottom_margin] = self._create_empty_row()

    def scroll_down(self, n: int = 1):
        """Scroll text down in the scrolling region (top_margin..bottom_margin)."""
        for _ in range(n):
            for r in range(self.bottom_margin, self.top_margin, -1):
                self.grid[r] = self.grid[r - 1]
            self.grid[self.top_margin] = self._create_empty_row()

    def insert_lines(self, n: int = 1):
        """Insert n blank lines at cursor row."""
        if self.cursor.row < self.top_margin or self.cursor.row > self.bottom_margin:
            return
        for _ in range(n):
            for r in range(self.bottom_margin, self.cursor.row, -1):
                self.grid[r] = self.grid[r - 1]
            self.grid[self.cursor.row] = self._create_empty_row()

    def delete_lines(self, n: int = 1):
        """Delete n lines at cursor row."""
        if self.cursor.row < self.top_margin or self.cursor.row > self.bottom_margin:
            return
        for _ in range(n):
            for r in range(self.cursor.row, self.bottom_margin):
                self.grid[r] = self.grid[r + 1]
            self.grid[self.bottom_margin] = self._create_empty_row()

    def insert_chars(self, n: int = 1):
        """Insert n spaces at cursor column, shifting existing chars right."""
        r = self.cursor.row
        c = self.cursor.col
        row = self.grid[r]
        for _ in range(n):
            row.pop()
            row.insert(c, CharCell(char=" ", fg=self.current_fg, bg=self.current_bg))

    def delete_chars(self, n: int = 1):
        """Delete n characters at cursor column, shifting chars left."""
        r = self.cursor.row
        c = self.cursor.col
        row = self.grid[r]
        for _ in range(n):
            if c < len(row):
                row.pop(c)
                row.append(CharCell(char=" ", fg=self.current_fg, bg=self.current_bg))

    def erase_chars(self, n: int = 1):
        """Erase n characters starting at cursor column without shifting."""
        r = self.cursor.row
        for i in range(n):
            col = self.cursor.col + i
            if col < self.cols:
                self.grid[r][col] = CharCell(char=" ", fg=self.current_fg, bg=self.current_bg)

    def erase_in_display(self, mode: int = 0):
        """
        Erase display (CSI J):
        - 0: cursor to end of screen
        - 1: start of screen to cursor
        - 2: entire screen
        - 3: entire screen and clear scrollback
        """
        if mode == 0:
            self.erase_in_line(0)
            for r in range(self.cursor.row + 1, self.rows):
                self.grid[r] = self._create_empty_row()
        elif mode == 1:
            self.erase_in_line(1)
            for r in range(0, self.cursor.row):
                self.grid[r] = self._create_empty_row()
        elif mode == 2:
            self.clear()
        elif mode == 3:
            self.clear()
            self.scrollback.clear()

    def erase_in_line(self, mode: int = 0):
        """
        Erase line (CSI K):
        - 0: cursor to end of line
        - 1: start of line to cursor
        - 2: entire line
        """
        r = self.cursor.row
        if mode == 0:
            for c in range(self.cursor.col, self.cols):
                self.grid[r][c] = CharCell(char=" ", fg=self.current_fg, bg=self.current_bg)
        elif mode == 1:
            for c in range(0, min(self.cursor.col + 1, self.cols)):
                self.grid[r][c] = CharCell(char=" ", fg=self.current_fg, bg=self.current_bg)
        elif mode == 2:
            self.grid[r] = self._create_empty_row()

    def save_cursor(self):
        """Save cursor position."""
        self.cursor.saved_row = self.cursor.row
        self.cursor.saved_col = self.cursor.col

    def restore_cursor(self):
        """Restore cursor position."""
        self.cursor.row = max(0, min(self.rows - 1, self.cursor.saved_row))
        self.cursor.col = max(0, min(self.cols - 1, self.cursor.saved_col))

    def switch_to_alt_buffer(self):
        """Switch to alternate screen buffer (smcup / ?1049h)."""
        if not self.in_alt_buffer:
            self.in_alt_buffer = True
            self.alt_grid = self._create_empty_grid()
            self.save_cursor()
            self.cursor.row = 0
            self.cursor.col = 0

    def switch_to_primary_buffer(self):
        """Switch back to primary screen buffer (rmcup / ?1049l)."""
        if self.in_alt_buffer:
            self.in_alt_buffer = False
            self.restore_cursor()

    # ───────────────────────────────────────────────────────────
    # Text Extraction & Search Utilities
    # ───────────────────────────────────────────────────────────

    def get_line_text(self, row: int, strip_trailing: bool = True) -> str:
        """Get plain text content of a single row."""
        if 0 <= row < self.rows:
            line = "".join(cell.char for cell in self.grid[row])
            return line.rstrip() if strip_trailing else line
        return ""

    def get_rendered_text(self, strip_trailing: bool = True) -> str:
        """Get all visible screen lines as a newline-separated string."""
        lines = [self.get_line_text(r, strip_trailing=strip_trailing) for r in range(self.rows)]
        # Trim empty trailing lines from the bottom
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def contains(self, substring: str, case_sensitive: bool = False) -> bool:
        """Check if substring exists anywhere in current rendered screen."""
        text = self.get_rendered_text()
        if not case_sensitive:
            return substring.lower() in text.lower()
        return substring in text

    def search_regex(self, pattern: Union[str, Pattern]) -> bool:
        """Check if regex pattern matches anywhere in current rendered screen."""
        text = self.get_rendered_text()
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

    def snapshot(self) -> "TerminalState":
        """Create a thread-safe point-in-time snapshot copy of the terminal grid and cursor."""
        with self._lock:
            snap = TerminalState(
                rows=self.rows,
                cols=self.cols,
                default_fg=self.default_fg,
                default_bg=self.default_bg,
                palette=self.palette,
                max_scrollback=self.max_scrollback,
            )
            snap.in_alt_buffer = self.in_alt_buffer
            snap.cursor = Cursor(
                row=self.cursor.row,
                col=self.cursor.col,
                visible=self.cursor.visible,
                saved_row=self.cursor.saved_row,
                saved_col=self.cursor.saved_col,
            )
            snap.primary_grid = [[cell.copy() for cell in row] for row in self.primary_grid]
            snap.alt_grid = [[cell.copy() for cell in row] for row in self.alt_grid]
            return snap

