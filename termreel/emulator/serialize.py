"""
Serialize a TerminalState into an ANSI byte stream that redraws it.

Used wherever TermReel has to put a screen into an asciicast without having
the raw program output to replay:

* the tmux backend, where each capture is a *snapshot* of the (possibly
  multi-pane) composited screen, not a stream of escape sequences;
* resuming a paused ``termreel live`` recording, where output produced while
  paused was never written to the cast;
* any time the cast must reflect a redacted snapshot rather than raw output.

The output homes the cursor, clears the screen, writes every row at an
absolute position with SGR runs, then restores the cursor position and
visibility. Replaying it into a terminal of the same size reproduces the
visible grid exactly, regardless of what the terminal showed before.
"""

from typing import List, Optional, Tuple

from termreel.emulator.state import CharCell, TerminalState


def _rgb255(color) -> Tuple[int, int, int]:
    r, g, b = color[:3]
    return (
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255)))),
    )


def _sgr_for(cell: CharCell, default_fg, default_bg) -> str:
    parts: List[str] = ["0"]
    if cell.bold:
        parts.append("1")
    if cell.dim:
        parts.append("2")
    if cell.italic:
        parts.append("3")
    if cell.underline:
        parts.append("4")
    if cell.blink:
        parts.append("5")
    if cell.reverse:
        parts.append("7")
    if cell.hidden:
        parts.append("8")
    if cell.strikethrough:
        parts.append("9")
    if tuple(cell.fg) != tuple(default_fg):
        parts.append("38;2;%d;%d;%d" % _rgb255(cell.fg))
    if tuple(cell.bg) != tuple(default_bg):
        parts.append("48;2;%d;%d;%d" % _rgb255(cell.bg))
    return "\x1b[" + ";".join(parts) + "m"


def _pen_sgr(state: TerminalState) -> str:
    pen = CharCell(
        char=" ", fg=state.current_fg, bg=state.current_bg,
        bold=state.current_bold, dim=state.current_dim, italic=state.current_italic,
        underline=state.current_underline, strikethrough=state.current_strikethrough,
        reverse=state.current_reverse, blink=state.current_blink, hidden=state.current_hidden,
    )
    return _sgr_for(pen, state.default_fg, state.default_bg)


def serialize_screen(state: TerminalState, cursor: Optional[Tuple[int, int]] = None,
                     cursor_visible: Optional[bool] = None,
                     restore_modes: bool = True) -> str:
    """
    Return an ANSI string that redraws ``state``'s active grid.

    ``cursor`` (row, col) and ``cursor_visible`` override the state's own
    cursor, which is useful for a redacted snapshot whose cursor was moved.
    The last column of the last row is written too; the trailing cursor
    positioning makes any pending-wrap state irrelevant.

    With ``restore_modes`` the redraw also re-establishes the alternate
    buffer, scroll margins, DECAWM/DECOM/IRM, G0/G1 charsets and the current
    SGR pen, so bytes that follow the redraw (a resumed ``live`` stream)
    render exactly as they did on the real terminal.
    """
    with state._lock:
        grid = state.grid
        rows, cols = state.rows, state.cols
        crow = state.cursor.row if cursor is None else cursor[0]
        ccol = state.cursor.col if cursor is None else cursor[1]
        cvis = state.cursor.visible if cursor_visible is None else cursor_visible
        out: List[str] = []
        if restore_modes:
            # Normalise the player: primary/alt buffer, full-screen margins,
            # absolute origin, replace mode, autowrap on, ASCII charsets.
            out.append("\x1b[?1049h" if state.in_alt_buffer else "\x1b[?1049l")
            out.append("\x1b[r\x1b[?6l\x1b[4l\x1b[?7h\x1b(B\x1b)B\x0f")
        out.append("\x1b[0m\x1b[H\x1b[2J")
        for r in range(rows):
            row = grid[r]
            out.append("\x1b[%d;1H" % (r + 1))
            current_sgr = None
            line: List[str] = []
            for c in range(min(cols, len(row))):
                cell = row[c]
                if cell.char == "":
                    # Second half of a wide character: the terminal advances
                    # over it when printing the first half.
                    continue
                sgr = _sgr_for(cell, state.default_fg, state.default_bg)
                if sgr != current_sgr:
                    line.append(sgr)
                    current_sgr = sgr
                line.append(cell.char if cell.char else " ")
            out.append("".join(line))
        out.append("\x1b[0m")
        crow = max(0, min(rows - 1, crow))
        ccol = max(0, min(cols - 1, ccol))
        if restore_modes:
            stops = getattr(state, "tab_stops", None)
            default_stops = set(range(0, cols, getattr(state, "tab_width", 8)))
            if stops is not None and set(stops) != default_stops:
                out.append("\x1b[3g")
                for stop in sorted(stops):
                    if 0 < stop < cols:
                        out.append("\x1b[1;%dH\x1bH" % (stop + 1))
            top = getattr(state, "top_margin", 0)
            bottom = getattr(state, "bottom_margin", rows - 1)
            if (top, bottom) != (0, rows - 1) and 0 <= top < bottom < rows:
                out.append("\x1b[%d;%dr" % (top + 1, bottom + 1))
            origin = getattr(state, "origin_mode", False)
            if origin:
                out.append("\x1b[?6h")
                crow_param = max(0, crow - top)
            else:
                crow_param = crow
            out.append("\x1b[%d;%dH" % (crow_param + 1, ccol + 1))
            raw_col = state.cursor.col if cursor is None else cursor[1]
            if raw_col >= cols and getattr(state, "autowrap", True):
                # Pending wrap: reprint the last cell (still in replace mode
                # and ASCII charset) so the terminal is left in the same
                # "next character wraps" state.
                cell = grid[crow][cols - 1] if cols - 1 < len(grid[crow]) else None
                if cell is not None and cell.char != "":
                    out.append(_sgr_for(cell, state.default_fg, state.default_bg))
                    out.append(cell.char or " ")
            if not getattr(state, "autowrap", True):
                out.append("\x1b[?7l")
            if getattr(state, "insert_mode", False):
                out.append("\x1b[4h")
            charsets = getattr(state, "charsets", ["B", "B"])
            if charsets[0] != "B":
                out.append("\x1b(" + charsets[0])
            if len(charsets) > 1 and charsets[1] != "B":
                out.append("\x1b)" + charsets[1])
            if getattr(state, "active_charset", 0) == 1:
                out.append("\x0e")
            out.append(_pen_sgr(state))
        else:
            out.append("\x1b[%d;%dH" % (crow + 1, ccol + 1))
        out.append("\x1b[?25h" if cvis else "\x1b[?25l")
        return "".join(out)
