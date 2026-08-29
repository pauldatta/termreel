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
    TAB = "Tab"
    BACKSPACE = "Backspace"
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
