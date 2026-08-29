#!/usr/bin/env python3
"""
tools/record_terminal.py — Real PTY Terminal Session Video Recorder & Renderer.

Drives actual interactive CLI sessions (e.g. agy, bash, git) inside a real
tmux pseudo-terminal, injects keystrokes with realistic typing cadence,
monitors screen state transitions, and renders the exact live terminal display
into high-fidelity H.264 MP4 videos using PyCairo and FFmpeg.
"""

import sys
import os
import re
import time
import math
import subprocess
import threading
import argparse
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Any, Callable
import cairo

# ───────────────────────────────────────────────────────────
# 256-Color ANSI Palette
# ───────────────────────────────────────────────────────────

PALETTE_16 = [
    # 0-7 Normal
    (0.09, 0.09, 0.14),  # 0: Black
    (0.95, 0.55, 0.66),  # 1: Red
    (0.65, 0.89, 0.63),  # 2: Green
    (0.98, 0.89, 0.69),  # 3: Yellow
    (0.54, 0.71, 0.98),  # 4: Blue
    (0.80, 0.65, 0.97),  # 5: Magenta
    (0.58, 0.89, 0.84),  # 6: Cyan
    (0.80, 0.84, 0.96),  # 7: White
    # 8-15 Bright
    (0.36, 0.39, 0.49),  # 8: Gray
    (0.98, 0.60, 0.70),  # 9: Bright Red
    (0.70, 0.95, 0.68),  # 10: Bright Green
    (1.00, 0.92, 0.72),  # 11: Bright Yellow
    (0.60, 0.76, 1.00),  # 12: Bright Blue
    (0.85, 0.70, 1.00),  # 13: Bright Magenta
    (0.65, 0.94, 0.90),  # 14: Bright Cyan
    (1.00, 1.00, 1.00),  # 15: Bright White
]

DEFAULT_FG = (0.85, 0.88, 0.96)
DEFAULT_BG = (0.10, 0.10, 0.15)
WINDOW_BG = (0.07, 0.07, 0.11)
TITLEBAR_BG = (0.13, 0.14, 0.20)
STATUSBAR_BG = (0.09, 0.09, 0.13)
ACCENT_COLOR = (0.54, 0.71, 0.98)


def get_256_color(idx: int) -> Tuple[float, float, float]:
    if 0 <= idx < 16:
        return PALETTE_16[idx]
    elif 16 <= idx <= 231:
        idx -= 16
        b = idx % 6
        g = (idx // 6) % 6
        r = idx // 36
        levels = [0.0, 95 / 255.0, 135 / 255.0, 175 / 255.0, 215 / 255.0, 1.0]
        return (levels[r], levels[g], levels[b])
    elif 232 <= idx <= 255:
        val = (8 + (idx - 232) * 10) / 255.0
        return (val, val, val)
    return DEFAULT_FG


@dataclass
class CharCell:
    char: str = " "
    fg: Tuple[float, float, float] = DEFAULT_FG
    bg: Tuple[float, float, float] = DEFAULT_BG
    bold: bool = False
    dim: bool = False
    underline: bool = False


class TerminalState:
    """Maintains a 2D screen buffer with full ANSI escape parsing."""

    def __init__(self, rows: int = 30, cols: int = 100):
        self.rows = rows
        self.cols = cols
        self.cursor_row = 0
        self.cursor_col = 0
        self.grid: List[List[CharCell]] = [
            [CharCell() for _ in range(cols)] for _ in range(rows)
        ]
        self.current_fg = DEFAULT_FG
        self.current_bg = DEFAULT_BG
        self.current_bold = False
        self.current_dim = False
        self.current_underline = False
        self.cursor_visible = True

    def clear(self):
        self.grid = [[CharCell() for _ in range(self.cols)] for _ in range(self.rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.current_fg = DEFAULT_FG
        self.current_bg = DEFAULT_BG
        self.current_bold = False
        self.current_dim = False
        self.current_underline = False

    def load_tmux_pane(self, raw_ansi: str):
        """Loads a full screen snapshot from tmux capture-pane -e."""
        self.clear()
        lines = raw_ansi.split("\n")
        for row_idx, line in enumerate(lines[: self.rows]):
            self.cursor_row = row_idx
            self.cursor_col = 0
            self.write_text(line)

    def write_char(self, char: str):
        if char == "\r":
            self.cursor_col = 0
            return
        if char == "\n":
            self.cursor_col = 0
            if self.cursor_row + 1 < self.rows:
                self.cursor_row += 1
            return
        if char == "\t":
            spaces = 4 - (self.cursor_col % 4)
            for _ in range(spaces):
                self.write_char(" ")
            return

        if self.cursor_col >= self.cols:
            return

        cell = CharCell(
            char=char,
            fg=self.current_fg,
            bg=self.current_bg,
            bold=self.current_bold,
            dim=self.current_dim,
            underline=self.current_underline,
        )
        self.grid[self.cursor_row][self.cursor_col] = cell
        self.cursor_col += 1

    def write_text(self, text: str):
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "\x1b" and i + 1 < n:
                if text[i + 1] == "[":
                    # CSI sequence
                    m = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z$])", text[i:])
                    if m:
                        params_str, cmd = m.groups()
                        self._apply_csi(cmd, params_str)
                        i += len(m.group(0))
                        continue
                elif text[i + 1] == "]" or text[i + 1] == ">" or text[i + 1] == "=":
                    # OSC or mode sequence
                    m = re.match(r"\x1b[\]>=][^\x1b\x07]*[\x1b\x07]?", text[i:])
                    if m:
                        i += len(m.group(0))
                        continue
                i += 1
                continue
            else:
                self.write_char(text[i])
                i += 1

    def _apply_csi(self, cmd: str, params_str: str):
        clean_str = params_str.lstrip("?")
        params = [int(p) for p in clean_str.split(";") if p.isdigit()]

        if cmd == "m":  # SGR
            if not params:
                params = [0]
            idx = 0
            while idx < len(params):
                p = params[idx]
                if p == 0:
                    self.current_fg = DEFAULT_FG
                    self.current_bg = DEFAULT_BG
                    self.current_bold = False
                    self.current_dim = False
                    self.current_underline = False
                elif p == 1:
                    self.current_bold = True
                elif p == 2:
                    self.current_dim = True
                elif p == 4:
                    self.current_underline = True
                elif p == 22:
                    self.current_bold = False
                    self.current_dim = False
                elif p == 24:
                    self.current_underline = False
                elif 30 <= p <= 37:
                    self.current_fg = PALETTE_16[p - 30 + (8 if self.current_bold else 0)]
                elif p == 39:
                    self.current_fg = DEFAULT_FG
                elif 40 <= p <= 47:
                    self.current_bg = PALETTE_16[p - 40]
                elif p == 49:
                    self.current_bg = DEFAULT_BG
                elif 90 <= p <= 97:
                    self.current_fg = PALETTE_16[p - 90 + 8]
                elif 100 <= p <= 107:
                    self.current_bg = PALETTE_16[p - 100 + 8]
                elif p == 38 and idx + 2 < len(params) and params[idx + 1] == 5:
                    self.current_fg = get_256_color(params[idx + 2])
                    idx += 2
                elif p == 48 and idx + 2 < len(params) and params[idx + 1] == 5:
                    self.current_bg = get_256_color(params[idx + 2])
                    idx += 2
                elif p == 38 and idx + 4 < len(params) and params[idx + 1] == 2:
                    self.current_fg = (params[idx + 2] / 255.0, params[idx + 3] / 255.0, params[idx + 4] / 255.0)
                    idx += 4
                elif p == 48 and idx + 4 < len(params) and params[idx + 1] == 2:
                    self.current_bg = (params[idx + 2] / 255.0, params[idx + 3] / 255.0, params[idx + 4] / 255.0)
                    idx += 4
                idx += 1
        elif cmd == "G":  # Cursor Horizontal Absolute
            col = (params[0] - 1) if params else 0
            self.cursor_col = max(0, min(self.cols - 1, col))
        elif cmd == "H" or cmd == "f":  # Cursor position
            r = (params[0] - 1) if (len(params) > 0 and params[0] > 0) else 0
            c = (params[1] - 1) if (len(params) > 1 and params[1] > 0) else 0
            self.cursor_row = max(0, min(self.rows - 1, r))
            self.cursor_col = max(0, min(self.cols - 1, c))
        elif cmd == "K":  # Clear line
            mode = params[0] if params else 0
            if mode == 0:
                for c in range(self.cursor_col, self.cols):
                    self.grid[self.cursor_row][c] = CharCell(bg=self.current_bg)
            elif mode == 1:
                for c in range(0, self.cursor_col + 1):
                    self.grid[self.cursor_row][c] = CharCell(bg=self.current_bg)
            elif mode == 2:
                for c in range(self.cols):
                    self.grid[self.cursor_row][c] = CharCell(bg=self.current_bg)
        elif cmd == "J":  # Clear screen
            if (params and params[0] == 2) or not params:
                self.clear()


class TerminalRenderer:
    """Renders TerminalState into an RGBA/BGRA video frame."""

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        title: str = "Antigravity CLI",
        subtitle: str = "Live Interactive Session",
    ):
        self.width = width
        self.height = height
        self.title = title
        self.subtitle = subtitle

        self.margin_x = 32
        self.margin_y = 24
        self.titlebar_h = 38
        self.statusbar_h = 28

        self.win_x = self.margin_x
        self.win_y = self.margin_y
        self.win_w = self.width - 2 * self.margin_x
        self.win_h = self.height - 2 * self.margin_y

        self.term_x = self.win_x + 16
        self.term_y = self.win_y + self.titlebar_h + 10
        self.term_w = self.win_w - 32
        self.term_h = self.win_h - self.titlebar_h - self.statusbar_h - 20

        self.font_size = 14.5
        self.line_height = 19.5
        self.char_width = 8.5

        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        ctx = cairo.Context(self.surface)
        ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(self.font_size)
        extents = ctx.text_extents("M")
        if extents.x_advance > 0:
            self.char_width = extents.x_advance

        self.cols = int(self.term_w // self.char_width)
        self.rows = int(self.term_h // self.line_height)

    def draw_frame(
        self,
        term: TerminalState,
        banner_card: Optional[Dict[str, str]] = None,
        cursor_pulse: float = 1.0,
    ) -> bytes:
        ctx = cairo.Context(self.surface)

        # 1. Background
        ctx.set_source_rgb(*WINDOW_BG)
        ctx.paint()

        # 2. Window Body
        r = 10.0
        x, y, w, h = self.win_x, self.win_y, self.win_w, self.win_h

        ctx.new_sub_path()
        ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        ctx.close_path()

        ctx.set_source_rgb(*DEFAULT_BG)
        ctx.fill_preserve()

        ctx.set_source_rgba(0.28, 0.32, 0.44, 0.8)
        ctx.set_line_width(1.5)
        ctx.stroke()

        # 3. Titlebar
        ctx.save()
        ctx.new_sub_path()
        ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        ctx.line_to(x + w, y + self.titlebar_h)
        ctx.line_to(x, y + self.titlebar_h)
        ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        ctx.close_path()
        ctx.set_source_rgb(*TITLEBAR_BG)
        ctx.fill()
        ctx.restore()

        ctx.set_source_rgba(0.22, 0.25, 0.34, 0.9)
        ctx.set_line_width(1.0)
        ctx.move_to(x, y + self.titlebar_h)
        ctx.line_to(x + w, y + self.titlebar_h)
        ctx.stroke()

        # Buttons
        dots = [
            (x + 18, y + 19, (0.95, 0.40, 0.40)),
            (x + 36, y + 19, (0.98, 0.75, 0.30)),
            (x + 54, y + 19, (0.40, 0.85, 0.45)),
        ]
        for dot_x, dot_y, color in dots:
            ctx.arc(dot_x, dot_y, 6.0, 0, 2 * math.pi)
            ctx.set_source_rgb(*color)
            ctx.fill()

        # Title
        ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(12.5)
        ctx.set_source_rgb(0.72, 0.76, 0.88)
        title_str = f"⚡ {self.title} — {self.subtitle}"
        ctx.move_to(x + 78, y + 23)
        ctx.show_text(title_str)

        # Status indicator
        ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(11.0)
        ctx.set_source_rgb(0.45, 0.88, 0.60)
        ctx.move_to(x + w - 120, y + 23)
        ctx.show_text("● LIVE TTY")

        # 4. Status Bar
        sb_y = y + h - self.statusbar_h
        ctx.set_source_rgb(*STATUSBAR_BG)
        ctx.rectangle(x + 1, sb_y, w - 2, self.statusbar_h - 1)
        ctx.fill()

        ctx.set_source_rgba(0.22, 0.25, 0.34, 0.9)
        ctx.set_line_width(1.0)
        ctx.move_to(x, sb_y)
        ctx.line_to(x + w, sb_y)
        ctx.stroke()

        ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(11.0)
        ctx.set_source_rgb(0.52, 0.58, 0.70)
        status_txt = "Antigravity CLI v1.1.22 | Real TTY Execution | UTF-8 | gemini-3.7-flash"
        ctx.move_to(x + 16, sb_y + 18)
        ctx.show_text(status_txt)

        # 5. Grid Rendering
        for r_idx in range(min(term.rows, self.rows)):
            row_y = self.term_y + r_idx * self.line_height + self.font_size
            for c_idx in range(min(term.cols, self.cols)):
                cell = term.grid[r_idx][c_idx]
                cell_x = self.term_x + c_idx * self.char_width

                if cell.bg != DEFAULT_BG:
                    ctx.set_source_rgb(*cell.bg)
                    ctx.rectangle(
                        cell_x,
                        row_y - self.font_size + 2,
                        self.char_width + 0.5,
                        self.line_height,
                    )
                    ctx.fill()

                if cell.char and cell.char != " ":
                    weight = cairo.FONT_WEIGHT_BOLD if cell.bold else cairo.FONT_WEIGHT_NORMAL
                    ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, weight)
                    ctx.set_font_size(self.font_size)

                    if cell.dim:
                        ctx.set_source_rgba(cell.fg[0], cell.fg[1], cell.fg[2], 0.5)
                    else:
                        ctx.set_source_rgb(*cell.fg)

                    ctx.move_to(cell_x, row_y)
                    ctx.show_text(cell.char)

                if cell.underline:
                    ctx.set_source_rgb(*cell.fg)
                    ctx.set_line_width(1.0)
                    ctx.move_to(cell_x, row_y + 2)
                    ctx.line_to(cell_x + self.char_width, row_y + 2)
                    ctx.stroke()

        # 6. Card Overlay
        if banner_card:
            self._draw_banner_card(ctx, banner_card)

        return bytes(self.surface.get_data())

    def _draw_banner_card(self, ctx: cairo.Context, card: Dict[str, str]):
        cw, ch = 560, 78
        cx = (self.width - cw) / 2
        cy = self.win_y + self.titlebar_h + 16

        ctx.save()
        r = 8.0
        ctx.new_sub_path()
        ctx.arc(cx + cw - r, cy + r, r, -math.pi / 2, 0)
        ctx.arc(cx + cw - r, cy + ch - r, r, 0, math.pi / 2)
        ctx.arc(cx + r, cy + ch - r, r, math.pi / 2, math.pi)
        ctx.arc(cx + r, cy + r, r, math.pi, 3 * math.pi / 2)
        ctx.close_path()

        ctx.set_source_rgba(0.12, 0.14, 0.22, 0.95)
        ctx.fill_preserve()

        ctx.set_source_rgba(0.40, 0.60, 0.95, 0.8)
        ctx.set_line_width(1.5)
        ctx.stroke()

        tag = card.get("tag", "SECTION")
        ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(10.0)
        ctx.set_source_rgb(0.98, 0.89, 0.69)
        ctx.move_to(cx + 20, cy + 24)
        ctx.show_text(f">>  {tag.upper()}")

        title = card.get("title", "")
        ctx.set_font_size(13.5)
        ctx.set_source_rgb(0.95, 0.96, 1.0)
        ctx.move_to(cx + 20, cy + 46)
        ctx.show_text(title)

        desc = card.get("desc", "")
        if desc:
            ctx.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ctx.set_font_size(10.5)
            ctx.set_source_rgb(0.65, 0.70, 0.82)
            ctx.move_to(cx + 20, cy + 64)
            ctx.show_text(desc)

        ctx.restore()


class TmuxSessionRecorder:
    """Controls a live tmux session, injects keys, and records frames to MP4."""

    def __init__(
        self,
        output_file: str,
        command: str,
        cwd: str,
        session_name: str = "agy_live_rec",
        width: int = 1280,
        height: int = 720,
        fps: int = 25,
        title: str = "Antigravity CLI Workshop",
        subtitle: str = "Live Execution",
    ):
        self.output_file = output_file
        self.command = command
        self.cwd = cwd
        self.session_name = session_name
        self.width = width
        self.height = height
        self.fps = fps
        self.title = title
        self.subtitle = subtitle

        self.renderer = TerminalRenderer(width=width, height=height, title=title, subtitle=subtitle)
        self.term = TerminalState(rows=self.renderer.rows, cols=self.renderer.cols)

        self.ffmpeg_proc: Optional[subprocess.Popen] = None
        self.is_recording = False
        self.capture_thread: Optional[threading.Thread] = None
        self.banner_card: Optional[Dict[str, str]] = None
        self.card_lock = threading.Lock()
        self.frame_count = 0

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    def start(self):
        # Kill any stale session
        subprocess.run(["tmux", "kill-session", "-t", self.session_name], capture_output=True)

        # Start tmux session with exact rows/cols
        cmd = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.session_name,
            "-x",
            str(self.renderer.cols),
            "-y",
            str(self.renderer.rows),
            self.command,
        ]
        subprocess.run(cmd, cwd=self.cwd, check=True)

        # Start FFmpeg process
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{self.width}x{self.height}",
            "-pix_fmt",
            "bgra",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            self.output_file,
        ]
        self.ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        self.is_recording = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print(f"🎬 Started live recording in tmux -> {self.output_file}")

    def _capture_loop(self):
        frame_interval = 1.0 / self.fps
        while self.is_recording:
            start_t = time.time()
            try:
                res = subprocess.run(
                    ["tmux", "capture-pane", "-t", self.session_name, "-p", "-e"],
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0:
                    self.term.load_tmux_pane(res.stdout)

                with self.card_lock:
                    card = self.banner_card

                frame_bytes = self.renderer.draw_frame(self.term, banner_card=card)
                if self.ffmpeg_proc and self.ffmpeg_proc.stdin:
                    self.ffmpeg_proc.stdin.write(frame_bytes)
                    self.frame_count += 1
            except Exception as e:
                pass

            elapsed = time.time() - start_t
            sleep_time = max(0.005, frame_interval - elapsed)
            time.sleep(sleep_time)

    def show_card(self, tag: str, title: str, desc: str = "", duration: float = 2.5):
        with self.card_lock:
            self.banner_card = {"tag": tag, "title": title, "desc": desc}
        time.sleep(duration)
        with self.card_lock:
            self.banner_card = None

    def type_keys(self, text: str, delay_per_char: float = 0.03):
        """Types keys into the live tmux session with realistic cadence."""
        for ch in text:
            subprocess.run(["tmux", "send-keys", "-t", self.session_name, "-l", ch], capture_output=True)
            time.sleep(delay_per_char)

    def send_key(self, key_name: str):
        """Sends special key (Enter, Escape, C-c, C-d, etc.)."""
        subprocess.run(["tmux", "send-keys", "-t", self.session_name, key_name], capture_output=True)

    def resolve_initial_prompts(self, timeout: float = 6.0):
        """Checks and confirms any initial folder trust or theme selection prompts."""
        start_t = time.time()
        while time.time() - start_t < timeout:
            res = subprocess.run(
                ["tmux", "capture-pane", "-t", self.session_name, "-p"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                txt = res.stdout
                if "trust" in txt.lower() and ("yes, i trust" in txt.lower() or "confirm" in txt.lower()):
                    self.send_key("Enter")
                    time.sleep(1.0)
                    return
                elif "? for shortcuts" in txt or ">" in txt:
                    return
            time.sleep(0.3)

    def wait_for_text(self, pattern: str, timeout: float = 30.0, poll_interval: float = 0.3) -> bool:
        start_t = time.time()
        while time.time() - start_t < timeout:
            res = subprocess.run(
                ["tmux", "capture-pane", "-t", self.session_name, "-p"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and re.search(pattern, res.stdout):
                return True
            time.sleep(poll_interval)
        return False

    def wait_for_idle(self, timeout: float = 60.0, poll_interval: float = 0.5) -> bool:
        """Waits until agy completes thinking/generating and returns to idle prompt."""
        start_t = time.time()
        time.sleep(1.5)
        while time.time() - start_t < timeout:
            res = subprocess.run(
                ["tmux", "capture-pane", "-t", self.session_name, "-p"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                txt = res.stdout
                if "? for shortcuts" in txt and not ("Generating..." in txt or "Thinking..." in txt):
                    return True
            time.sleep(poll_interval)
        return False

    def pause(self, seconds: float):
        time.sleep(seconds)

    def close(self):
        self.is_recording = False
        if self.capture_thread:
            self.capture_thread.join(timeout=2.0)

        subprocess.run(["tmux", "kill-session", "-t", self.session_name], capture_output=True)

        if self.ffmpeg_proc:
            if self.ffmpeg_proc.stdin:
                self.ffmpeg_proc.stdin.close()
            self.ffmpeg_proc.communicate()
            self.ffmpeg_proc.wait()

        size = os.path.getsize(self.output_file) if os.path.exists(self.output_file) else 0
        print(f"✅ Real session recorded: {self.output_file} ({self.frame_count} frames, {size / 1024:.1f} KB)")
