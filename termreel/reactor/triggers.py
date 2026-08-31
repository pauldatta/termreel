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
    SELECT_CHOICE = "select_choice"


@dataclass
class TriggerAction:
    """Action executed when a trigger matches."""
    action_type: ActionType
    value: Any = None
    delay_before: float = 0.0
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
    max_count: Optional[int] = None
    times_fired: int = 0
    last_fired_time: float = 0.0

    def __post_init__(self):
        if isinstance(self.pattern, str):
            self._compiled_regex = re.compile(self.pattern, re.IGNORECASE)
        else:
            self._compiled_regex = self.pattern

        # Harmonize max_count and max_firings
        if self.max_count is not None:
            self.max_firings = self.max_count
        else:
            self.max_count = self.max_firings

        # If a limit > 1 was specified, ensure once is False so it doesn't stop at 1
        effective_limit = self.max_count if self.max_count is not None else self.max_firings
        if effective_limit > 1 and self.once:
            self.once = False

    def matches(self, screen_text: str) -> bool:
        """Check if trigger pattern matches current screen text."""
        return bool(self._compiled_regex.search(screen_text))

    def can_fire(self, current_time: Optional[float] = None) -> bool:
        """Check if trigger is eligible to fire based on count and cooldown."""
        now = current_time if current_time is not None else time.time()
        effective_limit = self.max_count if self.max_count is not None else self.max_firings
        if self.once and self.times_fired >= 1:
            return False
        if effective_limit > 0 and self.times_fired >= effective_limit:
            return False
        if (now - self.last_fired_time) < self.cooldown_seconds:
            return False
        return True

    def mark_fired(self, current_time: Optional[float] = None):
        """Record a firing event."""
        now = current_time if current_time is not None else time.time()
        self.times_fired += 1
        self.last_fired_time = now


def create_trust_dialog_trigger(
    action_key: str = "Enter",
    delay_before: float = 0.4,
    delay_after: float = 0.4,
) -> Trigger:
    """Helper creating a trigger that auto-confirms project workspace trust prompts."""
    return Trigger(
        pattern=r"Do you trust the contents of this project|Yes, I trust|Trust project|Trust this workspace|Trust folder",
        action=TriggerAction(
            action_type=ActionType.SEND_KEY,
            value=action_key,
            delay_before=delay_before,
            delay_after=delay_after,
        ),
        once=True,
    )


def create_agy_permission_dialog_trigger(
    choice: int = 1,
    delay_before: float = 0.5,
    delay_after: float = 0.4,
    action_key: str = "Enter",
) -> Trigger:
    """
    Helper creating a trigger that auto-resolves interactive permission selection dialogs
    in Antigravity (agy) CLI, such as 'Requesting permission for: ... Do you want to proceed?'.
    Allows a natural reading pause before selecting the affirmative choice.
    """
    pattern = (
        r"Requesting permission for:"
        r"|Do you want to proceed\??"
        r"|Approve change\??"
        r"|Allow tool call"
        r"|Allow command"
        r"|Allow execution"
        r"|Allow this action"
        r"|Do you want to execute"
        r"|Do you want to run"
        r"|Permission required"
        r"|Permission request"
        r"|Grant permission"
        r"|Allow once"
        r"|Always allow"
        r"|Human[- ]in[- ]the[- ]loop"
        r"|>\s*1\.\s*Yes"
        r"|1\.\s*Yes"
    )
    if choice == 1:
        action: Union[TriggerAction, List[TriggerAction]] = TriggerAction(
            action_type=ActionType.SEND_KEY,
            value=action_key,
            delay_before=delay_before,
            delay_after=delay_after,
        )
    else:
        action = TriggerAction(
            action_type=ActionType.SELECT_CHOICE,
            value=choice,
            delay_before=delay_before,
            delay_after=delay_after,
        )

    return Trigger(
        pattern=pattern,
        action=action,
        once=False,
        cooldown_seconds=1.5,
        max_firings=50,
    )


def create_yes_no_prompt_trigger(
    response: str = "y",
    action_key: str = "Enter",
    delay_before: float = 0.3,
    delay_after: float = 0.4,
) -> Trigger:
    """Helper creating a trigger that auto-answers [y/N] / [Y/n] confirmation prompts."""
    return Trigger(
        pattern=r"\[y/N\]|\[Y/n\]|\(y/n\)|\(Y/N\)",
        action=[
            TriggerAction(
                action_type=ActionType.TYPE_TEXT,
                value=response,
                delay_before=delay_before,
                delay_after=0.1,
            ),
            TriggerAction(
                action_type=ActionType.SEND_KEY,
                value=action_key,
                delay_after=delay_after,
            ),
        ],
        once=False,
        cooldown_seconds=1.5,
        max_firings=50,
    )


def create_permission_prompt_trigger(
    action_key: str = "Enter",
    delay_before: float = 0.3,
    delay_after: float = 0.4,
) -> Trigger:
    """Helper creating a trigger that auto-approves CLI permission confirmation prompts."""
    return create_agy_permission_dialog_trigger(
        choice=1,
        delay_before=delay_before,
        delay_after=delay_after,
        action_key=action_key,
    )
