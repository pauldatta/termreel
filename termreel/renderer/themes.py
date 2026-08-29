"""
Color themes and palette configurations for terminal window chrome and text rendering.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union
from termreel.emulator.colors import RGBColor, RGBAColor, ColorPalette, parse_hex_color


@dataclass
class Theme:
    """Complete visual styling theme for window chrome, status bar, cards, and text."""
    name: str
    window_bg: RGBColor
    terminal_bg: RGBColor
    default_fg: RGBColor
    titlebar_bg: RGBColor
    statusbar_bg: RGBColor
    border_color: RGBAColor
    titlebar_border: RGBAColor
    title_text_color: RGBColor
    statusbar_text_color: RGBColor
    accent_color: RGBColor
    card_bg: RGBAColor
    card_border: RGBAColor
    card_tag_color: RGBColor
    card_title_color: RGBColor
    card_desc_color: RGBColor
    traffic_close: RGBColor = (0.95, 0.40, 0.40)
    traffic_minimize: RGBColor = (0.98, 0.75, 0.30)
    traffic_maximize: RGBColor = (0.40, 0.85, 0.45)
    palette: ColorPalette = field(default_factory=ColorPalette)


# ───────────────────────────────────────────────────────────
# Built-in Theme Definitions
# ───────────────────────────────────────────────────────────

def _hex_rgb(h: str) -> RGBColor:
    return parse_hex_color(h)

def _hex_rgba(h: str, a: float = 1.0) -> RGBAColor:
    r, g, b = parse_hex_color(h)
    return (r, g, b, a)

def _build_palette(colors: List[str]) -> ColorPalette:
    p16 = tuple(_hex_rgb(c) for c in colors[:16])
    return ColorPalette(palette_16=p16)


# 1. Catppuccin Mocha (Default)
CATPPUCCIN_MOCHA = Theme(
    name="catppuccin-mocha",
    window_bg=_hex_rgb("#11111b"),
    terminal_bg=_hex_rgb("#1e1e2e"),
    default_fg=_hex_rgb("#cdd6f4"),
    titlebar_bg=_hex_rgb("#181825"),
    statusbar_bg=_hex_rgb("#181825"),
    border_color=_hex_rgba("#45475a", 0.7),
    titlebar_border=_hex_rgba("#313244", 0.8),
    title_text_color=_hex_rgb("#bac2de"),
    statusbar_text_color=_hex_rgb("#a6adc8"),
    accent_color=_hex_rgb("#89b4fa"),
    card_bg=_hex_rgba("#181825", 0.95),
    card_border=_hex_rgba("#89b4fa", 0.8),
    card_tag_color=_hex_rgb("#f9e2af"),
    card_title_color=_hex_rgb("#cdd6f4"),
    card_desc_color=_hex_rgb("#a6adc8"),
    palette=_build_palette([
        "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
        "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
    ]),
)

# 2. Dracula
DRACULA = Theme(
    name="dracula",
    window_bg=_hex_rgb("#191a21"),
    terminal_bg=_hex_rgb("#282a36"),
    default_fg=_hex_rgb("#f8f8f2"),
    titlebar_bg=_hex_rgb("#21222c"),
    statusbar_bg=_hex_rgb("#21222c"),
    border_color=_hex_rgba("#6272a4", 0.6),
    titlebar_border=_hex_rgba("#44475a", 0.8),
    title_text_color=_hex_rgb("#f8f8f2"),
    statusbar_text_color=_hex_rgb("#6272a4"),
    accent_color=_hex_rgb("#bd93f9"),
    card_bg=_hex_rgba("#21222c", 0.95),
    card_border=_hex_rgba("#bd93f9", 0.8),
    card_tag_color=_hex_rgb("#f1fa8c"),
    card_title_color=_hex_rgb("#f8f8f2"),
    card_desc_color=_hex_rgb("#6272a4"),
    palette=_build_palette([
        "#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
        "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff",
    ]),
)

# 3. Tokyo Night
TOKYO_NIGHT = Theme(
    name="tokyo-night",
    window_bg=_hex_rgb("#16161e"),
    terminal_bg=_hex_rgb("#1a1b26"),
    default_fg=_hex_rgb("#c0caf5"),
    titlebar_bg=_hex_rgb("#1f2335"),
    statusbar_bg=_hex_rgb("#1f2335"),
    border_color=_hex_rgba("#292e42", 0.8),
    titlebar_border=_hex_rgba("#292e42", 0.9),
    title_text_color=_hex_rgb("#a9b1d6"),
    statusbar_text_color=_hex_rgb("#7aa2f7"),
    accent_color=_hex_rgb("#7aa2f7"),
    card_bg=_hex_rgba("#1f2335", 0.95),
    card_border=_hex_rgba("#7aa2f7", 0.8),
    card_tag_color=_hex_rgb("#e0af68"),
    card_title_color=_hex_rgb("#c0caf5"),
    card_desc_color=_hex_rgb("#a9b1d6"),
    palette=_build_palette([
        "#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
        "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5",
    ]),
)

# 4. Nord
NORD = Theme(
    name="nord",
    window_bg=_hex_rgb("#242933"),
    terminal_bg=_hex_rgb("#2e3440"),
    default_fg=_hex_rgb("#d8dee9"),
    titlebar_bg=_hex_rgb("#3b4252"),
    statusbar_bg=_hex_rgb("#3b4252"),
    border_color=_hex_rgba("#4c566a", 0.7),
    titlebar_border=_hex_rgba("#4c566a", 0.8),
    title_text_color=_hex_rgb("#eceff4"),
    statusbar_text_color=_hex_rgb("#88c0d0"),
    accent_color=_hex_rgb("#88c0d0"),
    card_bg=_hex_rgba("#3b4252", 0.95),
    card_border=_hex_rgba("#88c0d0", 0.8),
    card_tag_color=_hex_rgb("#ebcb8b"),
    card_title_color=_hex_rgb("#eceff4"),
    card_desc_color=_hex_rgb("#d8dee9"),
    palette=_build_palette([
        "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
        "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4",
    ]),
)

# 5. One Dark
ONE_DARK = Theme(
    name="one-dark",
    window_bg=_hex_rgb("#1e1e24"),
    terminal_bg=_hex_rgb("#282c34"),
    default_fg=_hex_rgb("#abb2bf"),
    titlebar_bg=_hex_rgb("#21252b"),
    statusbar_bg=_hex_rgb("#21252b"),
    border_color=_hex_rgba("#3e4451", 0.7),
    titlebar_border=_hex_rgba("#3e4451", 0.8),
    title_text_color=_hex_rgb("#d7dae0"),
    statusbar_text_color=_hex_rgb("#5c6370"),
    accent_color=_hex_rgb("#61afef"),
    card_bg=_hex_rgba("#21252b", 0.95),
    card_border=_hex_rgba("#61afef", 0.8),
    card_tag_color=_hex_rgb("#e5c07b"),
    card_title_color=_hex_rgb("#d7dae0"),
    card_desc_color=_hex_rgb("#abb2bf"),
    palette=_build_palette([
        "#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
        "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff",
    ]),
)

# 6. Monokai
MONOKAI = Theme(
    name="monokai",
    window_bg=_hex_rgb("#1b1c18"),
    terminal_bg=_hex_rgb("#272822"),
    default_fg=_hex_rgb("#f8f8f2"),
    titlebar_bg=_hex_rgb("#1e1f1c"),
    statusbar_bg=_hex_rgb("#1e1f1c"),
    border_color=_hex_rgba("#75715e", 0.6),
    titlebar_border=_hex_rgba("#75715e", 0.7),
    title_text_color=_hex_rgb("#f8f8f2"),
    statusbar_text_color=_hex_rgb("#a6e22e"),
    accent_color=_hex_rgb("#a6e22e"),
    card_bg=_hex_rgba("#1e1f1c", 0.95),
    card_border=_hex_rgba("#a6e22e", 0.8),
    card_tag_color=_hex_rgb("#e6db74"),
    card_title_color=_hex_rgb("#f8f8f2"),
    card_desc_color=_hex_rgb("#75715e"),
    palette=_build_palette([
        "#272822", "#f92672", "#a6e22e", "#e6db74", "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
        "#75715e", "#f92672", "#a6e22e", "#e6db74", "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5",
    ]),
)

# 7. GitHub Dark
GITHUB_DARK = Theme(
    name="github-dark",
    window_bg=_hex_rgb("#090d13"),
    terminal_bg=_hex_rgb("#0d1117"),
    default_fg=_hex_rgb("#c9d1d9"),
    titlebar_bg=_hex_rgb("#161b22"),
    statusbar_bg=_hex_rgb("#161b22"),
    border_color=_hex_rgba("#30363d", 0.8),
    titlebar_border=_hex_rgba("#30363d", 0.9),
    title_text_color=_hex_rgb("#f0f6fc"),
    statusbar_text_color=_hex_rgb("#8b949e"),
    accent_color=_hex_rgb("#58a6ff"),
    card_bg=_hex_rgba("#161b22", 0.95),
    card_border=_hex_rgba("#58a6ff", 0.8),
    card_tag_color=_hex_rgb("#d29922"),
    card_title_color=_hex_rgb("#f0f6fc"),
    card_desc_color=_hex_rgb("#8b949e"),
    palette=_build_palette([
        "#0d1117", "#ff7b72", "#7ee787", "#d29922", "#79c0ff", "#d2a8ff", "#56d4dd", "#c9d1d9",
        "#8b949e", "#ffa198", "#56d364", "#e3b341", "#58a6ff", "#bc8cff", "#39c5cf", "#f0f6fc",
    ]),
)

# 8. Matrix
MATRIX = Theme(
    name="matrix",
    window_bg=_hex_rgb("#040d06"),
    terminal_bg=_hex_rgb("#0a150c"),
    default_fg=_hex_rgb("#55ff55"),
    titlebar_bg=_hex_rgb("#071a0b"),
    statusbar_bg=_hex_rgb("#071a0b"),
    border_color=_hex_rgba("#00ff41", 0.5),
    titlebar_border=_hex_rgba("#00ff41", 0.6),
    title_text_color=_hex_rgb("#00ff41"),
    statusbar_text_color=_hex_rgb("#008f11"),
    accent_color=_hex_rgb("#00ff41"),
    card_bg=_hex_rgba("#071a0b", 0.95),
    card_border=_hex_rgba("#00ff41", 0.8),
    card_tag_color=_hex_rgb("#00ff41"),
    card_title_color=_hex_rgb("#55ff55"),
    card_desc_color=_hex_rgb("#008f11"),
    palette=_build_palette([
        "#000000", "#008f11", "#00ff41", "#55ff55", "#003b00", "#00ff41", "#008f11", "#55ff55",
        "#003b00", "#008f11", "#00ff41", "#55ff55", "#003b00", "#00ff41", "#008f11", "#ffffff",
    ]),
)

# 9. Catppuccin Latte (Light Theme)
CATPPUCCIN_LATTE = Theme(
    name="catppuccin-latte",
    window_bg=_hex_rgb("#dce0e8"),
    terminal_bg=_hex_rgb("#eff1f5"),
    default_fg=_hex_rgb("#4c4f69"),
    titlebar_bg=_hex_rgb("#e6e9ef"),
    statusbar_bg=_hex_rgb("#e6e9ef"),
    border_color=_hex_rgba("#acb0be", 0.7),
    titlebar_border=_hex_rgba("#bcc0cc", 0.8),
    title_text_color=_hex_rgb("#4c4f69"),
    statusbar_text_color=_hex_rgb("#6c6f85"),
    accent_color=_hex_rgb("#1e66f5"),
    card_bg=_hex_rgba("#e6e9ef", 0.95),
    card_border=_hex_rgba("#1e66f5", 0.8),
    card_tag_color=_hex_rgb("#df8e1d"),
    card_title_color=_hex_rgb("#4c4f69"),
    card_desc_color=_hex_rgb("#6c6f85"),
    palette=_build_palette([
        "#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#acb0be",
        "#6c6f85", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#4c4f69",
    ]),
)


THEMES: Dict[str, Theme] = {
    "catppuccin-mocha": CATPPUCCIN_MOCHA,
    "dracula": DRACULA,
    "tokyo-night": TOKYO_NIGHT,
    "nord": NORD,
    "one-dark": ONE_DARK,
    "monokai": MONOKAI,
    "github-dark": GITHUB_DARK,
    "matrix": MATRIX,
    "catppuccin-latte": CATPPUCCIN_LATTE,
}


def get_theme(name_or_theme: Union[str, Theme]) -> Theme:
    """Retrieve theme by name, or return theme object directly."""
    if isinstance(name_or_theme, Theme):
        return name_or_theme
    normalized = str(name_or_theme).lower().strip()
    return THEMES.get(normalized, CATPPUCCIN_MOCHA)


def list_themes() -> List[str]:
    """Return list of all available theme names."""
    return list(THEMES.keys())
