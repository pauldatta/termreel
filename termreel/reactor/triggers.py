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

    ``edge`` selects edge-triggered firing, used by the built-in auto-approve
    triggers so one prompt gets one answer:

    * ``None`` (default): level-triggered. Fires whenever the pattern is on
      screen and ``can_fire`` allows it (limits and cooldown).
    * ``"line"``: fires once per prompt that is *waiting for input*: the
      cursor line (or the last non-blank line) matches and nothing but
      whitespace/punctuation follows the match. A prompt instance is its
      absolute line (rows scrolled + row) and text, so an identical prompt
      printed on the bottom row after a scroll is a new instance, while an
      answered prompt, or one that stays up unanswered, is not re-answered.
    * ``"presence"``: fires when the pattern appears. Re-arms when it has
      disappeared from the screen, or when a new match appears on a lower
      absolute line than the answered one after the answer was sent (see
      ``_observe_presence``). A dialog that stays up because the keystroke
      did not dismiss it is not answered again.
    """
    pattern: Union[str, Pattern]
    action: Union[TriggerAction, List[TriggerAction], Callable, str]
    once: bool = True
    cooldown_seconds: float = 1.0
    max_firings: int = 1
    max_count: Optional[int] = None
    times_fired: int = 0
    last_fired_time: float = 0.0
    edge: Optional[str] = None
    label: Optional[str] = None

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

        if self.edge not in (None, "line", "presence"):
            raise ValueError(f"Trigger edge must be None, 'line' or 'presence', not {self.edge!r}")
        self._instance = None
        self._armed = True
        self._pending = 0
        self._bottom = None
        self._in_action = False

    @property
    def pattern_text(self) -> str:
        return self._compiled_regex.pattern

    def matches(self, screen_text: str) -> bool:
        """Check if trigger pattern matches current screen text."""
        return bool(self._compiled_regex.search(screen_text))

    # Text allowed after a prompt match on a line that is still waiting for an
    # answer: whitespace and punctuation such as ": " or "? ", but no typed
    # letters or digits.
    _UNANSWERED_TAIL = re.compile(r"[^\w]*")

    def _waiting_prompt(self, screen_text: str, cursor_row: Optional[int]):
        """
        Find the prompt line that is waiting for input: the cursor row if
        known, else the last non-blank line. Returns ``(row, text)`` or None
        when that line does not match or already has an answer typed after
        the match.
        """
        lines = screen_text.split("\n")
        candidates = []
        if cursor_row is not None and 0 <= cursor_row < len(lines):
            candidates.append(cursor_row)
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip():
                if idx not in candidates:
                    candidates.append(idx)
                break
        for row in candidates:
            line = lines[row]
            last = None
            for m in self._compiled_regex.finditer(line):
                last = m
            if last is None:
                continue
            if self._UNANSWERED_TAIL.fullmatch(line[last.end():]):
                return row, line.rstrip()
        return None

    def observe(self, screen_text: str, cursor_row: Optional[int] = None,
                lines_scrolled: int = 0) -> bool:
        """
        Update edge state from a new screen and report whether an unanswered
        instance is waiting. Level triggers just report ``matches``.

        ``cursor_row`` and ``lines_scrolled`` (see
        ``BaseSupervisor.capture_prompt_view``) let the ``line`` edge tell a
        new prompt printed on the bottom row after a scroll from the one it
        already answered.
        """
        if self.edge is None:
            return self.matches(screen_text)
        if self.edge == "presence":
            return self._observe_presence(screen_text, lines_scrolled)
        # edge == "line": one answer per prompt instance. An instance is the
        # waiting line's absolute position (scroll count + row) plus its
        # text. It is answered once; it is answered again only if it goes
        # away (the answer is echoed, the screen is cleared) and comes back.
        found = self._waiting_prompt(screen_text, cursor_row)
        if found is None:
            self._pending = 0
            self._instance = None
            return False
        row, text = found
        instance = (lines_scrolled + row, text)
        if instance != self._instance:
            self._instance = instance
            self._pending = 1
        return self._pending > 0

    def _observe_presence(self, screen_text: str, lines_scrolled: int) -> bool:
        """
        ``presence`` edge. A dialog is answered once when it appears. It is
        answered again only when

        * it leaves the screen and comes back, or
        * a new match shows up *below* the one that was answered, after the
          answer was sent. That is the next dialog printed under an answered
          one that is still visible in scrolling output.

        A dialog that stays up (the key did not dismiss it) keeps its
        position and is not answered again. Positions are absolute
        (``lines_scrolled`` + row), so an answered dialog that scrolls up is
        not mistaken for a new one. Matches that appear while the answer is
        still pending (the dialog is still being drawn) are treated as part
        of the same dialog. Known gap: a dialog replaced in place by a
        different dialog at the same rows, with no frame in between where
        the pattern is absent, is not answered.
        """
        bottom_row = None
        for m in self._compiled_regex.finditer(screen_text):
            end = max(m.start(), m.end() - 1)
            row = screen_text.count("\n", 0, end)
            if bottom_row is None or row > bottom_row:
                bottom_row = row
        if bottom_row is None:
            self._armed = True
            self._pending = 0
            self._bottom = None
            return False
        bottom = lines_scrolled + bottom_row
        if self._armed:
            self._armed = False
            self._pending = 1
            self._bottom = bottom
        elif self._pending > 0 or self._in_action:
            # Not answered yet, or the answer is still being typed: more
            # lines of the same dialog may still be arriving.
            self._bottom = bottom if self._bottom is None else max(self._bottom, bottom)
        elif self._bottom is not None and bottom > self._bottom:
            self._pending = 1
            self._bottom = bottom
        else:
            # Same dialog, or it moved up (the tmux scroll counter stops at
            # history-limit, so an old dialog can appear to move up).
            self._bottom = bottom
        return self._pending > 0

    def action_started(self) -> None:
        """Called by the monitor when this trigger's action is dispatched."""
        self._in_action = True

    def action_finished(self) -> None:
        """Called by the monitor once the action's keys have been sent."""
        self._in_action = False

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
        if self._pending > 0:
            self._pending -= 1


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
        edge="presence",
        label="auto_trust",
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

    # Edge-triggered: the dialog is answered once when it appears and not
    # again until it has left the screen. The previous level-triggered
    # version re-sent Enter every 1.5 s for as long as any of these phrases
    # stayed visible, up to 50 times.
    return Trigger(
        pattern=pattern,
        action=action,
        once=False,
        cooldown_seconds=0.5,
        max_firings=50,
        edge="presence",
        label="auto_approve_dialogs:permission",
    )


def create_yes_no_prompt_trigger(
    response: str = "y",
    action_key: str = "Enter",
    delay_before: float = 0.3,
    delay_after: float = 0.4,
) -> Trigger:
    """
    Helper creating a trigger that auto-answers [y/N] / [Y/n] confirmation prompts.

    Edge-triggered per line: each prompt line that appears gets exactly one
    answer. An answered prompt still visible above the cursor is not
    answered again.
    """
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
        cooldown_seconds=0.3,
        max_firings=50,
        edge="line",
        label="auto_approve_dialogs:yes_no",
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
