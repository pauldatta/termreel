"""
Human-driven live terminal capture.

`termreel live` records the operator's own shell session rather than replaying
a scripted manifest. The user types; TermReel encodes.
"""

from termreel.live.config import (
    DEFAULT_PREFIX_SPEC,
    PREFIX_ENV_VAR,
    PrefixBinding,
    config_file_path,
    load_live_config,
    resolve_prefix,
)
from termreel.live.passthrough import (
    DEFAULT_BINDINGS,
    PASTE_END,
    PASTE_START,
    PassthroughLoop,
    PrefixFSM,
    TerminalGuard,
)
from termreel.live.keyprobe import classify, format_report, run_key_probe
from termreel.live.recorder import LiveRecorder, LiveReport, blend_frames

__all__ = [
    "DEFAULT_BINDINGS",
    "DEFAULT_PREFIX_SPEC",
    "PASTE_END",
    "PASTE_START",
    "PREFIX_ENV_VAR",
    "PassthroughLoop",
    "PrefixBinding",
    "PrefixFSM",
    "LiveRecorder",
    "LiveReport",
    "TerminalGuard",
    "blend_frames",
    "classify",
    "config_file_path",
    "format_report",
    "load_live_config",
    "resolve_prefix",
    "run_key_probe",
]
