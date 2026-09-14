"""
Natural human typing simulation, jitter generation, typo injection, and key mapping.
"""

import random
import re
import time
from typing import List, Tuple, Dict, Optional, Generator

from termreel.exceptions import KeySpecError


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


    @classmethod
    def to_tmux(cls, key: str) -> str:
        """Map key name or alias to tmux send-keys argument."""
        k = key.strip().lower()
        return cls._TMUX_MAP.get(k, key)

    @classmethod
    def to_pty(cls, key: str) -> str:
        """
        Map a key specification to the bytes a terminal actually sends.

        Raises KeySpecError for anything unrecognised. See parse_key_spec.
        """
        return parse_key_spec(key)


# Control characters reachable via Ctrl that are not plain letters.
# Ctrl masks off the top three bits, which is exactly `ord(c) & 0x1f` for the
# ASCII range 0x40-0x5f, so these are the punctuation members of that range.
_CTRL_PUNCTUATION: Dict[str, str] = {
    "@": "\x00",
    "space": "\x00",
    "[": "\x1b",
    "\\": "\x1c",
    "]": "\x1d",
    "^": "\x1e",
    "_": "\x1f",
    # Not an & 0x1f result: terminals send DEL for Ctrl-?.
    "?": "\x7f",
}

# Specs that name a byte sequence directly rather than a Ctrl combination.
_NAMED_KEYS: Dict[str, str] = {
    "enter": "\r",
    "return": "\r",
    "esc": "\x1b",
    "escape": "\x1b",
    "tab": "\t",
    "backspace": "\x7f",
    "bspace": "\x7f",
    "space": " ",
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
}

# Raw byte values accepted verbatim, before any whitespace stripping.
_LITERAL_KEYS: Dict[str, str] = {
    "\r": "\r",
    "\n": "\r",
    "\x1b": "\x1b",
    "\t": "\t",
    "\x7f": "\x7f",
    " ": " ",
}

_HEX_SPEC = re.compile(r"^0x([0-9a-f]{1,2})$")
_CTRL_SPEC = re.compile(r"^(?:c|ctrl|control)[-+](.+)$")


def parse_key_spec(spec: str) -> str:
    """
    Parse a key specification into the exact characters a terminal transmits.

    Accepted forms:

    ==================  ==========================================
    ``Enter`` ``Up``    named keys (case-insensitive)
    ``C-t``             Ctrl combination -> ``chr(ord(c) & 0x1f)``
    ``ctrl+t``          same, alternate spelling
    ``^T``              same, caret notation
    ``C-]``             Ctrl punctuation -> 0x1d
    ``0x14``            explicit byte value
    ``none``            deliberately unbound -> empty string
    ``y``               any single printable character, literally
    ==================  ==========================================

    Anything else raises KeySpecError. This is deliberate: the old behaviour
    was to return the unrecognised spec unchanged, which meant `send_key: C-t`
    typed the three characters "C-t" into the recorded session and the only
    way to notice was to watch the video.
    """
    if not isinstance(spec, str):
        raise KeySpecError(f"Key specification must be a string, got {type(spec).__name__}: {spec!r}")

    if spec in _LITERAL_KEYS:
        return _LITERAL_KEYS[spec]

    raw = spec.strip()
    if not raw:
        raise KeySpecError("Key specification is empty.")

    lowered = raw.lower()

    if lowered in ("none", "off", "disabled"):
        return ""

    if lowered in _NAMED_KEYS:
        return _NAMED_KEYS[lowered]

    hex_match = _HEX_SPEC.match(lowered)
    if hex_match:
        value = int(hex_match.group(1), 16)
        if value > 0xFF:
            raise KeySpecError(f"Byte value out of range in key specification: {spec!r}")
        return chr(value)

    ctrl_target: Optional[str] = None
    ctrl_match = _CTRL_SPEC.match(lowered)
    if ctrl_match:
        ctrl_target = ctrl_match.group(1)
    elif len(raw) == 2 and raw[0] == "^":
        ctrl_target = raw[1].lower()

    if ctrl_target is not None:
        if ctrl_target in _CTRL_PUNCTUATION:
            return _CTRL_PUNCTUATION[ctrl_target]
        if len(ctrl_target) == 1 and "a" <= ctrl_target <= "z":
            return chr(ord(ctrl_target) & 0x1F)
        raise KeySpecError(
            f"Unsupported Ctrl combination: {spec!r}. "
            f"Use C-<letter>, or one of C-@ C-space C-[ C-\\ C-] C-^ C-_ C-?"
        )

    # A bare printable character is its own keystroke ("y" at a [y/N] prompt).
    if len(raw) == 1 and raw.isprintable():
        return raw

    raise KeySpecError(
        f"Unrecognised key specification: {spec!r}. "
        f"Expected a named key ({', '.join(sorted(set(_NAMED_KEYS)))}), "
        f"a Ctrl combination such as 'C-t', a byte such as '0x14', "
        f"a single character, or 'none'."
    )


def describe_key_bytes(data: bytes) -> str:
    """
    Human-readable label for bytes read from a terminal, for `--keys` output.

    Returns a spec that round-trips through parse_key_spec where one exists,
    otherwise a hex dump.
    """
    if len(data) == 1:
        b = data[0]
        if b == 0x00:
            return "C-space"
        if b == 0x1B:
            return "Escape"
        if b == 0x09:
            return "Tab"
        if b == 0x0D:
            return "Enter"
        if b == 0x7F:
            return "Backspace"
        if b < 0x20:
            for name, ch in _CTRL_PUNCTUATION.items():
                if ch == chr(b) and len(name) == 1:
                    return f"C-{name}"
            return f"C-{chr(b + 0x60)}"
        if 0x20 <= b < 0x7F:
            return repr(chr(b))
    return " ".join(f"0x{b:02x}" for b in data)


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
