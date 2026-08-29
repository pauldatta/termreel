"""
High-performance PyCairo vector terminal frame renderer.
"""

import math
import threading
from typing import Optional, Dict, Tuple, Any, Union
import cairo
from termreel.emulator.state import TerminalState, CharCell

from termreel.renderer.themes import Theme, get_theme
from termreel.renderer.chrome import ChromeRenderer
from termreel.renderer.cards import CardRenderer


class CairoTerminalRenderer:
    """
    Renders 2D TerminalState grids into high-fidelity raster frames with custom
    window chrome, theme palettes, typography, and card overlays.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        title: str = "TermReel",
        subtitle: str = "Interactive Session",
        theme: Union[str, Theme] = "catppuccin-mocha",
        font_family: str = "DejaVu Sans Mono",
        font_size: float = 14.5,
        margin_x: int = 32,
        margin_y: int = 24,
        titlebar_height: int = 38,
        statusbar_height: int = 28,
    ):
        self.width = width
        self.height = height
        self.title = title
        self.subtitle = subtitle
        self.theme: Theme = get_theme(theme)
        self.font_family = font_family
        self.font_size = font_size

        self.margin_x = margin_x
        self.margin_y = margin_y
        self.titlebar_h = titlebar_height
        self.statusbar_h = statusbar_height

        self.win_x = float(self.margin_x)
        self.win_y = float(self.margin_y)
        self.win_w = float(self.width - 2 * self.margin_x)
        self.win_h = float(self.height - 2 * self.margin_y)

        # Terminal content inner bounding box
        self.term_padding_x = 16.0
        self.term_padding_y = 10.0
        self.term_x = self.win_x + self.term_padding_x
        self.term_y = self.win_y + self.titlebar_h + self.term_padding_y
        self.term_w = self.win_w - (2 * self.term_padding_x)
        self.term_h = self.win_h - self.titlebar_h - self.statusbar_h - (2 * self.term_padding_y)

        # Measure monospace cell dimensions with PyCairo
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        ctx = cairo.Context(self.surface)
        ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(self.font_size)

        extents = ctx.text_extents("M")
        self.char_width = extents.x_advance if extents.x_advance > 0 else (self.font_size * 0.6)
        self.line_height = self.font_size * 1.35

        # Calculate exact rows and columns that fit inside inner area
        self.cols = max(10, int(self.term_w // self.char_width))
        self.rows = max(5, int(self.term_h // self.line_height))

        self.chrome = ChromeRenderer(font_family=self.font_family)
        self.cards = CardRenderer(font_family=self.font_family)

    def set_theme(self, theme: Union[str, Theme]):
        """Dynamically update the visual theme."""
        self.theme = get_theme(theme)

    def draw_frame(
        self,
        term_state: TerminalState,
        banner_card: Optional[Dict[str, Any]] = None,
        status_left: Optional[str] = None,
        status_right: Optional[str] = None,
        status_pill: str = "● LIVE TTY",
        cursor_pulse: float = 1.0,
    ) -> bytes:
        """
        Renders a full frame and returns raw BGRA/ARGB byte buffer.
        """
        ctx = cairo.Context(self.surface)

        # 1. Outer background wallpaper & window container
        self.chrome.draw_window_background(
            ctx=ctx,
            theme=self.theme,
            screen_w=self.width,
            screen_h=self.height,
            win_x=self.win_x,
            win_y=self.win_y,
            win_w=self.win_w,
            win_h=self.win_h,
        )

        # 2. Window Titlebar
        self.chrome.draw_titlebar(
            ctx=ctx,
            theme=self.theme,
            win_x=self.win_x,
            win_y=self.win_y,
            win_w=self.win_w,
            titlebar_h=self.titlebar_h,
            title=self.title,
            subtitle=self.subtitle,
            status_text=status_pill,
        )

        # 3. Bottom Statusbar
        default_status_left = f"{self.title} | {self.cols}x{self.rows} | UTF-8"
        self.chrome.draw_statusbar(
            ctx=ctx,
            theme=self.theme,
            win_x=self.win_x,
            win_y=self.win_y,
            win_w=self.win_w,
            win_h=self.win_h,
            statusbar_h=self.statusbar_h,
            left_text=status_left or default_status_left,
            right_text=status_right or "TermReel HD",
        )

        # 4. Render 2D Grid Cells & Cursor under state lock (Batched Run Optimization)
        with getattr(term_state, "_lock", threading.RLock()):
            max_r = min(term_state.rows, self.rows)
            max_c = min(term_state.cols, self.cols)

            for r_idx in range(max_r):
                row_cells = term_state.grid[r_idx]
                row_y = self.term_y + (r_idx * self.line_height) + self.font_size

                c_idx = 0
                while c_idx < max_c:
                    cell: CharCell = row_cells[c_idx]
                    eff_bg = cell.effective_bg
                    eff_fg = cell.effective_fg
                    bold = cell.bold
                    italic = cell.italic
                    dim = cell.dim
                    underline = cell.underline
                    strikethrough = cell.strikethrough
                    hidden = cell.hidden

                    start_c = c_idx
                    chars = [cell.char if (cell.char and not hidden) else " "]
                    c_idx += 1

                    while c_idx < max_c:
                        next_cell: CharCell = row_cells[c_idx]
                        if (
                            next_cell.effective_bg == eff_bg
                            and next_cell.effective_fg == eff_fg
                            and next_cell.bold == bold
                            and next_cell.italic == italic
                            and next_cell.dim == dim
                            and next_cell.underline == underline
                            and next_cell.strikethrough == strikethrough
                            and next_cell.hidden == hidden
                        ):
                            chars.append(next_cell.char if (next_cell.char and not hidden) else " ")
                            c_idx += 1
                        else:
                            break

                    run_length = c_idx - start_c
                    start_x = self.term_x + (start_c * self.char_width)
                    run_width = run_length * self.char_width

                    # Render custom cell background rectangle for entire run
                    if eff_bg != self.theme.terminal_bg and eff_bg != (0.10, 0.10, 0.15):
                        ctx.set_source_rgb(*eff_bg)
                        ctx.rectangle(
                            start_x,
                            row_y - self.font_size + 2.0,
                            run_width + 0.5,
                            self.line_height,
                        )
                        ctx.fill()

                    run_text = "".join(chars)
                    if run_text.strip():
                        weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL
                        slant = cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL
                        ctx.select_font_face(self.font_family, slant, weight)
                        ctx.set_font_size(self.font_size)

                        if dim:
                            ctx.set_source_rgba(eff_fg[0], eff_fg[1], eff_fg[2], 0.5)
                        else:
                            ctx.set_source_rgb(*eff_fg)

                        ctx.move_to(start_x, row_y)
                        ctx.show_text(run_text)

                    # Underline
                    if underline:
                        ctx.set_source_rgb(*eff_fg)
                        ctx.set_line_width(1.2)
                        ctx.move_to(start_x, row_y + 2.5)
                        ctx.line_to(start_x + run_width, row_y + 2.5)
                        ctx.stroke()

                    # Strikethrough
                    if strikethrough:
                        ctx.set_source_rgb(*eff_fg)
                        ctx.set_line_width(1.2)
                        ctx.move_to(start_x, row_y - (self.font_size * 0.35))
                        ctx.line_to(start_x + run_width, row_y - (self.font_size * 0.35))
                        ctx.stroke()


            # 5. Cursor Rendering
            if term_state.cursor_visible and cursor_pulse > 0.05:
                cur_r = term_state.cursor_row
                cur_c = term_state.cursor_col
                if cur_r < self.rows and cur_c < self.cols:
                    cur_x = self.term_x + (cur_c * self.char_width)
                    cur_y = self.term_y + (cur_r * self.line_height) + 2.0
                    ctx.set_source_rgba(
                        self.theme.accent_color[0],
                        self.theme.accent_color[1],
                        self.theme.accent_color[2],
                        0.75 * cursor_pulse,
                    )
                    ctx.rectangle(cur_x, cur_y, self.char_width, self.line_height - 2.0)
                    ctx.fill()


        # 6. Overlay Card (if active)
        if banner_card:
            self.cards.draw_card(
                ctx=ctx,
                card_data=banner_card,
                theme=self.theme,
                screen_width=self.width,
                screen_height=self.height,
                window_y=self.win_y,
                titlebar_h=self.titlebar_h,
            )

        return bytes(self.surface.get_data())
