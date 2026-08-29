"""
Unit and integration tests for reactive UI permission and human-in-the-loop interceptors.
"""

import threading
import time
import unittest
from termreel.reactor.triggers import (
    ActionType,
    Trigger,
    TriggerAction,
    create_agy_permission_dialog_trigger,
    create_permission_prompt_trigger,
    create_trust_dialog_trigger,
    create_yes_no_prompt_trigger,
)
from termreel.reactor.monitor import ScreenMonitor
from termreel.supervisor.base import BaseSupervisor


class MockSupervisor(BaseSupervisor):
    def __init__(self, screen_text: str = ""):
        self.screen_text = screen_text
        self.keys_sent = []
        self.text_sent = []
        self.is_running = True

    def start(self) -> None:
        self.is_running = True

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        self.text_sent.append(text)

    def send_key(self, key_name: str) -> None:
        self.keys_sent.append(key_name)

    def send_raw(self, data: bytes) -> None:
        pass

    def paste_text(self, text: str) -> None:
        pass

    def capture_ansi(self) -> str:
        return self.screen_text

    def capture_plain(self) -> str:
        return self.screen_text

    def resize(self, rows: int, cols: int) -> None:
        pass

    def is_alive(self) -> bool:
        return self.is_running

    def terminate(self) -> None:
        self.is_running = False


class TestPermissionInterceptor(unittest.TestCase):
    """Test reactive UI interception of interactive permission dialogs."""

    def test_intercept_agy_permission_dialog(self):
        prompt_screen = """
Requesting permission for:
  python3 app.py

Do you want to proceed?
> 1. Yes
  2. Yes, and always allow in this conversation for commands that start with 'python3 app.py'
  3. Yes, and always allow for commands that start with 'python3 app.py' (Persist to settings.json)
  4. No
"""
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        # Trigger with minimal delay for fast test
        trig = create_agy_permission_dialog_trigger(choice=1, delay_before=0.01, delay_after=0.01)
        monitor.add_trigger(trig)

        fired = monitor.evaluate_and_react(sup)
        self.assertEqual(len(fired), 1)
        self.assertIn("Enter", sup.keys_sent)
        self.assertEqual(trig.times_fired, 1)

    def test_intercept_agy_choice_selection(self):
        prompt_screen = """
Requesting permission for:
  pytest -v

Do you want to proceed?
> 1. Yes
  2. Yes, and always allow in this conversation
  3. No
"""
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        # Choice 2 sends Down then Enter
        trig = create_agy_permission_dialog_trigger(choice=2, delay_before=0.01, delay_after=0.01)
        monitor.add_trigger(trig)

        fired = monitor.evaluate_and_react(sup)
        self.assertEqual(len(fired), 1)
        self.assertEqual(sup.keys_sent, ["Down", "Enter"])

    def test_intercept_yes_no_prompt(self):
        prompt_screen = "Do you want to apply this migration? [y/N]: "
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        trig = create_yes_no_prompt_trigger(response="y", delay_before=0.01, delay_after=0.01)
        monitor.add_trigger(trig)

        fired = monitor.evaluate_and_react(sup)
        self.assertEqual(len(fired), 1)
        self.assertEqual(sup.text_sent, ["y"])
        self.assertEqual(sup.keys_sent, ["Enter"])

    def test_intercept_trust_dialog(self):
        prompt_screen = "Do you trust the contents of this project workspace?"
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        trig = create_trust_dialog_trigger(delay_before=0.01, delay_after=0.01)
        monitor.add_trigger(trig)

        fired = monitor.evaluate_and_react(sup)
        self.assertEqual(len(fired), 1)
        self.assertEqual(sup.keys_sent, ["Enter"])

    def test_thread_safe_trigger_firing(self):
        """Verify concurrent calls to evaluate_and_react only fire once per cooldown."""
        prompt_screen = "Requesting permission for: git push\nDo you want to proceed?\n> 1. Yes"
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        trig = create_agy_permission_dialog_trigger(choice=1, delay_before=0.01, delay_after=0.01)
        trig.once = True
        monitor.add_trigger(trig)

        errors = []

        def worker():
            try:
                monitor.evaluate_and_react(sup)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(sup.keys_sent, ["Enter"])
        self.assertEqual(trig.times_fired, 1)

    def test_async_action_execution_and_wait(self):
        """Verify async action dispatching runs non-blockingly and wait_for_actions joins cleanly."""
        prompt_screen = "Requesting permission for:\n  python3 app.py\nDo you want to proceed?\n> 1. Yes"
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        trig = create_agy_permission_dialog_trigger(choice=1, delay_before=0.05, delay_after=0.01)
        monitor.add_trigger(trig)

        # Dispatched with async_action=True
        fired = monitor.evaluate_and_react(sup, async_action=True)
        self.assertEqual(len(fired), 1)
        # Immediately returns without waiting for delay_before
        monitor.wait_for_actions(timeout=1.0)
        self.assertIn("Enter", sup.keys_sent)

    def test_choice_3_and_string_digits(self):
        """Verify selecting choice 3 sends two Down keypresses and Enter."""
        prompt_screen = "Requesting permission for: pytest\nDo you want to proceed?\n> 1. Yes\n  2. Yes (conversation)\n  3. Yes (persist)\n  4. No"
        sup = MockSupervisor(screen_text=prompt_screen)
        monitor = ScreenMonitor(supervisor=sup)

        trig = create_agy_permission_dialog_trigger(choice=3, delay_before=0.01, delay_after=0.01)
        monitor.add_trigger(trig)

        fired = monitor.evaluate_and_react(sup)
        self.assertEqual(len(fired), 1)
        self.assertEqual(sup.keys_sent, ["Down", "Down", "Enter"])

    def test_various_permission_dialog_patterns(self):
        """Verify diverse CLI dialog patterns match properly."""
        patterns_to_test = [
            "Permission required: python3 setup.py",
            "Grant permission to execute /tmp/bin?",
            "Do you want to run bash build.sh?",
            "Approve change to index.html?",
            "Allow tool call: edit_file",
            "Human-in-the-loop: approve execution?",
        ]
        for pat_text in patterns_to_test:
            sup = MockSupervisor(screen_text=pat_text)
            monitor = ScreenMonitor(supervisor=sup)
            trig = create_agy_permission_dialog_trigger(choice=1, delay_before=0.01, delay_after=0.01)
            monitor.add_trigger(trig)
            fired = monitor.evaluate_and_react(sup)
            self.assertEqual(len(fired), 1, f"Pattern failed to match: {pat_text}")
            self.assertIn("Enter", sup.keys_sent)


if __name__ == "__main__":
    unittest.main()
