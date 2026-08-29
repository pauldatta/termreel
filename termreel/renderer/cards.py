"""
Chapter card and overlay announcement rendering using PyCairo.
"""

import math
from typing import Dict, Optional, Any
import cairo
from termreel.renderer.themes import Theme


def draw_rounded_rect(ctx: cairo.Context, x: float, y: float, w: float, h: float, r: float):
    """Draw a rectangle with rounded corners."""
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


class CardRenderer:
    """Renders floating chapter cards, lesson objectives, and alert overlays."""

    def __init__(self, font_family: str = "DejaVu Sans Mono"):
        self.font_family = font_family

    def draw_card(
        self,
        ctx: cairo.Context,
        card_data: Dict[str, Any],
        theme: Theme,
        screen_width: int,
        screen_height: int,
        window_y: float,
        titlebar_h: float,
    ):
        """Draw an elegant chapter card overlay."""
        tag = str(card_data.get("tag", "CHAPTER")).upper()
        title = str(card_data.get("title", ""))
        desc = str(card_data.get("desc", ""))

        cw = min(680.0, screen_width * 0.75)
        ch = 88.0 if desc else 64.0
        cx = (screen_width - cw) / 2.0
        cy = window_y + titlebar_h + 20.0

        ctx.save()

        # Subtle card shadow
        ctx.set_source_rgba(0.0, 0.0, 0.0, 0.45)
        draw_rounded_rect(ctx, cx + 2, cy + 3, cw, ch, 10.0)
        ctx.fill()

        # Card body background
        draw_rounded_rect(ctx, cx, cy, cw, ch, 10.0)
        ctx.set_source_rgba(*theme.card_bg)
        ctx.fill_preserve()

        # Card accent border
        ctx.set_source_rgba(*theme.card_border)
        ctx.set_line_width(1.5)
        ctx.stroke()

        # Tag indicator pill (e.g. >> MODULE 1)
        ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(10.5)
        ctx.set_source_rgb(*theme.card_tag_color)
        ctx.move_to(cx + 22.0, cy + 24.0)
        ctx.show_text(f">>  {tag}")

        # Card Title
        ctx.set_font_size(14.5)
        ctx.set_source_rgb(*theme.card_title_color)
        ctx.move_to(cx + 22.0, cy + 48.0)
        ctx.show_text(title)

        # Card Description (if present)
        if desc:
            ctx.select_font_face(self.font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ctx.set_font_size(11.5)
            ctx.set_source_rgb(*theme.card_desc_color)
            ctx.move_to(cx + 22.0, cy + 70.0)
            ctx.show_text(desc)

        ctx.restore()
