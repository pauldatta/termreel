"""
Terminal window frame chrome, macOS traffic lights, titlebar, and status bar rendering.
"""

import math
from typing import Optional, Dict, Any
import cairo
from termreel.renderer.themes import Theme
from termreel.renderer.cards import draw_rounded_rect


class ChromeRenderer:
    """
    Renders outer window frame, titlebar, window buttons, live status pills, and bottom statusbar.
    """

    def __init__(self, font_family: str = "DejaVu Sans Mono"):
        self.font_family = font_family

    def draw_window_background(
        self,
        ctx: cairo.Context,
        theme: Theme,
        screen_w: int,
        screen_h: int,
        win_x: float,
        win_y: float,
        win_w: float,
        win_h: float,
        corner_radius: float = 10.0,
    ):
        """Draw wallpaper background and main window frame container."""
        # 1. Canvas / Screen background
        ctx.set_source_rgb(*theme.window_bg)
        ctx.paint()

        # 2. Window Drop Shadow
        ctx.save()
        ctx.set_source_rgba(0.0, 0.0, 0.0, 0.5)
        draw_rounded_rect(ctx, win_x + 4, win_y + 6, win_w, win_h, corner_radius)
        ctx.fill()
        ctx.restore()

        # 3. Main Window Body
        draw_rounded_rect(ctx, win_x, win_y, win_w, win_h, corner_radius)
        ctx.set_source_rgb(*theme.terminal_bg)
        ctx.fill_preserve()

        # 4. Outer Border
        ctx.set_source_rgba(*theme.border_color)
        ctx.set_line_width(1.5)
        ctx.stroke()

    def draw_titlebar(
        self,
        ctx: cairo.Context,
        theme: Theme,
        win_x: float,
        win_y: float,
        win_w: float,
        titlebar_h: float,
        title: str,
        subtitle: str,
        status_text: str = "● LIVE TTY",
        corner_radius: float = 10.0,
    ):
        """Draw top titlebar with macOS buttons, title text, and status badge."""
        r = corner_radius
        ctx.save()

        # Top rounded corners clipping for titlebar
        ctx.new_sub_path()
        ctx.arc(win_x + win_w - r, win_y + r, r, -math.pi / 2, 0)
        ctx.line_to(win_x + win_w, win_y + titlebar_h)
        ctx.line_to(win_x, win_y + titlebar_h)
        ctx.arc(win_x + r, win_y + r, r, math.pi, 3 * math.pi / 2)
        ctx.close_path()

        ctx.set_source_rgb(*theme.titlebar_bg)
        ctx.fill()
        ctx.restore()

        # Separator line under titlebar
        ctx.set_source_rgba(*theme.titlebar_border)
        ctx.set_line_width(1.0)
        ctx.move_to(win_x, win_y + titlebar_h)
        ctx.line_to(win_x + win_w, win_y + titlebar_h)
        ctx.stroke()

        # Traffic light buttons (Close, Minimize, Maximize)
        btn_y = win_y + (titlebar_h / 2.0)
        buttons = [
            (win_x + 18.0, btn_y, theme.traffic_close),
            (win_x + 36.0, btn_y, theme.traffic_minimize),
            (win_x + 54.0, btn_y, theme.traffic_maximize),
        ]
        for bx, by, col in buttons:
            ctx.arc(bx, by, 5.5, 0, 2 * math.pi)
            ctx.set_source_rgb(*col)
            ctx.fill()

        # Window Title & Subtitle
        ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(12.5)
        ctx.set_source_rgb(*theme.title_text_color)
        full_title = f"{title}"
        if subtitle:
            full_title += f"  —  {subtitle}"
        ctx.move_to(win_x + 78.0, win_y + (titlebar_h / 2.0) + 4.5)
        ctx.show_text(full_title)

        # Status badge indicator (e.g. ● LIVE TTY)
        if status_text:
            ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ctx.set_font_size(11.0)
            ctx.set_source_rgb(*theme.accent_color)
            extents = ctx.text_extents(status_text)
            ctx.move_to(win_x + win_w - extents.width - 24.0, win_y + (titlebar_h / 2.0) + 4.0)
            ctx.show_text(status_text)

    def draw_statusbar(
        self,
        ctx: cairo.Context,
        theme: Theme,
        win_x: float,
        win_y: float,
        win_w: float,
        win_h: float,
        statusbar_h: float,
        left_text: str,
        right_text: str = "UTF-8 | TermReel",
        corner_radius: float = 10.0,
    ):
        """Draw bottom status bar with session metadata."""
        sb_y = win_y + win_h - statusbar_h
        r = corner_radius

        ctx.save()
        # Bottom rounded corners clipping for status bar
        ctx.new_sub_path()
        ctx.move_to(win_x, sb_y)
        ctx.line_to(win_x + win_w, sb_y)
        ctx.arc(win_x + win_w - r, win_y + win_h - r, r, 0, math.pi / 2)
        ctx.arc(win_x + r, win_y + win_h - r, r, math.pi / 2, math.pi)
        ctx.close_path()

        ctx.set_source_rgb(*theme.statusbar_bg)
        ctx.fill()
        ctx.restore()

        # Separator line above statusbar
        ctx.set_source_rgba(*theme.titlebar_border)
        ctx.set_line_width(1.0)
        ctx.move_to(win_x, sb_y)
        ctx.line_to(win_x + win_w, sb_y)
        ctx.stroke()

        # Left status text
        ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(10.5)
        ctx.set_source_rgb(*theme.statusbar_text_color)
        ctx.move_to(win_x + 16.0, sb_y + (statusbar_h / 2.0) + 4.0)
        ctx.show_text(left_text)

        # Right status text
        if right_text:
            extents = ctx.text_extents(right_text)
            ctx.move_to(win_x + win_w - extents.width - 16.0, sb_y + (statusbar_h / 2.0) + 4.0)
            ctx.show_text(right_text)
