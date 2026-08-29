"""
Event reactor, conditional triggers, screen monitoring, and idle detection.
"""

from termreel.reactor.triggers import (
    Trigger,
    TriggerAction,
    ActionType,
    create_trust_dialog_trigger,
    create_permission_prompt_trigger,
)
from termreel.reactor.monitor import (
    ScreenMonitor,
    DEFAULT_IDLE_PATTERNS,
    DEFAULT_BUSY_PATTERNS,
)

__all__ = [
    "Trigger",
    "TriggerAction",
    "ActionType",
    "create_trust_dialog_trigger",
    "create_permission_prompt_trigger",
    "ScreenMonitor",
    "DEFAULT_IDLE_PATTERNS",
    "DEFAULT_BUSY_PATTERNS",
]
