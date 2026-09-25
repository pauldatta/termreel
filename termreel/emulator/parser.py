"""
Streaming VT/xterm escape-sequence parser feeding a TerminalState.

A byte-at-a-time state machine (after ECMA-48 / the DEC VT500 parser model):
an escape sequence split across two ``feed`` calls is carried over instead
of being printed as ``[31m``. Behaviour is checked against tmux by
``tests/test_emulator_differential.py``.
"""

import codecs
import re
from typing import Callable, List, Optional, Tuple, Union

from termreel.emulator.state import TerminalState
from termreel.emulator.colors import truecolor_rgb

GROUND = 0
ESCAPE = 1
ESCAPE_INTERMEDIATE = 2
CSI_ENTRY = 3
CSI_IGNORE = 4
OSC_STRING = 5
STRING_IGNORE = 6  # DCS / SOS / PM / APC payloads

# Characters that end a run of plain printable text.
_SPECIAL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Upper bound on buffered sequence bytes, so a stream that opens an OSC and
# never terminates it cannot grow memory without limit.
_MAX_SEQ = 4096
_MAX_OSC = 65536


class ANSIParser:
    """
    Parses ANSI escape sequences and applies them to a TerminalState machine.

    ``responder``, when set, receives replies the terminal owes the
    application (cursor position reports, device attributes). The PTY
    supervisor wires it to the child's input. It is left unset for ``live``,
    where the operator's real terminal already answers these queries.
    """

    def __init__(self, state: TerminalState, responder: Optional[Callable[[str], None]] = None):
        self.state = state
        self.window_title = ""
        self.responder = responder
        self._utf8_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._reset_parser()

    def _reset_parser(self) -> None:
        self._pstate = GROUND
        self._buf: List[str] = []
        self._intermediates = ""
        self._osc: List[str] = []
        self._osc_len = 0
        self._esc_in_string = False

    # ------------------------------------------------------------------ feed

    def feed(self, data: Union[str, bytes]):
        """Feed text or byte chunks into the ANSI parser."""
        if isinstance(data, (bytes, bytearray, memoryview)):
            text = self._utf8_decoder.decode(bytes(data), final=False)
        else:
            text = data
        if not text:
            return

        state = self.state
        i = 0
        n = len(text)
        special = _SPECIAL
        while i < n:
            if self._pstate == GROUND:
                m = special.search(text, i)
                end = m.start() if m else n
                if end > i:
                    print_char = state.print_char
                    for ch in text[i:end]:
                        print_char(ch)
                    i = end
                    if i >= n:
                        break
                ch = text[i]
                i += 1
                self._ground_control(ch)
                continue

            ch = text[i]
            i += 1
            self._advance(ch)

    def _ground_control(self, ch: str) -> None:
        if ch == "\x1b":
            self._pstate = ESCAPE
            self._intermediates = ""
        else:
            self._execute(ch)

    def _execute(self, ch: str) -> None:
        """
        C0 control characters.

        Code points U+0080..U+009F arrive here after UTF-8 decoding. tmux (in
        UTF-8 mode, which is what TermReel records) does not act on them, so
        neither do we: ``\\xc2\\x9b31m`` is not a CSI.
        """
        s = self.state
        s.last_printed = None
        if ch == "\r":
            s.carriage_return()
        elif ch in ("\n", "\x0b", "\x0c"):
            s.line_feed()
        elif ch == "\b":
            s.backspace()
        elif ch == "\t":
            s.tab()
        elif ch == "\x0e":  # SO -> G1
            s.active_charset = 1
        elif ch == "\x0f":  # SI -> G0
            s.active_charset = 0
        # BEL, NUL, DEL, ENQ, C1 and the rest are not displayed.

    def _enter_csi(self) -> None:
        self._pstate = CSI_ENTRY
        self._buf = []
        self._intermediates = ""

    def _enter_osc(self) -> None:
        self._pstate = OSC_STRING
        self._osc = []
        self._osc_len = 0
        self._esc_in_string = False

    def _advance(self, ch: str) -> None:
        ps = self._pstate
        code = ord(ch)

        if ps == ESCAPE or ps == ESCAPE_INTERMEDIATE:
            if ch in ("\x18", "\x1a"):
                self._pstate = GROUND
                return
            if ch == "\x1b":
                self._pstate = ESCAPE
                self._intermediates = ""
                return
            if code < 0x20:
                self._execute(ch)
                return
            if 0x20 <= code <= 0x2F:
                self._intermediates += ch
                self._pstate = ESCAPE_INTERMEDIATE
                if len(self._intermediates) > 4:
                    self._pstate = GROUND
                return
            self._pstate = GROUND
            if ps == ESCAPE:
                if ch == "[":
                    self._enter_csi()
                    return
                if ch == "]":
                    self._enter_osc()
                    return
                if ch in ("P", "X", "^", "_"):
                    self._pstate = STRING_IGNORE
                    self._esc_in_string = False
                    return
            self._esc_dispatch(self._intermediates, ch)
            return

        if ps == CSI_ENTRY or ps == CSI_IGNORE:
            if ch in ("\x18", "\x1a"):
                self._pstate = GROUND
                return
            if ch == "\x1b":
                self._pstate = ESCAPE
                self._intermediates = ""
                return
            if code < 0x20:
                self._execute(ch)
                return
            if 0x40 <= code <= 0x7E:
                self._pstate = GROUND
                if ps == CSI_ENTRY:
                    self._csi_dispatch("".join(self._buf), self._intermediates, ch)
                return
            if ps == CSI_IGNORE:
                return
            if 0x30 <= code <= 0x3F:
                if self._intermediates:
                    self._pstate = CSI_IGNORE
                    return
                self._buf.append(ch)
                if len(self._buf) > _MAX_SEQ:
                    self._pstate = CSI_IGNORE
                return
            if 0x20 <= code <= 0x2F:
                self._intermediates += ch
                return
            # DEL or anything else inside CSI: ignore the byte.
            return

        if ps == OSC_STRING:
            if self._esc_in_string:
                self._esc_in_string = False
                self._osc_dispatch("".join(self._osc))
                self._pstate = GROUND
                if ch != "\\":
                    # ESC not followed by '\' ends the string and starts a new sequence.
                    self._pstate = ESCAPE
                    self._intermediates = ""
                    self._advance(ch)
                return
            if ch == "\x07" or ch == "\x9c":
                self._osc_dispatch("".join(self._osc))
                self._pstate = GROUND
                return
            if ch == "\x1b":
                self._esc_in_string = True
                return
            if ch in ("\x18", "\x1a"):
                self._pstate = GROUND
                return
            if self._osc_len < _MAX_OSC:
                self._osc.append(ch)
                self._osc_len += 1
            return

        if ps == STRING_IGNORE:
            if self._esc_in_string:
                self._esc_in_string = False
                self._pstate = GROUND
                if ch != "\\":
                    self._pstate = ESCAPE
                    self._intermediates = ""
                    self._advance(ch)
                return
            if ch == "\x1b":
                self._esc_in_string = True
            elif ch in ("\x9c", "\x18", "\x1a"):
                self._pstate = GROUND
            return

    # --------------------------------------------------------------- ESC

    def _esc_dispatch(self, intermediates: str, final: str) -> None:
        s = self.state
        s.last_printed = None
        if intermediates == "":
            if final == "7":
                s.save_cursor()
            elif final == "8":
                s.restore_cursor()
            elif final == "D":
                s._index()
            elif final == "E":
                s.next_line()
            elif final == "H":
                s.set_tab_stop()
            elif final == "M":
                s.reverse_index()
            elif final == "c":
                s.reset()
            # ESC = / ESC > (keypad), ESC N / O (single shift), ESC \ (stray ST): no display effect.
            return
        lead = intermediates[0]
        if lead in "()*+" and len(intermediates) == 1:
            slot = {"(": 0, ")": 1}.get(lead)
            if slot is not None:
                s.charsets[slot] = "0" if final == "0" else "B"
            return
        if intermediates == "#" and final == "8":
            s.fill_with_e()
            return
        # ESC % G / ESC % @ (UTF-8 selection), ESC # 3-6 and others: consumed, no effect.

    # --------------------------------------------------------------- CSI

    @staticmethod
    def _split_params(params_str: str) -> Tuple[str, List[List[int]]]:
        """Split 'private marker' and ';'-separated groups of ':'-separated sub-params."""
        prefix = ""
        if params_str and params_str[0] in "<=>?":
            prefix = params_str[0]
            params_str = params_str[1:]
        groups: List[List[int]] = []
        if params_str == "":
            return prefix, groups
        for group in params_str.split(";"):
            subs = []
            for part in group.split(":"):
                if part.isdigit():
                    subs.append(min(int(part), 65535))
                elif part == "":
                    subs.append(-1)  # omitted
                else:
                    return prefix, []  # malformed (e.g. a second private marker)
            groups.append(subs)
        return prefix, groups

    def _parse_params(self, params_str: str) -> List[int]:
        """Parse semicolon/colon separated numeric parameters (flat list, omitted -> 0)."""
        _, groups = self._split_params(params_str)
        flat: List[int] = []
        for g in groups:
            for v in g:
                flat.append(0 if v < 0 else v)
        return flat

    def _csi_dispatch(self, params_str: str, intermediates: str, cmd: str) -> None:
        prefix, groups = self._split_params(params_str)
        # First sub-parameter of each group; omitted -> 0.
        params = [(g[0] if g and g[0] >= 0 else 0) for g in groups]
        s = self.state

        def p(idx: int, default: int = 1) -> int:
            v = params[idx] if idx < len(params) else 0
            return v if v > 0 else default

        if intermediates or prefix:
            # tmux forgets the REP character on every CSI except a plain
            # ``CSI b``, private-mode and intermediate forms included.
            s.last_printed = None

        if intermediates:
            # DECSTR (! p), DECSCUSR (SP q), DECRQM ($ p), etc. tmux ignores
            # DECSTR entirely (margins and origin mode survive it), and
            # TermReel follows tmux so both backends render the same bytes
            # the same way.
            return

        if prefix == "?":
            if cmd in ("h", "l"):
                self._handle_private_mode(params, cmd)
            elif cmd == "n":
                if p(0, 0) == 6 and self.responder is not None:
                    row, col = self._report_position()
                    self._reply(f"\x1b[?{row};{col}R")
            return

        if prefix == ">":
            if cmd == "c" and p(0, 0) == 0:
                self._reply("\x1b[>0;0;0c")
            # CSI > Ps m (modifyOtherKeys), CSI > q (XTVERSION)...: not SGR.
            return

        if prefix in ("<", "="):
            return

        if cmd == "m":
            self._handle_sgr(groups)
        elif cmd == "A":
            s.cursor_up(p(0))
        elif cmd in ("B", "e"):
            s.cursor_down(p(0))
        elif cmd in ("C", "a"):
            s.cursor_forward(p(0))
        elif cmd == "D":
            s.cursor_back(p(0))
        elif cmd == "E":
            s.cursor_down(p(0))
            s.cursor.col = 0
        elif cmd == "F":
            s.cursor_up(p(0))
            s.cursor.col = 0
        elif cmd in ("G", "`"):
            s.move_cursor(col=p(0) - 1)
        elif cmd == "d":
            s.move_cursor(row=p(0) - 1)
            s.cursor.col = min(s.cursor.col, s.cols - 1)
        elif cmd in ("H", "f"):
            s.move_cursor(row=p(0) - 1, col=p(1) - 1)
        elif cmd == "J":
            s.erase_in_display(p(0, 0))
        elif cmd == "K":
            s.erase_in_line(p(0, 0))
        elif cmd == "L":
            s.insert_lines(p(0))
        elif cmd == "M":
            s.delete_lines(p(0))
        elif cmd == "@":
            s.insert_chars(p(0))
        elif cmd == "P":
            s.delete_chars(p(0))
        elif cmd == "X":
            s.erase_chars(p(0))
        elif cmd == "S":
            s.scroll_up(p(0))
        elif cmd == "T":
            if len(params) <= 1:
                s.scroll_down(p(0))
        elif cmd == "I":
            s.tab(p(0))
        elif cmd == "Z":
            s.back_tab(p(0))
        elif cmd == "b":
            if s.last_printed is not None:
                ch = s.last_printed
                for _ in range(min(p(0), 65535)):
                    for c in ch:
                        s.print_char(c)
        elif cmd == "g":
            s.clear_tab_stop(p(0, 0))
        elif cmd == "s":
            s.save_cursor()
        elif cmd == "u":
            s.restore_cursor()
        elif cmd == "r":
            # tmux: an omitted bottom means the last row, but an explicit 0
            # is clamped up to 1 (so "5;0r" is an invalid, ignored region).
            raw_bottom = groups[1][0] if len(groups) > 1 and groups[1] else -1
            top = p(0) - 1
            bottom = (s.rows if raw_bottom < 0 else max(raw_bottom, 1)) - 1
            s.set_margins(top, bottom)
        elif cmd in ("h", "l"):
            for mode in params:
                if mode == 4:
                    s.insert_mode = (cmd == "h")
        elif cmd == "n":
            mode = p(0, 0)
            if mode == 5:
                self._reply("\x1b[0n")
            elif mode == 6:
                row, col = self._report_position()
                self._reply(f"\x1b[{row};{col}R")
        elif cmd == "c":
            if p(0, 0) == 0:
                self._reply("\x1b[?1;2c")
        # 't' (window ops), 'q' (LEDs) and anything unknown: ignored.
        # tmux only repeats a character printed immediately before REP: any
        # other control sequence, SGR included, forgets it.
        if cmd != "b":
            s.last_printed = None

    def _report_position(self) -> Tuple[int, int]:
        s = self.state
        row = s.cursor.row - (s.top_margin if s.origin_mode else 0)
        col = min(s.cursor.col, s.cols - 1)
        return row + 1, col + 1

    def _reply(self, text: str) -> None:
        responder = self.responder
        if responder is None:
            return
        try:
            responder(text)
        except Exception:
            pass

    def _handle_private_mode(self, params: List[int], cmd: str):
        """Handle DEC private mode escapes (e.g. ?25h, ?1049h)."""
        is_set = (cmd == "h")
        s = self.state
        for mode in params:
            if mode == 25:  # Cursor show/hide
                s.cursor_visible = is_set
            elif mode == 7:
                s.autowrap = is_set
                if not is_set and s.cursor.col >= s.cols:
                    s.cursor.col = s.cols - 1
            elif mode == 6:
                s.origin_mode = is_set
                s.move_cursor(row=0, col=0)
            elif mode == 1:
                s.app_cursor_keys = is_set
            elif mode == 2004:
                s.bracketed_paste = is_set
            elif mode == 1049:
                if is_set:
                    s.switch_to_alt_buffer(save_cursor=True)
                else:
                    s.switch_to_primary_buffer(restore_cursor=True)
            elif mode in (47, 1047):
                if is_set:
                    s.switch_to_alt_buffer(save_cursor=False)
                else:
                    s.switch_to_primary_buffer(restore_cursor=False)
            elif mode == 1048:
                if is_set:
                    s.save_cursor()
                else:
                    s.restore_cursor()

    # --------------------------------------------------------------- SGR

    def _extended_color(self, group: List[int], rest: List[List[int]]) -> Tuple[Optional[tuple], int]:
        """
        Decode 38/48/58 colour. Returns (rgb or None, number of extra groups consumed).

        Accepts the colon form in one group (``38:5:n``, ``38:2::r:g:b``,
        ``38:2:r:g:b``) and the legacy semicolon form (``38;5;n``, ``38;2;r;g;b``).
        """
        pal = self.state.palette
        if len(group) > 1:
            kind = group[1]
            vals = [v if v >= 0 else 0 for v in group[2:]]
            if kind == 5 and vals:
                return pal.get_256_color(min(vals[0], 255)), 0
            if kind == 2:
                rgb = vals[-3:] if len(vals) >= 3 else None
                if rgb:
                    return truecolor_rgb(*rgb), 0
            return None, 0
        firsts = [(g[0] if g and g[0] >= 0 else 0) for g in rest]
        if not firsts:
            return None, 0
        kind = firsts[0]
        if kind == 5:
            if len(firsts) >= 2:
                return pal.get_256_color(min(firsts[1], 255)), 2
            return None, len(firsts)
        if kind == 2:
            if len(firsts) >= 4:
                return truecolor_rgb(firsts[1], firsts[2], firsts[3]), 4
            return None, len(firsts)
        return None, 1

    def _handle_sgr(self, groups):
        """Select Graphic Rendition (SGR) text styling."""
        s = self.state
        if groups and isinstance(groups[0], int):
            groups = [[g] for g in groups]  # legacy flat list
        if not groups:
            groups = [[0]]

        idx = 0
        n = len(groups)
        while idx < n:
            group = groups[idx]
            code = group[0] if group[0] >= 0 else 0

            if code in (38, 48, 58):
                color, used = self._extended_color(group, groups[idx + 1:])
                if code == 38 and color is not None:
                    s.current_fg = color
                elif code == 48 and color is not None:
                    s.current_bg = color
                idx += 1 + used
                continue

            if code == 0:
                s.reset_attributes()
            elif code == 1:
                s.current_bold = True
            elif code == 2:
                s.current_dim = True
            elif code == 3:
                s.current_italic = True
            elif code == 4:
                style = group[1] if len(group) > 1 else 1
                s.current_underline = style != 0
            elif code in (5, 6):
                s.current_blink = True
            elif code == 7:
                s.current_reverse = True
            elif code == 8:
                s.current_hidden = True
            elif code == 9:
                s.current_strikethrough = True
            elif code == 21:
                s.current_underline = True
            elif code == 22:
                s.current_bold = False
                s.current_dim = False
            elif code == 23:
                s.current_italic = False
            elif code == 24:
                s.current_underline = False
            elif code == 25:
                s.current_blink = False
            elif code == 27:
                s.current_reverse = False
            elif code == 28:
                s.current_hidden = False
            elif code == 29:
                s.current_strikethrough = False
            elif 30 <= code <= 37:
                color_idx = (code - 30) + (8 if s.current_bold else 0)
                s.current_fg = s.palette.get_16_color(color_idx)
            elif code == 39:
                s.current_fg = s.default_fg
            elif 40 <= code <= 47:
                s.current_bg = s.palette.get_16_color(code - 40)
            elif code == 49:
                s.current_bg = s.default_bg
            elif 90 <= code <= 97:
                s.current_fg = s.palette.get_16_color(code - 90 + 8)
            elif 100 <= code <= 107:
                s.current_bg = s.palette.get_16_color(code - 100 + 8)
            # 53/55 overline, 59 underline colour reset, etc.: no rendering.
            idx += 1

    # --------------------------------------------------------------- OSC

    def _handle_osc(self, payload: str):
        """Backwards-compatible alias."""
        self._osc_dispatch(payload)

    def _osc_dispatch(self, payload: str):
        """Handle OSC control strings (window title, colour queries)."""
        code_str, _, text = payload.partition(";")
        if code_str in ("0", "1", "2"):
            self.window_title = text
        elif code_str in ("10", "11") and text == "?":
            rgb = self.state.default_fg if code_str == "10" else self.state.default_bg
            hexes = "/".join(f"{int(max(0.0, min(1.0, c)) * 65535):04x}" for c in rgb)
            self._reply(f"\x1b]{code_str};rgb:{hexes}\x1b\\")

    # ------------------------------------------------------------ tmux feed

    def feed_tmux_pane(self, raw_ansi: str, cursor: Optional[Tuple[int, int]] = None,
                       cursor_visible: Optional[bool] = None, joined: bool = False):
        """
        Load a full ``tmux capture-pane -p -e [-J]`` dump into the state.

        A line longer than the grid (``-J`` joins soft-wrapped rows) is laid
        out across rows with the soft-wrap flag set, which is what makes
        wrap-aware masking possible on the tmux backend.

        tmux's capture format differs from a live stream in two ways that
        matter here (checked against tmux 3.6):

        * DEC special graphics cells are bracketed with SO/SI, and the reader
          is expected to treat G1 as the graphics set.
        * Without ``-J`` every line re-emits its attributes from scratch;
          with ``-J`` (``joined=True``) attributes and the SO state carry
          over from one line to the next and are only emitted on change.
        """
        from termreel.emulator.state import Row, TerminalState as _TS

        target = self.state
        cols = target.cols
        rows = target.rows
        target.clear()
        grid = target.grid

        scratch = _TS(rows=1, cols=max(cols * rows, cols), default_fg=target.default_fg,
                      default_bg=target.default_bg, palette=target.palette)
        scratch.autowrap = False
        scratch.charsets = ["B", "0"]
        sub = ANSIParser(scratch)

        out_row = 0
        lines = raw_ansi.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for line in lines:
            if out_row >= rows:
                break
            scratch.primary_grid = scratch._create_empty_grid()
            scratch.cursor.row = 0
            scratch.cursor.col = 0
            if not joined:
                scratch.reset_attributes()
                scratch.active_charset = 0
            scratch.last_printed = None
            sub._reset_parser()
            sub.feed(line)
            cells = scratch.primary_grid[0]
            used = scratch.cursor.col
            # Trim to the last cell that was written or carries a background.
            length = max(used, 0)
            chunk_start = 0
            while True:
                if out_row >= rows:
                    break
                end = min(chunk_start + cols, max(length, chunk_start))
                # Do not split a wide character across rows.
                if end < length and end > chunk_start and cells[end].char == "":
                    end -= 1
                row = grid[out_row]
                for c, cell in enumerate(cells[chunk_start:end]):
                    row[c] = cell
                if end - chunk_start < cols and end < length:
                    row[end - chunk_start].char = ""
                more = end < length
                row.wrapped = more
                out_row += 1
                if not more:
                    break
                chunk_start = end

        if cursor is not None:
            cx, cy = cursor
            target.cursor.row = max(0, min(rows - 1, cy))
            target.cursor.col = max(0, min(cols, cx))
        if cursor_visible is not None:
            target.cursor.visible = cursor_visible
