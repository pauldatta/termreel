"""
Natural human typing simulation, jitter generation, typo injection, and key mapping.
"""

import random
import time
from typing import List, Tuple, Dict, Optional, Generator


# Adjacent keys on standard QWERTY layout for realistic typo generation
QWERTY_NEIGHBORS: Dict[str, str] = {
    "q": "wa", "w": "qase", "e": "wsdr", "r": "edft", "t": "rfgy",
    "y": "tghu", "u": "yhji", "i": "ujko", "o": "iklp", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
}


class KeyMap:
    """Standard terminal key sequence names and mappings."""
    ENTER = "Enter"
    RETURN = "Return"
    ESCAPE = "Escape"
    ESC = "Escape"
    TAB = "Tab"
    BACKSPACE = "Backspace"
    BSPACE = "Backspace"
    SPACE = "Space"
    UP = "Up"
    DOWN = "Down"
    LEFT = "Left"
    RIGHT = "Right"
    HOME = "Home"
    END = "End"
    PAGE_UP = "PageUp"
    PAGE_DOWN = "PageDown"
    CTRL_C = "C-c"
    CTRL_D = "C-d"
    CTRL_O = "C-o"
    CTRL_L = "C-l"
    CTRL_J = "C-j"
    CTRL_U = "C-u"
    CTRL_W = "C-w"
    CTRL_A = "C-a"
    CTRL_E = "C-e"
    CTRL_Z = "C-z"

    # Normalized lookup mappings for tmux and pty
    _TMUX_MAP: Dict[str, str] = {
        "enter": "Enter",
        "return": "Enter",
        "\r": "Enter",
        "\n": "Enter",
        "esc": "Escape",
        "escape": "Escape",
        "\x1b": "Escape",
        "tab": "Tab",
        "\t": "Tab",
        "backspace": "BSpace",
        "bspace": "BSpace",
        "\x7f": "BSpace",
        "space": "Space",
        " ": "Space",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "home": "Home",
        "end": "End",
        "pageup": "PageUp",
        "pgup": "PageUp",
        "pagedown": "PageDown",
        "pgdn": "PageDown",
        "c-c": "C-c",
        "ctrl+c": "C-c",
        "ctrl-c": "C-c",
        "c-d": "C-d",
        "ctrl+d": "C-d",
        "ctrl-d": "C-d",
        "c-o": "C-o",
        "ctrl+o": "C-o",
        "ctrl-o": "C-o",
        "c-l": "C-l",
        "ctrl+l": "C-l",
        "ctrl-l": "C-l",
        "c-z": "C-z",
        "ctrl+z": "C-z",
        "ctrl-z": "C-z",
        "c-j": "C-j",
        "ctrl+j": "C-j",
        "c-u": "C-u",
        "ctrl+u": "C-u",
        "c-w": "C-w",
        "ctrl+w": "C-w",
        "c-a": "C-a",
        "ctrl+a": "C-a",
        "c-e": "C-e",
        "ctrl+e": "C-e",
    }

    _PTY_MAP: Dict[str, str] = {
        "enter": "\r",
        "return": "\r",
        "\r": "\r",
        "\n": "\r",
        "esc": "\x1b",
        "escape": "\x1b",
        "\x1b": "\x1b",
        "tab": "\t",
        "\t": "\t",
        "backspace": "\x7f",
        "bspace": "\x7f",
        "\x7f": "\x7f",
        "space": " ",
        " ": " ",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "right": "\x1b[C",
        "left": "\x1b[D",
        "home": "\x1b[H",
        "end": "\x1b[F",
        "pageup": "\x1b[5~",
        "pgup": "\x1b[5~",
        "pagedown": "\x1b[6~",
        "pgdn": "\x1b[6~",
        "c-c": "\x03",
        "ctrl+c": "\x03",
        "ctrl-c": "\x03",
        "c-d": "\x04",
        "ctrl+d": "\x04",
        "ctrl-d": "\x04",
        "c-o": "\x0f",
        "ctrl+o": "\x0f",
        "ctrl-o": "\x0f",
        "c-l": "\x0c",
        "ctrl+l": "\x0c",
        "ctrl-l": "\x0c",
        "c-z": "\x1a",
        "ctrl+z": "\x1a",
        "ctrl-z": "\x1a",
        "c-j": "\n",
        "ctrl+j": "\n",
        "c-u": "\x15",
        "ctrl+u": "\x15",
        "c-w": "\x17",
        "ctrl+w": "\x17",
        "c-a": "\x01",
        "ctrl+a": "\x01",
        "c-e": "\x05",
        "ctrl+e": "\x05",
    }

    @classmethod
    def to_tmux(cls, key: str) -> str:
        """Map key name or alias to tmux send-keys argument."""
        k = key.strip().lower()
        return cls._TMUX_MAP.get(k, key)

    @classmethod
    def to_pty(cls, key: str) -> str:
        """Map key name or alias to ANSI escape sequence or ASCII character."""
        k = key.strip().lower()
        return cls._PTY_MAP.get(k, key)


class KeystrokeGenerator:
    """
    Simulates realistic human typing cadences with variable micro-delays,
    deliberate typos with backspace corrections, and special key sequences.
    """

    def __init__(
        self,
        base_speed: float = 0.035,
        jitter: float = 0.015,
        typo_rate: float = 0.0,
    ):
        self.base_speed = max(0.001, base_speed)
        self.jitter = max(0.0, jitter)
        self.typo_rate = max(0.0, min(1.0, typo_rate))

    def get_delay(self, char: Optional[str] = None) -> float:
        """Calculate human-like delay for the given character."""
        # Add slight extra delay for punctuation and spaces
        multiplier = 1.0
        if char:
            if char in " \t":
                multiplier = 1.3
            elif char in ".,;:!?()[]{}\"'":
                multiplier = 1.8
            elif char == "\n":
                multiplier = 2.2

        delta = random.uniform(-self.jitter, self.jitter)
        delay = (self.base_speed + delta) * multiplier
        return max(0.005, delay)

    def generate_keystroke_events(self, text: str) -> Generator[Tuple[str, str, float], None, None]:
        """
        Yields (action, value, delay_after) tuples for typing the text.
        action is 'char', 'key', or 'paste'.
        """
        for i, ch in enumerate(text):
            # Check if we should inject a deliberate typo
            if self.typo_rate > 0 and ch.lower() in QWERTY_NEIGHBORS and random.random() < self.typo_rate:
                # Type wrong character
                typo_char = random.choice(QWERTY_NEIGHBORS[ch.lower()])
                if ch.isupper():
                    typo_char = typo_char.upper()

                yield ("char", typo_char, self.get_delay(typo_char))

                # Realization pause before correction
                yield ("pause", "", random.uniform(0.12, 0.25))

                # Backspace
                yield ("key", "Backspace", random.uniform(0.06, 0.12))

                # Type correct character
                yield ("char", ch, self.get_delay(ch))
            else:
                yield ("char", ch, self.get_delay(ch))
