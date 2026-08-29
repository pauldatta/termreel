"""
Terminal emulator components: colors, grid state, and ANSI escape parser.
"""

from termreel.emulator.colors import (
    RGBColor,
    RGBAColor,
    ANSIColor,
    ColorPalette,
    parse_hex_color,
    rgb_to_hex,
    get_ansi_color_256,
    truecolor_rgb,
    STANDARD_16_PALETTE,
    DEFAULT_PALETTE,
)
from termreel.emulator.state import CharCell, Cursor, TerminalState
from termreel.emulator.parser import ANSIParser

__all__ = [
    "RGBColor",
    "RGBAColor",
    "ANSIColor",
    "ColorPalette",
    "parse_hex_color",
    "rgb_to_hex",
    "get_ansi_color_256",
    "truecolor_rgb",
    "STANDARD_16_PALETTE",
    "DEFAULT_PALETTE",
    "CharCell",
    "Cursor",
    "TerminalState",
    "ANSIParser",
]
