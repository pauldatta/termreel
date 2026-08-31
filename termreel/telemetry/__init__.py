"""
TermReel Telemetry Subsystem.
Provides live session registration, UNIX domain socket IPC, and screen observation.
"""

from termreel.telemetry.models import SessionMetadata, ScreenSnapshot
from termreel.telemetry.registry import SessionRegistry
from termreel.telemetry.server import TelemetryServer

__all__ = [
    "SessionMetadata",
    "ScreenSnapshot",
    "SessionRegistry",
    "TelemetryServer",
]
