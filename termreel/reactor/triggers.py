"""
Conditional event triggers, pattern matching, and automated reaction rules.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
import time
from typing import Callable, List, Optional, Pattern, Union, Any


class ActionType(str, Enum):
    SEND_KEY = "send_key"
    TYPE_TEXT = "type"
    PAUSE = "pause"
    SCREENSHOT = "screenshot"
    CALLBACK = "callback"


@dataclass
class TriggerAction:
    """Action executed when a trigger matches."""
    action_type: ActionType
    value: Any = None
    delay_after: float = 0.0


@dataclass
class Trigger:
    """
    Monitors live screen content and triggers an automated action upon pattern match.
    Ideal for auto-confirming workspace trust dialogs, permission approvals, etc.
    """
    pattern: Union[str, Pattern]
    action: Union[TriggerAction, List[TriggerAction], Callable, str]
    once: bool = True
    cooldown_seconds: float = 1.0
    max_firings: int = 1
    times_fired: int = 0
    last_fired_time: float = 0.0

    def __post_init__(self):
        if isinstance(self.pattern, str):
            self._compiled_regex = re.compile(self.pattern, re.IGNORECASE)
        else:
            self._compiled_regex = self.pattern

    def matches(self, screen_text: str) -> bool:
        """Check if trigger pattern matches current screen text."""
        return bool(self._compiled_regex.search(screen_text))

    def can_fire(self, current_time: Optional[float] = None) -> bool:
        """Check if trigger is eligible to fire based on count and cooldown."""
        now = current_time if current_time is not None else time.time()
        if self.once and self.times_fired >= 1:
            return False
        if self.max_firings > 0 and self.times_fired >= self.max_firings:
            return False
        if (now - self.last_fired_time) < self.cooldown_seconds:
            return False
        return True

    def mark_fired(self, current_time: Optional[float] = None):
        """Record a firing event."""
        now = current_time if current_time is not None else time.time()
        self.times_fired += 1
        self.last_fired_time = now


def create_trust_dialog_trigger(action_key: str = "Enter") -> Trigger:
    """Helper creating a trigger that auto-confirms project workspace trust prompts."""
    return Trigger(
        pattern=r"Do you trust the contents of this project|Yes, I trust|Trust project",
        action=TriggerAction(action_type=ActionType.SEND_KEY, value=action_key, delay_after=0.5),
        once=True,
    )


def create_permission_prompt_trigger(action_key: str = "Enter") -> Trigger:
    """Helper creating a trigger that auto-approves CLI permission confirmation prompts."""
    return Trigger(
        pattern=r"Approve change\?|Allow tool call|\[y/N\]",
        action=[
            TriggerAction(action_type=ActionType.TYPE_TEXT, value="y", delay_after=0.1),
            TriggerAction(action_type=ActionType.SEND_KEY, value=action_key, delay_after=0.5),
        ],
        once=False,
        cooldown_seconds=1.5,
        max_firings=20,
    )
