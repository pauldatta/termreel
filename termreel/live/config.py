"""
Hotkey prefix resolution for `termreel live`.

Resolution order, highest priority first:

    --prefix  ->  $TERMREEL_PREFIX  ->  ~/.termreel/config.yaml  ->  default

Rebinding is first-class rather than an afterthought because the usable key
pool on a Mac is genuinely small: Command never reaches the terminal (the
window server eats it), Option emits accented characters unless the user opts
into "Use Option as Meta", and the shell already owns most of Control. Any
fixed default will collide with somebody's setup.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from termreel.exceptions import KeySpecError
from termreel.utils.keystrokes import parse_key_spec

# C-t (0x14). Single-letter Ctrl combinations are the only class that
# transmits identically across every Mac keyboard layout; C-] / C-^ / C-_
# require those characters to be reachable without AltGr, which fails on many
# non-US layouts. C-t is rarely used interactively (readline transpose-chars).
DEFAULT_PREFIX_SPEC = "C-t"

PREFIX_ENV_VAR = "TERMREEL_PREFIX"

CONFIG_DIR_NAME = ".termreel"
CONFIG_FILE_NAME = "config.yaml"

# Prefixes that are refused outright, with the reason shown to the user.
# Binding any of these does not merely collide, it removes a control the
# operator needs while the recording is running.
REFUSED_PREFIXES: Dict[str, str] = {
    "\x03": "C-c sends SIGINT; the recorded shell needs it to cancel commands",
    "\x04": "C-d sends EOF; the recorded shell needs it to exit",
    "\x13": "C-s is XOFF and will freeze your terminal with no way to unfreeze it",
    "\x11": "C-q is XON and is the only thing that undoes an accidental C-s",
    "\x1a": "C-z sends SIGTSTP; the recorded shell needs it to suspend jobs",
    "\x1c": "C-\\ sends SIGQUIT",
    "\x1b": "Escape (C-[) prefixes every arrow key and function key sequence",
    "\r": "Enter cannot be a prefix",
    "\n": "Newline cannot be a prefix",
}


@dataclass(frozen=True)
class PrefixBinding:
    """A resolved, validated hotkey prefix."""

    spec: str
    byte: bytes
    source: str

    @property
    def label(self) -> str:
        """Short caret-notation label for banners, e.g. '^T'."""
        value = self.byte[0]
        if value < 0x20:
            return f"^{chr(value + 0x40)}"
        if value == 0x7F:
            return "^?"
        return chr(value)


def config_home() -> str:
    """Directory holding TermReel's user configuration."""
    return os.path.join(os.path.expanduser("~"), CONFIG_DIR_NAME)


def config_file_path() -> str:
    """Path of the global config file (~/.termreel/config.yaml)."""
    return os.path.join(config_home(), CONFIG_FILE_NAME)


def load_live_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Read the ``live:`` section of the global config file.

    A missing, unreadable, empty or malformed file is not an error: it just
    means no configured preference, and resolution falls through to the
    default. A config file should never be able to stop a recording.
    """
    target = path if path is not None else config_file_path()
    if not os.path.isfile(target):
        return {}
    try:
        import yaml

        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    live = data.get("live")
    return live if isinstance(live, dict) else {}


def validate_prefix_spec(spec: str, source: str) -> PrefixBinding:
    """
    Parse and validate a single prefix specification.

    Raises KeySpecError with an explanation if the spec is unparseable, is not
    a single byte, or names a key the recorded shell cannot afford to lose.
    """
    parsed = parse_key_spec(spec)

    if parsed == "":
        raise KeySpecError(
            f"Hotkey prefix from {source} is '{spec}', which disables the prefix entirely. "
            f"There would then be no way to pause or stop the recording."
        )

    if len(parsed) != 1:
        raise KeySpecError(
            f"Hotkey prefix from {source} must be a single keystroke, but '{spec}' "
            f"expands to {len(parsed)} characters. Use a Ctrl combination such as 'C-t'."
        )

    reason = REFUSED_PREFIXES.get(parsed)
    if reason is not None:
        raise KeySpecError(
            f"Refusing hotkey prefix '{spec}' from {source}: {reason}. "
            f"Try 'C-t', 'C-g' or 'C-]' instead."
        )

    return PrefixBinding(spec=spec, byte=parsed.encode("latin-1"), source=source)


def resolve_prefix(
    cli_value: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> PrefixBinding:
    """
    Resolve the live hotkey prefix.

    ``env`` and ``config`` are injectable so this is testable without touching
    the real environment or the user's home directory.
    """
    environ = env if env is not None else os.environ
    live_config = config if config is not None else load_live_config()

    if cli_value:
        return validate_prefix_spec(cli_value, "--prefix")

    env_value = environ.get(PREFIX_ENV_VAR)
    if env_value:
        return validate_prefix_spec(env_value, f"${PREFIX_ENV_VAR}")

    config_value = live_config.get("prefix")
    if isinstance(config_value, str) and config_value.strip():
        return validate_prefix_spec(config_value, config_file_path())

    return validate_prefix_spec(DEFAULT_PREFIX_SPEC, "default")
