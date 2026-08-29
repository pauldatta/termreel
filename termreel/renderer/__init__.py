"""
Vector frame renderer, themes, window chrome, and chapter cards.
"""

from termreel.renderer.themes import (
    Theme,
    get_theme,
    list_themes,
    THEMES,
    CATPPUCCIN_MOCHA,
    DRACULA,
    TOKYO_NIGHT,
    NORD,
    ONE_DARK,
    MONOKAI,
    GITHUB_DARK,
    MATRIX,
    CATPPUCCIN_LATTE,
)
from termreel.renderer.chrome import ChromeRenderer
from termreel.renderer.cards import CardRenderer
from termreel.renderer.cairo_renderer import CairoTerminalRenderer

__all__ = [
    "Theme",
    "get_theme",
    "list_themes",
    "THEMES",
    "CATPPUCCIN_MOCHA",
    "DRACULA",
    "TOKYO_NIGHT",
    "NORD",
    "ONE_DARK",
    "MONOKAI",
    "GITHUB_DARK",
    "MATRIX",
    "CATPPUCCIN_LATTE",
    "ChromeRenderer",
    "CardRenderer",
    "CairoTerminalRenderer",
]
