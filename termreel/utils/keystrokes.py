"""
Natural human typing simulation, jitter generation, typo injection, and key mapping.

Key specifications use one vocabulary for both backends. ``C-r``,
``ctrl+r``, ``Ctrl-R`` and ``^R`` all mean the same key, and so do
``M-f`` / ``alt+f`` / ``meta+f`` and ``S-Tab`` / ``shift+tab`` / ``BTab``.
:func:`parse_key` turns a spec into a :class:`Key`; the PTY backend encodes it
as the bytes xterm sends (:meth:`KeyMap.to_pty`) and the tmux backend as a
``send-keys`` argument (:meth:`KeyMap.tmux_args`). An unknown or unsupported
spec raises :class:`KeySpecError` on both backends instead of being typed
into the session as literal text.
"""

import random
import re
import time
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Key model
# ---------------------------------------------------------------------------

# Canonical special-key names and their spellings.
_KEY_ALIASES: Dict[str, str] = {
    "enter": "enter", "return": "enter", "ret": "enter", "cr": "enter",
    "esc": "escape", "escape": "escape",
    "tab": "tab",
    "btab": "btab", "backtab": "btab",
    "backspace": "backspace", "bspace": "backspace", "bs": "backspace",
    "space": "space", "spc": "space",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "pageup": "pageup", "pgup": "pageup", "ppage": "pageup", "page_up": "pageup",
    "pagedown": "pagedown", "pgdn": "pagedown", "npage": "pagedown", "page_down": "pagedown",
    "insert": "insert", "ins": "insert", "ic": "insert",
    "delete": "delete", "del": "delete", "dc": "delete",
}
for _n in range(1, 13):
    _KEY_ALIASES[f"f{_n}"] = f"f{_n}"

_TMUX_NAMES: Dict[str, str] = {
    "enter": "Enter", "escape": "Escape", "tab": "Tab", "btab": "BTab",
    "backspace": "BSpace", "space": "Space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PPage", "pagedown": "NPage",
    "insert": "IC", "delete": "DC",
}
for _n in range(1, 13):
    _TMUX_NAMES[f"f{_n}"] = f"F{_n}"

# xterm encodings (normal cursor-key mode).
_CSI_LETTER: Dict[str, str] = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F"}
_SS3_FKEYS: Dict[str, str] = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}
_TILDE_CODES: Dict[str, int] = {
    "insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
    "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24,
}

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

# Raw byte values accepted verbatim, before any whitespace stripping.
_LITERAL_KEYS: Dict[str, str] = {
    "\r": "enter",
    "\n": "enter",
    "\x1b": "escape",
    "\t": "tab",
    "\x7f": "backspace",
    " ": "space",
}

_HEX_SPEC = re.compile(r"^0x([0-9a-f]{1,2})$")
_MOD_SPEC = re.compile(r"^(ctrl|control|c|alt|meta|opt|option|m|shift|s)[-+](.+)$")
_MOD_NAMES = {
    "ctrl": "ctrl", "control": "ctrl", "c": "ctrl",
    "alt": "alt", "meta": "alt", "opt": "alt", "option": "alt", "m": "alt",
    "shift": "shift", "s": "shift",
}


@dataclass(frozen=True)
class Key:
    """
    A parsed key. Exactly one of ``name`` (canonical special key), ``char``
    (one printable character) or ``byte`` (explicit 0xNN value) is set;
    ``none`` marks a deliberately unbound key.
    """

    name: Optional[str] = None
    char: Optional[str] = None
    byte: Optional[int] = None
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    none: bool = False
    spec: str = ""

    @property
    def xterm_modifier(self) -> int:
        return 1 + (1 if self.shift else 0) + (2 if self.alt else 0) + (4 if self.ctrl else 0)


def _unsupported(spec: str, why: str) -> KeySpecError:
    return KeySpecError(f"Unsupported key specification {spec!r}: {why}")


def parse_key(spec: str) -> Key:
    """Parse a key spec into a :class:`Key` (see module docstring)."""
    if not isinstance(spec, str):
        raise KeySpecError(f"Key specification must be a string, got {type(spec).__name__}: {spec!r}")

    if spec in _LITERAL_KEYS:
        return Key(name=_LITERAL_KEYS[spec], spec=spec)

    raw = spec.strip()
    if not raw:
        raise KeySpecError("Key specification is empty.")
    lowered = raw.lower()

    if lowered in ("none", "off", "disabled"):
        return Key(none=True, spec=spec)

    hex_match = _HEX_SPEC.match(lowered)
    if hex_match:
        return Key(byte=int(hex_match.group(1), 16), spec=spec)
    if lowered.startswith("0x"):
        raise KeySpecError(f"Byte value out of range or malformed in key specification: {spec!r}")

    ctrl = alt = shift = False
    rest_raw, rest = raw, lowered
    if len(raw) == 2 and raw[0] == "^":
        ctrl = True
        rest_raw, rest = raw[1], raw[1].lower()
    else:
        while True:
            m = _MOD_SPEC.match(rest)
            if not m:
                break
            mod = _MOD_NAMES[m.group(1)]
            if mod == "ctrl":
                ctrl = True
            elif mod == "alt":
                alt = True
            else:
                shift = True
            cut = len(rest) - len(m.group(2))
            rest, rest_raw = m.group(2), rest_raw[cut:]
        if (ctrl or alt or shift) and not rest:
            raise KeySpecError(f"Key specification {spec!r} has a modifier but no key.")

    if rest in _KEY_ALIASES:
        name = _KEY_ALIASES[rest]
        if name == "btab":
            name, shift = "tab", True
        return Key(name=name, ctrl=ctrl, alt=alt, shift=shift, spec=spec)

    if ctrl and rest in _CTRL_PUNCTUATION:
        return Key(char=rest if len(rest) == 1 else " ", ctrl=True, alt=alt, shift=shift, spec=spec)

    if len(rest_raw) == 1 and rest_raw.isprintable():
        return Key(char=rest_raw, ctrl=ctrl, alt=alt, shift=shift, spec=spec)

    if ctrl or alt or shift:
        raise KeySpecError(
            f"Unsupported key combination: {spec!r}. Modifiers (C-/ctrl+, M-/alt+, "
            f"S-/shift+) combine with a named key or a single character."
        )
    raise KeySpecError(
        f"Unrecognised key specification: {spec!r}. "
        f"Expected a named key ({', '.join(sorted(set(_KEY_ALIASES.values())))}), "
        f"a combination such as 'C-t', 'M-f', 'S-Tab' or 'ctrl+left', "
        f"a byte such as '0x14', a single character, or 'none'."
    )


def encode_key_pty(key: Key, app_cursor: bool = False) -> str:
    """The characters an xterm-compatible terminal sends for ``key``."""
    spec = key.spec
    if key.none:
        return ""
    if key.byte is not None:
        if key.ctrl or key.alt or key.shift:
            raise _unsupported(spec, "modifiers cannot apply to a raw byte")
        return chr(key.byte)

    prefix = "\x1b" if key.alt else ""
    mod = key.xterm_modifier if (key.ctrl or key.shift or key.alt) else 1

    if key.name is not None:
        name = key.name
        if name in _CSI_LETTER:
            letter = _CSI_LETTER[name]
            if key.ctrl or key.shift or key.alt:
                return f"\x1b[1;{mod}{letter}"
            return (f"\x1bO{letter}" if app_cursor else f"\x1b[{letter}")
        if name in _SS3_FKEYS:
            if key.ctrl or key.shift or key.alt:
                return f"\x1b[1;{mod}{_SS3_FKEYS[name]}"
            return f"\x1bO{_SS3_FKEYS[name]}"
        if name in _TILDE_CODES:
            code = _TILDE_CODES[name]
            if key.ctrl or key.shift or key.alt:
                return f"\x1b[{code};{mod}~"
            return f"\x1b[{code}~"
        if name == "tab":
            if key.ctrl:
                raise _unsupported(spec, "terminals have no standard encoding for Ctrl+Tab")
            if key.shift:
                return prefix + "\x1b[Z"
            return prefix + "\t"
        if name == "enter":
            if key.ctrl or key.shift:
                raise _unsupported(spec, "terminals send plain Enter for Ctrl/Shift+Enter")
            return prefix + "\r"
        if name == "escape":
            if key.ctrl or key.shift:
                raise _unsupported(spec, "no standard encoding")
            return prefix + "\x1b"
        if name == "backspace":
            if key.shift:
                raise _unsupported(spec, "no standard encoding for Shift+Backspace")
            return prefix + ("\x08" if key.ctrl else "\x7f")
        if name == "space":
            if key.ctrl:
                return prefix + "\x00"
            return prefix + " "
        raise _unsupported(spec, f"no PTY encoding for {name}")

    ch = key.char or ""
    if key.ctrl:
        target = ch.lower()
        if target == " ":
            return prefix + "\x00"
        if target in _CTRL_PUNCTUATION:
            return prefix + _CTRL_PUNCTUATION[target]
        if "a" <= target <= "z":
            return prefix + chr(ord(target) & 0x1F)
        raise KeySpecError(
            f"Unsupported Ctrl combination: {spec!r}. "
            f"Use C-<letter>, or one of C-@ C-space C-[ C-\\ C-] C-^ C-_ C-?"
        )
    if key.shift:
        if ch.isalpha():
            return prefix + ch.upper()
        raise _unsupported(spec, "Shift only applies to letters and named keys; type the shifted character instead")
    return prefix + ch


def parse_key_spec(spec: str) -> str:
    """
    Parse a key specification into the exact characters a terminal transmits.

    Accepted forms:

    ======================  ==========================================
    ``Enter`` ``Up`` ``F5``  named keys (case-insensitive)
    ``C-t`` ``ctrl+t``       Ctrl combination -> ``chr(ord(c) & 0x1f)``
    ``^T``                   same, caret notation
    ``C-]``                  Ctrl punctuation -> 0x1d
    ``M-f`` ``alt+f``        Alt/Meta -> ESC prefix
    ``S-Tab`` ``BTab``       Shift+Tab -> ``CSI Z``
    ``ctrl+left``            modified cursor/function keys (xterm style)
    ``0x14``                 explicit byte value
    ``none``                 deliberately unbound -> empty string
    ``y``                    any single printable character, literally
    ======================  ==========================================

    Anything else raises KeySpecError. The old behaviour was to return the
    unrecognised spec unchanged, which meant `send_key: C-t` typed the three
    characters "C-t" into the recorded session.
    """
    return encode_key_pty(parse_key(spec))


def tmux_key_args(spec: str) -> List[str]:
    """
    Arguments for ``tmux send-keys -t <target> ...`` that press ``spec``.

    A single printable character is sent with ``-l`` so tmux does not read it
    as a key name or a command separator (a lone ``;`` is one).
    """
    key = parse_key(spec)
    if key.none:
        return []
    if key.byte is not None:
        if key.ctrl or key.alt or key.shift:
            raise _unsupported(spec, "modifiers cannot apply to a raw byte")
        return ["-H", f"{key.byte:02x}"]
    mods = ("C-" if key.ctrl else "") + ("M-" if key.alt else "")
    if key.name is not None:
        name = _TMUX_NAMES[key.name]
        if key.name == "tab" and key.shift:
            if key.ctrl:
                raise _unsupported(spec, "no standard encoding for Ctrl+Shift+Tab")
            return [("M-" if key.alt else "") + "BTab"]
        if key.shift:
            if key.name in _CSI_LETTER or key.name in _SS3_FKEYS or key.name in _TILDE_CODES:
                mods += "S-"
            else:
                raise _unsupported(spec, f"no standard encoding for Shift+{name}")
        if key.ctrl and key.name in ("tab", "enter", "escape"):
            raise _unsupported(spec, f"no standard encoding for Ctrl+{name}")
        # Validate the same combinations the PTY backend accepts so a spec
        # never works on one backend and fails on the other.
        encode_key_pty(key)
        return [mods + name]
    ch = key.char or ""
    if key.ctrl:
        encode_key_pty(key)  # same validation as PTY (letters/punctuation)
        target = ch.lower()
        if target == " " or target == "@":
            return [("M-" if key.alt else "") + "C-Space"]
        if target == "?":
            return [("M-" if key.alt else "") + "BSpace"]
        if target == "[":
            return [("M-" if key.alt else "") + "Escape"]
        return [mods + target]
    if key.shift:
        encode_key_pty(key)
        ch = ch.upper()
    if key.alt:
        return ["M-" + ch]
    return ["-l", "--", ch]


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

    @classmethod
    def to_tmux(cls, key: str) -> str:
        """
        tmux key name for ``key`` (``Enter``, ``C-c``, ``M-f``, ``S-Up``, ``F5``).

        Raises KeySpecError for unknown keys. Previously an unknown spec was
        returned unchanged, so ``ctrl+r`` was typed as the text "ctrl+r".
        A plain character is returned as itself; use :meth:`tmux_args` to
        send it safely.
        """
        args = tmux_key_args(key)
        if not args:
            return ""
        if args[0] == "-l":
            return args[-1]
        if args[0] == "-H":
            return "0x" + args[1]
        return args[0]

    @classmethod
    def tmux_args(cls, key: str) -> List[str]:
        """``send-keys`` arguments for ``key``; see :func:`tmux_key_args`."""
        return tmux_key_args(key)

    @classmethod
    def to_pty(cls, key: str, app_cursor: bool = False) -> str:
        """
        Map a key specification to the bytes a terminal actually sends.

        ``app_cursor`` selects application cursor-key mode (DECCKM, set by
        full-screen programs such as vim and less), in which unmodified
        arrows, Home and End are sent as ``ESC O x`` instead of ``ESC [ x``.
        Raises KeySpecError for anything unrecognised. See parse_key_spec.
        """
        return encode_key_pty(parse_key(key), app_cursor=app_cursor)


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
