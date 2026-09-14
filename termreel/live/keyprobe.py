"""
`termreel live --keys`: show what your terminal actually sends.

Every claim about Mac key bytes in TermReel's design notes is grounded in
documentation, not measured on hardware. This is the honest answer to "does
that key work on *my* keyboard" -- it reports the literal bytes your terminal
emits, and whether binding them is a good idea.
"""

import os
import sys
import termios
import tty
from typing import Dict, List, Optional, Tuple

from termreel.live.config import REFUSED_PREFIXES
from termreel.utils.keystrokes import describe_key_bytes

# Keys that parse fine but that the shell is likely to want back.
CONTESTED_KEYS: Dict[str, str] = {
    "\x01": "readline beginning-of-line; also the tmux prefix on many setups",
    "\x02": "readline backward-char; the default tmux prefix",
    "\x05": "readline end-of-line",
    "\x06": "readline forward-char",
    "\x0b": "readline kill-line",
    "\x0c": "readline clear-screen",
    "\x0e": "readline next-history",
    "\x0f": "already used by TermReel scenarios as a shortcut key",
    "\x10": "readline previous-history",
    "\x12": "readline reverse-search-history",
    "\x15": "readline unix-line-discard",
    "\x17": "readline unix-word-rubout",
}


def classify(data: bytes) -> Tuple[str, str]:
    """
    Return (verdict, explanation) for a captured key.

    verdict is one of "safe", "contested", "refused", "not-a-key".
    """
    if len(data) != 1:
        if data.startswith(b"\x1b"):
            return (
                "not-a-key",
                "escape sequence (arrow/function key) - not usable as a single-byte prefix",
            )
        return (
            "not-a-key",
            "multi-byte input. If you pressed Option+letter, your terminal sent an "
            'accented character; enable "Use Option as Meta" to change that',
        )

    char = chr(data[0])
    refused = REFUSED_PREFIXES.get(char)
    if refused is not None:
        return "refused", refused
    contested = CONTESTED_KEYS.get(char)
    if contested is not None:
        return "contested", contested
    if data[0] < 0x20 or data[0] == 0x7F:
        return "safe", "rarely used interactively"
    return "not-a-key", "printable character - a prefix must be a control key"


def format_report(data: bytes) -> str:
    """One report line for a captured key."""
    hex_bytes = " ".join(f"0x{b:02x}" for b in data)
    label = describe_key_bytes(data)
    verdict, explanation = classify(data)

    if verdict == "safe":
        return f'  -> {hex_bytes:<12} {label:<12} OK  safe. Suggested config: prefix: "{label}"'
    if verdict == "contested":
        return f"  -> {hex_bytes:<12} {label:<12} !!  {explanation}"
    if verdict == "refused":
        return f"  -> {hex_bytes:<12} {label:<12} NO  {explanation}"
    return f"  -> {hex_bytes:<12} {label:<12} --  {explanation}"


def run_key_probe(stdin_fd: Optional[int] = None, max_keys: Optional[int] = None) -> int:
    """
    Read keys in raw mode and report their bytes until Ctrl-C.

    Raw mode means Ctrl-C arrives as the byte 0x03 rather than a signal, so it
    is handled explicitly as the exit condition.
    """
    fd = stdin_fd if stdin_fd is not None else sys.stdin.fileno()
    if not os.isatty(fd):
        print(
            "termreel live --keys needs an interactive terminal on stdin.",
            file=sys.stderr,
        )
        return 1

    print("Press any key to see what your terminal sends.  Ctrl-C to exit.")
    sys.stdout.flush()

    saved = termios.tcgetattr(fd)
    seen = 0
    try:
        tty.setraw(fd)
        while max_keys is None or seen < max_keys:
            data = os.read(fd, 32)
            if not data:
                break
            if data == b"\x03":
                break
            sys.stdout.write(format_report(data) + "\r\n")
            sys.stdout.flush()
            seen += 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    print()
    return 0
