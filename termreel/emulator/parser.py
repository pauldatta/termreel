"""
ANSI/VT100/Xterm escape sequence parser and state stream processor.
"""

import re
from typing import Union, List, Optional
from termreel.emulator.state import TerminalState
from termreel.emulator.colors import truecolor_rgb


class ANSIParser:
    """
    Parses ANSI escape sequences and applies them to a TerminalState machine.
    """

    # Regex for standard CSI sequences: ESC [ <private?> <params> <intermediate> <command>
    CSI_PATTERN = re.compile(r"^\x1b\[([\?><=]?)([0-9;:?]*)([ -/]*)([a-zA-Z~@`])")

    # Regex for OSC sequences: ESC ] <code> ; <data> (BEL | ESC \)
    OSC_PATTERN = re.compile(r"^\x1b\]([^\x1b\x07]*)(?:\x07|\x1b\\)")

    # Regex for 2-character ESC sequences (e.g. ESC 7, ESC 8, ESC M, ESC c)
    ESC_2CHAR_PATTERN = re.compile(r"^\x1b([78cMENH#%()=><])")

    def __init__(self, state: TerminalState):
        self.state = state
        self.window_title = ""

    def feed(self, data: Union[str, bytes]):
        """Feed text or byte chunks into the ANSI parser."""
        if isinstance(data, bytes):
            # Replace invalid UTF-8 with Unicode replacement char
            text = data.decode("utf-8", errors="replace")
        else:
            text = data

        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            if ch == "\x1b":
                # Check CSI sequence: \x1b[ ...
                if i + 1 < n and text[i + 1] == "[":
                    m = self.CSI_PATTERN.match(text[i:])
                    if m:
                        prefix, params_str, intermediate, cmd = m.groups()
                        self._handle_csi(prefix, params_str, cmd)
                        i += len(m.group(0))
                        continue
                    else:
                        # Malformed or partial CSI, skip ESC [
                        i += 2
                        continue

                # Check OSC sequence: \x1b] ...
                elif i + 1 < n and text[i + 1] == "]":
                    m = self.OSC_PATTERN.match(text[i:])
                    if m:
                        osc_payload = m.group(1)
                        self._handle_osc(osc_payload)
                        i += len(m.group(0))
                        continue
                    else:
                        i += 2
                        continue

                # Check simple 2-char ESC sequence
                m = self.ESC_2CHAR_PATTERN.match(text[i:])
                if m:
                    esc_cmd = m.group(1)
                    self._handle_esc_char(esc_cmd)
                    i += len(m.group(0))
                    continue

                # Unhandled lone ESC
                i += 1
                continue

            # Standard printable character or ASCII control code
            self.state.write_char(ch)
            i += 1

    def feed_tmux_pane(self, raw_ansi: str):
        """
        Loads a full tmux pane capture (tmux capture-pane -e -p).
        Clears screen and parses line-by-line.
        """
        self.state.clear()
        lines = raw_ansi.split("\n")
        for r_idx, line in enumerate(lines[: self.state.rows]):
            self.state.cursor.row = r_idx
            self.state.cursor.col = 0
            self.feed(line)

    def _parse_params(self, params_str: str) -> List[int]:
        """Parse semicolon/colon separated numeric parameters."""
        if not params_str:
            return []
        # Support both ';' and ':' delimiters
        normalized = params_str.replace(":", ";")
        parts = normalized.split(";")
        result = []
        for p in parts:
            if p.isdigit():
                result.append(int(p))
            elif not p:
                result.append(0)
        return result

    def _handle_csi(self, prefix: str, params_str: str, cmd: str):
        """Handle CSI escape sequence."""
        params = self._parse_params(params_str)

        # Private mode set / reset (e.g. ?25h, ?1049h)
        if prefix == "?":
            self._handle_private_mode(params, cmd)
            return

        if cmd == "m":
            self._handle_sgr(params)
        elif cmd == "A":  # Cursor Up
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.row = max(0, self.state.cursor.row - count)
        elif cmd == "B":  # Cursor Down
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.row = min(self.state.rows - 1, self.state.cursor.row + count)
        elif cmd == "C":  # Cursor Forward (Right)
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.col = min(self.state.cols - 1, self.state.cursor.col + count)
        elif cmd == "D":  # Cursor Back (Left)
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.col = max(0, self.state.cursor.col - count)
        elif cmd == "E":  # Cursor Next Line
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.col = 0
            self.state.cursor.row = min(self.state.rows - 1, self.state.cursor.row + count)
        elif cmd == "F":  # Cursor Previous Line
            count = params[0] if (params and params[0] > 0) else 1
            self.state.cursor.col = 0
            self.state.cursor.row = max(0, self.state.cursor.row - count)
        elif cmd in ("G", "`"):  # Cursor Horizontal Absolute
            col = (params[0] - 1) if (params and params[0] > 0) else 0
            self.state.cursor.col = max(0, min(self.state.cols - 1, col))
        elif cmd == "d":  # Line Position Absolute
            row = (params[0] - 1) if (params and params[0] > 0) else 0
            self.state.cursor.row = max(0, min(self.state.rows - 1, row))
        elif cmd in ("H", "f"):  # Cursor Position (Row, Col)
            row = (params[0] - 1) if (len(params) > 0 and params[0] > 0) else 0
            col = (params[1] - 1) if (len(params) > 1 and params[1] > 0) else 0
            self.state.cursor.row = max(0, min(self.state.rows - 1, row))
            self.state.cursor.col = max(0, min(self.state.cols - 1, col))
        elif cmd == "J":  # Erase in Display
            mode = params[0] if params else 0
            self.state.erase_in_display(mode)
        elif cmd == "K":  # Erase in Line
            mode = params[0] if params else 0
            self.state.erase_in_line(mode)
        elif cmd == "L":  # Insert Line
            count = params[0] if (params and params[0] > 0) else 1
            self.state.insert_lines(count)
        elif cmd == "M":  # Delete Line
            count = params[0] if (params and params[0] > 0) else 1
            self.state.delete_lines(count)
        elif cmd == "@":  # Insert Character
            count = params[0] if (params and params[0] > 0) else 1
            self.state.insert_chars(count)
        elif cmd == "P":  # Delete Character
            count = params[0] if (params and params[0] > 0) else 1
            self.state.delete_chars(count)
        elif cmd == "X":  # Erase Character
            count = params[0] if (params and params[0] > 0) else 1
            self.state.erase_chars(count)
        elif cmd == "S":  # Scroll Up
            count = params[0] if (params and params[0] > 0) else 1
            self.state.scroll_up(count)
        elif cmd == "T":  # Scroll Down
            count = params[0] if (params and params[0] > 0) else 1
            self.state.scroll_down(count)
        elif cmd == "s":  # Save Cursor
            self.state.save_cursor()
        elif cmd == "u":  # Restore Cursor
            self.state.restore_cursor()
        elif cmd == "r":  # Set Scrolling Region
            top = (params[0] - 1) if (len(params) > 0 and params[0] > 0) else 0
            bot = (params[1] - 1) if (len(params) > 1 and params[1] > 0) else (self.state.rows - 1)
            self.state.top_margin = max(0, min(self.state.rows - 1, top))
            self.state.bottom_margin = max(self.state.top_margin, min(self.state.rows - 1, bot))

    def _handle_private_mode(self, params: List[int], cmd: str):
        """Handle DEC private mode escapes (e.g. ?25h, ?1049h)."""
        is_set = (cmd == "h")
        for mode in params:
            if mode == 25:  # Cursor show/hide
                self.state.cursor_visible = is_set
            elif mode in (47, 1047, 1049):  # Alternate screen buffer
                if is_set:
                    self.state.switch_to_alt_buffer()
                else:
                    self.state.switch_to_primary_buffer()

    def _handle_sgr(self, params: List[int]):
        """Select Graphic Rendition (SGR) text styling."""
        if not params:
            params = [0]

        idx = 0
        num_params = len(params)

        while idx < num_params:
            p = params[idx]

            if p == 0:  # Reset all attributes
                self.state.reset_attributes()
            elif p == 1:  # Bold
                self.state.current_bold = True
            elif p == 2:  # Dim
                self.state.current_dim = True
            elif p == 3:  # Italic
                self.state.current_italic = True
            elif p == 4:  # Underline
                self.state.current_underline = True
            elif p in (5, 6):  # Blink
                self.state.current_blink = True
            elif p == 7:  # Reverse
                self.state.current_reverse = True
            elif p == 8:  # Hidden
                self.state.current_hidden = True
            elif p == 9:  # Strikethrough
                self.state.current_strikethrough = True
            elif p in (21, 22):  # Normal intensity (bold/dim off)
                self.state.current_bold = False
                self.state.current_dim = False
            elif p == 23:  # Not italic
                self.state.current_italic = False
            elif p == 24:  # Not underline
                self.state.current_underline = False
            elif p == 25:  # Blink off
                self.state.current_blink = False
            elif p == 27:  # Reverse off
                self.state.current_reverse = False
            elif p == 28:  # Hidden off
                self.state.current_hidden = False
            elif p == 29:  # Strikethrough off
                self.state.current_strikethrough = False

            # Standard foreground 30..37
            elif 30 <= p <= 37:
                color_idx = (p - 30) + (8 if self.state.current_bold else 0)
                self.state.current_fg = self.state.palette.get_16_color(color_idx)
            elif p == 39:  # Default foreground
                self.state.current_fg = self.state.default_fg

            # Standard background 40..47
            elif 40 <= p <= 47:
                self.state.current_bg = self.state.palette.get_16_color(p - 40)
            elif p == 49:  # Default background
                self.state.current_bg = self.state.default_bg

            # Bright foreground 90..97
            elif 90 <= p <= 97:
                self.state.current_fg = self.state.palette.get_16_color(p - 90 + 8)

            # Bright background 100..107
            elif 100 <= p <= 107:
                self.state.current_bg = self.state.palette.get_16_color(p - 100 + 8)

            # Extended color (256 or TrueColor)
            elif p in (38, 48):
                is_fg = (p == 38)
                if idx + 1 < num_params:
                    color_type = params[idx + 1]

                    if color_type == 5 and idx + 2 < num_params:
                        # 256 color: 38;5;N or 48;5;N
                        color_val = self.state.palette.get_256_color(params[idx + 2])
                        if is_fg:
                            self.state.current_fg = color_val
                        else:
                            self.state.current_bg = color_val
                        idx += 2

                    elif color_type == 2 and idx + 4 < num_params:
                        # TrueColor 24-bit: 38;2;R;G;B or 48;2;R;G;B
                        r = params[idx + 2]
                        g = params[idx + 3]
                        b = params[idx + 4]
                        color_val = truecolor_rgb(r, g, b)
                        if is_fg:
                            self.state.current_fg = color_val
                        else:
                            self.state.current_bg = color_val
                        idx += 4

            idx += 1

    def _handle_osc(self, payload: str):
        """Handle OSC control strings (e.g. window title)."""
        if ";" in payload:
            code_str, text = payload.split(";", 1)
            if code_str in ("0", "1", "2"):
                self.window_title = text

    def _handle_esc_char(self, cmd: str):
        """Handle 2-character escape codes (ESC + char)."""
        if cmd == "7":  # Save cursor
            self.state.save_cursor()
        elif cmd == "8":  # Restore cursor
            self.state.restore_cursor()
        elif cmd == "M":  # Reverse Index (scroll down)
            if self.state.cursor.row == self.state.top_margin:
                self.state.scroll_down(1)
            elif self.state.cursor.row > 0:
                self.state.cursor.row -= 1
        elif cmd == "E":  # Next line
            self.state.cursor.col = 0
            self.state.line_feed()
        elif cmd == "c":  # Reset initial state
            self.state.clear_all()
