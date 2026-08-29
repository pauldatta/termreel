"""
Screen state monitor, event reactor, idle detector, and verification assertions.
"""

import re
import threading
import time
from typing import List, Optional, Pattern, Union, Callable
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
            screen_text = sup.capture_plain()
            now = time.time()
            fired = []

            for trig in self.triggers:
                if trig.can_fire(now) and trig.matches(screen_text):
                    trig.mark_fired(now)
                    fired.append(trig)
                    if async_action:
                        t = threading.Thread(
                            target=self._execute_action,
                            args=(trig.action, sup),
                            daemon=True,
                        )
                        t.start()
                        self._action_threads.append(t)
                    else:
                        self._execute_action(trig.action, sup)

            # Cleanup finished action threads
            if self._action_threads:
                self._action_threads = [t for t in self._action_threads if t.is_alive()]

            return fired

    def _execute_action(self, action: Union[TriggerAction, List[TriggerAction], Callable, str], sup: BaseSupervisor):
        """Execute the resolved action on the supervisor in a thread-safe manner."""
        with self._action_lock:
            if callable(action):
                action(sup)
                return

            actions_list = action if isinstance(action, list) else [action]
            for act in actions_list:
                if isinstance(act, str):
                    sup.send_key(act)
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

                    if act.delay_after > 0:
                        time.sleep(act.delay_after)

    def wait_for_text(
        self,
        pattern: Union[str, Pattern],
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 30.0,
        poll_interval: float = 0.2,
    ) -> bool:
        """Poll until pattern appears on screen or timeout expires."""
        sup = supervisor or self.supervisor
        if not sup:
            raise ValueError("No supervisor provided.")

        regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
        start_t = time.time()

        while time.time() - start_t < timeout:
            self.evaluate_and_react(sup)
            txt = sup.capture_plain()
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
    ):
        """Assertion method verifying that text appears in the terminal."""
        if not self.wait_for_text(pattern, supervisor=supervisor, timeout=timeout):
            sup = supervisor or self.supervisor
            current_screen = sup.capture_plain() if sup else "<no screen>"
            raise AssertionError(f"Expected pattern '{pattern}' not found on terminal screen within {timeout}s.\nCurrent screen:\n{current_screen}")

    def assert_text_absent(
        self,
        pattern: Union[str, Pattern],
        supervisor: Optional[BaseSupervisor] = None,
        timeout: float = 5.0,
    ):
        """Assertion method verifying that text is NOT present in the terminal."""
        sup = supervisor or self.supervisor
        if not sup:
            raise ValueError("No supervisor provided.")

        regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
        start_t = time.time()

        while time.time() - start_t < timeout:
            txt = sup.capture_plain()
            if regex.search(txt):
                raise AssertionError(f"Forbidden pattern '{pattern}' was found on screen:\n{txt}")
            time.sleep(0.2)
