"""
TermReel: Universal Terminal Recording & Video Synthesis Harness.

Drives interactive CLIs, TUIs, and AI coding agents inside pseudo-terminals,
simulates natural keystrokes, reacts to live screen events, and streams
pixel-perfect H.264 MP4, WebM, and GIF videos.
"""

__version__ = "0.1.0"
__author__ = "Paul Datta"
__email__ = "pkdatta2000@gmail.com"

from termreel.emulator.colors import ANSIColor, ColorPalette, parse_hex_color, rgb_to_hex
from termreel.emulator.state import CharCell, Cursor, TerminalState
from termreel.emulator.parser import ANSIParser
from termreel.supervisor.base import BaseSupervisor
from termreel.supervisor.tmux_session import TmuxSupervisor
from termreel.supervisor.pty_session import PtySupervisor
from termreel.supervisor.factory import create_supervisor
from termreel.renderer.themes import Theme, get_theme, list_themes
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.transcoder.gif_encoder import GifEncoder
from termreel.reactor.triggers import Trigger, TriggerAction, ActionType
from termreel.reactor.monitor import ScreenMonitor
from termreel.scenario.schema import ScenarioManifest, TimelineStep
from termreel.scenario.runner import ScenarioRunner, ScenarioReport
from termreel.utils.keystrokes import KeystrokeGenerator, KeyMap
from termreel.utils.redaction import Redactor
from termreel.utils.asciicast import AsciicastRecorder, AsciicastPlayer

__all__ = [
    "__version__",
    "ANSIColor",
    "ColorPalette",
    "parse_hex_color",
    "rgb_to_hex",
    "CharCell",
    "Cursor",
    "TerminalState",
    "ANSIParser",
    "BaseSupervisor",
    "TmuxSupervisor",
    "PtySupervisor",
    "create_supervisor",
    "Theme",
    "get_theme",
    "list_themes",
    "CairoTerminalRenderer",
    "FFmpegPipe",
    "GifEncoder",
    "Trigger",
    "TriggerAction",
    "ActionType",
    "ScreenMonitor",
    "ScenarioManifest",
    "TimelineStep",
    "ScenarioRunner",
    "ScenarioReport",
    "KeystrokeGenerator",
    "KeyMap",
    "Redactor",
    "AsciicastRecorder",
    "AsciicastPlayer",
]
