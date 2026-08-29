"""
Color parsing, 256-color palettes, TrueColor (24-bit RGB), and color manipulation.
"""

from typing import Tuple, Optional, Union
from dataclasses import dataclass


RGBColor = Tuple[float, float, float]
RGBAColor = Tuple[float, float, float, float]


def clamp(val: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(min_val, min(max_val, val))


def parse_hex_color(hex_str: str) -> RGBColor:
    """Parse #RRGGBB or #RGB into float RGB (0.0 to 1.0)."""
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join([c * 2 for c in hex_str])
    if len(hex_str) != 6:
        return (1.0, 1.0, 1.0)
    try:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return (clamp(r), clamp(g), clamp(b))
    except ValueError:
        return (1.0, 1.0, 1.0)


def rgb_to_hex(rgb: RGBColor) -> str:
    """Convert float RGB (0.0 to 1.0) to #RRGGBB hex string."""
    r = int(clamp(rgb[0]) * 255.0 + 0.5)
    g = int(clamp(rgb[1]) * 255.0 + 0.5)
    b = int(clamp(rgb[2]) * 255.0 + 0.5)
    return f"#{r:02x}{g:02x}{b:02x}"


# Standard ANSI 16-color palette (Catppuccin Mocha inspired default fallback)
STANDARD_16_PALETTE: Tuple[RGBColor, ...] = (
    # 0-7: Normal
    (0.09, 0.09, 0.14),  # 0: Black
    (0.95, 0.55, 0.66),  # 1: Red
    (0.65, 0.89, 0.63),  # 2: Green
    (0.98, 0.89, 0.69),  # 3: Yellow
    (0.54, 0.71, 0.98),  # 4: Blue
    (0.80, 0.65, 0.97),  # 5: Magenta
    (0.58, 0.89, 0.84),  # 6: Cyan
    (0.80, 0.84, 0.96),  # 7: White (Light Gray)
    # 8-15: Bright
    (0.36, 0.39, 0.49),  # 8: Bright Black (Gray)
    (0.98, 0.60, 0.70),  # 9: Bright Red
    (0.70, 0.95, 0.68),  # 10: Bright Green
    (1.00, 0.92, 0.72),  # 11: Bright Yellow
    (0.60, 0.76, 1.00),  # 12: Bright Blue
    (0.85, 0.70, 1.00),  # 13: Bright Magenta
    (0.65, 0.94, 0.90),  # 14: Bright Cyan
    (1.00, 1.00, 1.00),  # 15: Bright White
)


@dataclass
class ColorPalette:
    """Color palette for ANSI 16 and 256 indexing."""
    palette_16: Tuple[RGBColor, ...] = STANDARD_16_PALETTE

    def get_16_color(self, index: int) -> RGBColor:
        """Get color for ANSI 16 color index (0-15)."""
        idx = max(0, min(15, index))
        return self.palette_16[idx]

    def get_256_color(self, index: int) -> RGBColor:
        """
        Get color for 256-color ANSI index (0-255).
        - 0-15: standard & bright colors
        - 16-231: 6x6x6 RGB cube
        - 232-255: 24-step grayscale ramp
        """
        if 0 <= index < 16:
            return self.get_16_color(index)
        elif 16 <= index <= 231:
            idx = index - 16
            b_idx = idx % 6
            g_idx = (idx // 6) % 6
            r_idx = idx // 36
            # ANSI 6x6x6 color levels: [0, 95, 135, 175, 215, 255] / 255
            levels = [0.0, 95.0 / 255.0, 135.0 / 255.0, 175.0 / 255.0, 215.0 / 255.0, 1.0]
            return (levels[r_idx], levels[g_idx], levels[b_idx])
        elif 232 <= index <= 255:
            # Grayscale ramp: 24 steps from (8 + (index - 232) * 10) / 255
            val = (8.0 + (index - 232) * 10.0) / 255.0
            val = clamp(val)
            return (val, val, val)
        return (0.85, 0.88, 0.96)


DEFAULT_PALETTE = ColorPalette()


def get_ansi_color_256(index: int, palette: Optional[ColorPalette] = None) -> RGBColor:
    p = palette or DEFAULT_PALETTE
    return p.get_256_color(index)


def truecolor_rgb(r: int, g: int, b: int) -> RGBColor:
    """Convert integer RGB (0-255) to float RGB (0.0-1.0)."""
    return (clamp(r / 255.0), clamp(g / 255.0), clamp(b / 255.0))


def blend_colors(c1: RGBColor, c2: RGBColor, factor: float) -> RGBColor:
    """Linear interpolation between c1 and c2 (factor 0.0 -> c1, 1.0 -> c2)."""
    f = clamp(factor)
    return (
        c1[0] * (1.0 - f) + c2[0] * f,
        c1[1] * (1.0 - f) + c2[1] * f,
        c1[2] * (1.0 - f) + c2[2] * f,
    )


def adjust_brightness(c: RGBColor, factor: float) -> RGBColor:
    """Multiply color channels by brightness factor."""
    return (clamp(c[0] * factor), clamp(c[1] * factor), clamp(c[2] * factor))


class ANSIColor:
    """Helper namespace for color utilities."""
    parse_hex = staticmethod(parse_hex_color)
    to_hex = staticmethod(rgb_to_hex)
    get_256 = staticmethod(get_ansi_color_256)
    truecolor = staticmethod(truecolor_rgb)
    blend = staticmethod(blend_colors)
    brightness = staticmethod(adjust_brightness)
