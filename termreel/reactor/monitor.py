"""
Screen state monitor, event reactor, idle detector, and verification assertions.
"""

import re
import sys
import threading
import time
from typing import List, Optional, Pattern, Union, Callable
from termreel.exceptions import KeySpecError
from termreel.reactor.triggers import Trigger, TriggerAction, ActionType
from termreel.supervisor.base import BaseSupervisor


DEFAULT_IDLE_PATTERNS = [
    re.compile(r"\?\s+for\s+shortcuts", re.IGNORECASE),
    re.compile(r"(?:^|\n)[^\n]*[>$#]\s*$", re.MULTILINE),
]

DEFAULT_BUSY_PATTERNS = [
    re.compile(r"Generating\.\.\.", re.IGNORECASE),
    re.compile(r"Thinking\.\.\.", re.IGNORECASE),
    re.compile(r"Executing\.\.\.", re.IGNORECASE),
    re.compile(r"Working\.\.\.", re.IGNORECASE),
    re.compile(r"Editing\s+files\.\.\.", re.IGNORECASE),
    re.compile(r"Applying\s+changes\.\.\.", re.IGNORECASE),
    re.compile(r"Writing\s+files\.\.\.", re.IGNORECASE),
    re.compile(r"esc\s+to\s+cancel", re.IGNORECASE),
    re.compile(r"Requesting\s+permission\s+for:", re.IGNORECASE),
    re.compile(r"Do\s+you\s+want\s+to\s+proceed\??", re.IGNORECASE),
    re.compile(r"Approve\s+change\??", re.IGNORECASE),
    re.compile(r">\s*1\.\s*Yes", re.IGNORECASE),
    re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⡿⣟⣯⣷⣾⣽⣻⢿]"),
]


class ScreenMonitor:
    """
    Monitors real-time screen content, executes registered conditional triggers,
    and provides deterministic state-transition synchronization (e.g. wait_for_idle).
    """

    def __init__(
        self,
        supervisor: Optional[BaseSupervisor] = None,
        triggers: Optional[List[Trigger]] = None,
    ):
        self.supervisor = supervisor
        self.triggers: List[Trigger] = triggers or []
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._action_threads: List[threading.Thread] = []
        # Every trigger firing: {"time", "trigger", "pattern", "keys"}.
        self.injections: List[dict] = []

    def add_trigger(self, trigger: Trigger):
        """Register a new event trigger."""
        with self._lock:
            self.triggers.append(trigger)

    def wait_for_actions(self, timeout: float = 2.0):
        """Wait for any active asynchronous trigger actions to finish."""
        threads = list(self._action_threads)
        for t in threads:
            if t.is_alive():
                t.join(timeout=timeout)
        self._action_threads = [t for t in self._action_threads if t.is_alive()]

    def evaluate_and_react(
        self,
        supervisor: Optional[BaseSupervisor] = None,
        async_action: bool = False,
    ) -> List[Trigger]:
        """
        Capture screen text, test against all active triggers, and execute matching actions.
        When async_action is True, actions run in background threads without blocking frame rendering.
        Returns the list of triggers that fired.
        """
        sup = supervisor or self.supervisor
        if not sup:
            return []

        with self._lock:
            view = getattr(sup, "capture_prompt_view", None)
            if callable(view):
                screen_text, cursor_row, scrolled = view()
            else:
                screen_text, cursor_row, scrolled = sup.capture_plain(), None, 0
            now = time.time()
            fired = []

            for trig in self.triggers:
                # observe() runs on every evaluation, even while a trigger is
                # cooling down, so edge triggers track prompt instances
                # continuously instead of only when they are eligible.
                if hasattr(trig, "observe"):
                    waiting = trig.observe(screen_text, cursor_row, scrolled)
                else:
                    waiting = trig.matches(screen_text)
                if waiting and trig.can_fire(now):
                    trig.mark_fired(now)
                    fired.append(trig)
                    self._record_injection(trig, now)
                    started = getattr(trig, "action_started", None)
                    if callable(started):
                        started()
                    if async_action:
                        t = threading.Thread(
                            target=self._execute_action,
                            args=(trig.action, sup, getattr(trig, "action_finished", None)),
                            daemon=True,
                        )
                        t.start()
                        self._action_threads.append(t)
                    else:
                        self._execute_action(trig.action, sup, getattr(trig, "action_finished", None))

            # Cleanup finished action threads
            if self._action_threads:
                self._action_threads = [t for t in self._action_threads if t.is_alive()]

            return fired

    @staticmethod
    def describe_action(action) -> List[str]:
        """Human-readable list of the keys/text an action will inject."""
        if callable(action) and not isinstance(action, TriggerAction):
            return [f"callback:{getattr(action, '__name__', 'callable')}"]
        out: List[str] = []
        for act in (action if isinstance(action, list) else [action]):
            if isinstance(act, str):
                out.append(f"key:{act}")
            elif isinstance(act, TriggerAction):
                if act.action_type == ActionType.SEND_KEY:
                    out.append(f"key:{act.value}")
                elif act.action_type == ActionType.TYPE_TEXT:
                    out.append(f"text:{act.value!r}")
                elif act.action_type == ActionType.SELECT_CHOICE:
                    out.append(f"choice:{act.value}")
                elif act.action_type == ActionType.PAUSE:
                    continue
                else:
                    out.append(str(act.action_type.value))
        return out

    def _record_injection(self, trig: Trigger, now: float) -> None:
        self.injections.append({
            "time": now,
            "trigger": getattr(trig, "label", None) or "trigger",
            "pattern": getattr(trig, "pattern_text", str(trig.pattern)),
            "keys": self.describe_action(trig.action),
        })

    def _execute_action(self, action: Union[TriggerAction, List[TriggerAction], Callable, str], sup: BaseSupervisor,
                        on_sent: Optional[Callable[[], None]] = None):
        """
        Execute the resolved action on the supervisor in a thread-safe manner.

        ``on_sent`` is called as soon as the last key has been sent, before
        that action's ``delay_after`` pause (the program may already be
        printing its next prompt during the pause), and also if the action
        fails.
        """
        notified = False

        def _notify():
            nonlocal notified
            if not notified and on_sent is not None:
                notified = True
                try:
                    on_sent()
                except Exception:
                    pass

        try:
            with self._action_lock:
                if callable(action):
                    action(sup)
                    return

                actions_list = action if isinstance(action, list) else [action]
                for idx, act in enumerate(actions_list):
                    last = idx == len(actions_list) - 1
                    try:
                        self._execute_single_action(act, sup, on_sent=_notify if last else None)
                    except KeySpecError as exc:
                        # Unrecognised key specifications raise now rather than
                        # being typed into the session as literal text. On this
                        # daemon thread that would otherwise be an unhandled
                        # traceback that also swallows every action queued behind
                        # it, so report it and carry on.
                        sys.stderr.write(
                            f"[termreel] Trigger action skipped: {exc} "
                            f"(use type: type_text to send literal text)\n"
                        )
                        sys.stderr.flush()
        finally:
            _notify()

    def _execute_single_action(self, act, sup: BaseSupervisor, on_sent: Optional[Callable[[], None]] = None):
        """Run one resolved trigger action against the supervisor."""
        if isinstance(act, str):
            sup.send_key(act)
            if on_sent is not None:
                on_sent()
        elif isinstance(act, TriggerAction):
            if act.delay_before > 0:
                time.sleep(act.delay_before)

            if act.action_type == ActionType.SEND_KEY:
                sup.send_key(str(act.value))
            elif act.action_type == ActionType.TYPE_TEXT:
                sup.send_text(str(act.value))
            elif act.action_type == ActionType.PAUSE:
                time.sleep(float(act.value or 0.5))
            elif act.action_type == ActionType.SELECT_CHOICE:
                choice_val = act.value
                if isinstance(choice_val, int) or (isinstance(choice_val, str) and choice_val.isdigit()):
                    choice_num = int(choice_val)
                    steps = max(0, choice_num - 1)
                    for _ in range(steps):
                        sup.send_key("Down")
                        time.sleep(0.15)
                    time.sleep(0.1)
                    sup.send_key("Enter")
                elif isinstance(choice_val, dict):
                    steps = int(choice_val.get("steps", 0))
                    direction = choice_val.get("direction", "Down")
                    confirm = choice_val.get("confirm", True)
                    confirm_key = choice_val.get("confirm_key", "Enter")
                    for _ in range(steps):
                        sup.send_key(direction)
                        time.sleep(0.15)
                    if confirm:
                        time.sleep(0.1)
                        sup.send_key(confirm_key)
                else:
                    sup.send_key("Enter")
            elif act.action_type == ActionType.CALLBACK and callable(act.value):
                act.value(sup)

            if on_sent is not None:
                on_sent()
            if act.delay_after > 0:
                time.sleep(act.delay_after)

    def _get_target_text(self, sup: Optional[BaseSupervisor], scope: str = "visible") -> str:
        """Extract terminal screen text taking scope (visible vs all scrollback) into account."""
        if not sup:
            return ""
        if scope.lower() == "all":
            if hasattr(sup, "state") and hasattr(sup.state, "get_full_text"):
                return sup.state.get_full_text()
            if hasattr(sup, "capture_plain"):
                try:
                    return sup.capture_plain(include_scrollback=True)
                except TypeError:
                    pass
        return sup.capture_plain()

    def wait_for_text(
        self,
        pattern: Union[str, Pattern],
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 30.0,
        poll_interval: float = 0.2,
        scope: str = "visible",
    ) -> bool:
        """Poll until pattern appears on screen (or scrollback) or timeout expires."""
        sup = supervisor or self.supervisor
        if not sup:
            raise ValueError("No supervisor provided.")

        regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
        start_t = time.time()

        while time.time() - start_t < timeout:
            self.evaluate_and_react(sup)
            txt = self._get_target_text(sup, scope=scope)
            if regex.search(txt):
                return True
            time.sleep(poll_interval)
        return False

    def wait_for_idle(
        self,
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 60.0,
        idle_regex: Optional[Union[str, Pattern]] = None,
        busy_regex: Optional[Union[str, Pattern]] = None,
        poll_interval: float = 0.25,
        min_stable_seconds: float = 0.5,
    ) -> bool:
        """
        Intelligently waits until the CLI finishes processing/generating and returns to an idle prompt.
        Eliminates brittle fixed sleep timers.
        """
        sup = supervisor or self.supervisor
        if not sup:
            raise ValueError("No supervisor provided.")

        idle_re = [re.compile(idle_regex, re.IGNORECASE)] if isinstance(idle_regex, str) else (
            [idle_regex] if idle_regex else DEFAULT_IDLE_PATTERNS
        )
        busy_re = [re.compile(busy_regex, re.IGNORECASE)] if isinstance(busy_regex, str) else (
            [busy_regex] if busy_regex else DEFAULT_BUSY_PATTERNS
        )

        start_t = time.time()
        # Brief grace delay before checking
        time.sleep(0.5)

        stable_since: Optional[float] = None

        while time.time() - start_t < timeout:
            self.evaluate_and_react(sup)
            txt = sup.capture_plain()

            # Check if any busy indicator is present
            is_busy = any(b.search(txt) for b in busy_re if b)

            # Check if idle prompt is present
            is_idle = any(i.search(txt) for i in idle_re if i)

            if is_idle and not is_busy:
                if stable_since is None:
                    stable_since = time.time()
                elif (time.time() - stable_since) >= min_stable_seconds:
                    return True
            else:
                stable_since = None

            time.sleep(poll_interval)

        return False

    def assert_text_present(
        self,
        pattern: Union[str, Pattern],
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 10.0,
        scope: str = "visible",
    ):
        """Assertion method verifying that text appears in the terminal screen or scrollback."""
        if not self.wait_for_text(pattern, supervisor=supervisor, timeout=timeout, scope=scope):
            sup = supervisor or self.supervisor
            current_screen = self._get_target_text(sup, scope=scope) if sup else "<no screen>"
            raise AssertionError(f"Expected pattern '{pattern}' not found in terminal ({scope}) within {timeout}s.\nCurrent content:\n{current_screen}")

    def assert_text_absent(
        self,
        pattern: Union[str, Pattern],
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 5.0,
        scope: str = "visible",
    ):
        """Assertion method verifying that text is NOT present in the terminal screen or scrollback."""
        sup = supervisor or self.supervisor
        if not sup:
            raise ValueError("No supervisor provided.")

        regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
        start_t = time.time()

        while time.time() - start_t < timeout:
            txt = self._get_target_text(sup, scope=scope)
            if regex.search(txt):
                raise AssertionError(f"Forbidden pattern '{pattern}' was found in terminal ({scope}):\n{txt}")
            time.sleep(0.2)

