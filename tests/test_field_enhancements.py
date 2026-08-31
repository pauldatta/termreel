"""
Comprehensive unit and integration tests for TermReel field enhancements:
1. Polymorphic and structured send_key (string, dict, delay_before/after, invalid shapes).
2. Unescaped newline collapse in type.text (default collapse vs multiline: true / collapse_newlines: false).
3. Shell prompt readiness (wait_for_prompt in launch and wait_for_idle).
4. inspect_modal action primitive (open_command, open_key, wait_for_render, display_duration, dismiss_key).
5. Trigger engine max_count / max_firings accurate limit enforcement.
"""

import time
import unittest
from termreel.exceptions import ScenarioValidationError
from termreel.scenario.schema import (
    ScenarioManifest,
    TimelineStep,
    TriggerConfig,
    SendKeyParams,
    LaunchParams,
    WaitForIdleParams,
    InspectModalParams,
)
from termreel.scenario.runner import ScenarioRunner
from termreel.reactor.triggers import Trigger, TriggerAction, ActionType
from termreel.reactor.monitor import ScreenMonitor
from termreel.supervisor.base import BaseSupervisor


class MockSupervisor(BaseSupervisor):
    """Mock supervisor recording all sent keystrokes, text, and providing synthetic screen output."""

    def __init__(self, screen_text: str = "$ "):
        self.screen_text = screen_text
        self.text_sent = []
        self.keys_sent = []
        self.raw_sent = []
        self.is_running = True

    def start(self) -> None:
        self.is_running = True

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        self.text_sent.append(text)

    def send_key(self, key_name: str) -> None:
        self.keys_sent.append(key_name)

    def send_raw(self, data: bytes) -> None:
        self.raw_sent.append(data)

    def paste_text(self, text: str) -> None:
        self.text_sent.append(text)

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


class TestPolymorphicSendKey(unittest.TestCase):
    """Tests for polymorphic and structured send_key action."""

    def test_send_key_plain_string_parsing(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key: "Escape"
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_str)
        self.assertEqual(len(manifest.timeline), 1)
        step = manifest.timeline[0]
        self.assertEqual(step.step_type, "send_key")
        self.assertEqual(step.params.get("key"), "Escape")

    def test_send_key_structured_dict_parsing(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key:
      key: "Escape"
      delay_before: 0.2
      delay_after: 0.5
      pause_after: 0.5
      delay: 0.3
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_str)
        self.assertEqual(len(manifest.timeline), 1)
        step = manifest.timeline[0]
        self.assertEqual(step.step_type, "send_key")
        self.assertEqual(step.params["key"], "Escape")
        self.assertEqual(step.params["delay_before"], 0.2)
        self.assertEqual(step.params["delay_after"], 0.5)

    def test_send_key_missing_key_in_dict_raises_validation_error(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key:
      delay_before: 0.5
      delay_after: 1.0
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("must contain a non-empty 'key' field", str(ctx.exception))

    def test_send_key_empty_key_in_dict_raises_validation_error(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key:
      key: ""
      delay_before: 0.5
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("must contain a non-empty 'key' field", str(ctx.exception))

    def test_send_key_invalid_shape_integer_raises_validation_error(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key: 12345
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("expected string or dictionary", str(ctx.exception))

    def test_send_key_invalid_shape_list_raises_validation_error(self):
        yaml_str = """
version: "1.0"
timeline:
  - send_key:
      - "Escape"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("expected string or dictionary", str(ctx.exception))

    def test_send_key_runner_execution_plain_string(self):
        manifest = ScenarioManifest.from_dict({"timeline": [{"send_key": "Escape"}]})
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        runner._execute_step(manifest.timeline[0], 0)
        self.assertIn("Escape", mock_sup.keys_sent)

    def test_send_key_runner_execution_structured_dict_timing(self):
        manifest = ScenarioManifest.from_dict({
            "timeline": [{
                "send_key": {
                    "key": "Enter",
                    "delay_before": 0.05,
                    "delay_after": 0.05,
                }
            }]
        })
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        t0 = time.time()
        runner._execute_step(manifest.timeline[0], 0)
        elapsed = time.time() - t0

        self.assertIn("Enter", mock_sup.keys_sent)
        self.assertGreaterEqual(elapsed, 0.09)

    def test_send_key_runner_execution_with_pause_after_fallback(self):
        manifest = ScenarioManifest.from_dict({
            "timeline": [{
                "send_key": {
                    "key": "C-c",
                    "pause_after": 0.05,
                }
            }]
        })
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        t0 = time.time()
        runner._execute_step(manifest.timeline[0], 0)
        elapsed = time.time() - t0

        self.assertIn("C-c", mock_sup.keys_sent)
        self.assertGreaterEqual(elapsed, 0.04)

    def test_send_key_runner_invalid_params_raises_validation_error(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        runner.supervisor = MockSupervisor()
        with self.assertRaises(ScenarioValidationError):
            runner._execute_send_key({"delay_before": 0.1})


class TestTypeNewlineCollapse(unittest.TestCase):
    """Tests for newline collapsing in type.text."""

    def test_type_newline_collapse_default(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        params = {
            "text": "echo 'first line'\n  echo 'second line'",
            "speed": 0.0001,
            "pause": 0.0,
        }
        runner._execute_type(params)

        full_typed = "".join(mock_sup.text_sent)
        self.assertNotIn("\n", full_typed)
        self.assertEqual(full_typed, "echo 'first line' echo 'second line'")

    def test_type_multiple_newlines_collapse_to_single_space(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        params = {
            "text": "command --opt1  \n\n  --opt2",
            "speed": 0.0001,
            "pause": 0.0,
        }
        runner._execute_type(params)

        full_typed = "".join(mock_sup.text_sent)
        self.assertEqual(full_typed, "command --opt1 --opt2")

    def test_type_multiline_true_preserves_newlines(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        params = {
            "text": "line1\nline2",
            "multiline": True,
            "speed": 0.0001,
            "pause": 0.0,
        }
        runner._execute_type(params)

        sent = "".join(mock_sup.text_sent)
        keys = mock_sup.keys_sent
        self.assertTrue("\n" in sent or "Enter" in keys or "Return" in keys)

    def test_type_collapse_newlines_false_preserves_newlines(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        params = {
            "text": "line1\nline2",
            "collapse_newlines": False,
            "speed": 0.0001,
            "pause": 0.0,
        }
        runner._execute_type(params)

        sent = "".join(mock_sup.text_sent)
        keys = mock_sup.keys_sent
        self.assertTrue("\n" in sent or "Enter" in keys or "Return" in keys)

    def test_type_leading_trailing_newlines_handled_cleanly(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor()
        runner.supervisor = mock_sup

        params = {
            "text": "\ngit status\n",
            "speed": 0.0001,
            "pause": 0.0,
        }
        runner._execute_type(params)

        full_typed = "".join(mock_sup.text_sent)
        self.assertEqual(full_typed, "git status")


class TestShellPromptReadiness(unittest.TestCase):
    """Tests for wait_for_prompt on launch and wait_for_idle."""

    def test_launch_wait_for_prompt_success(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="pauldatta@cloudtop:~$ ")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="launch",
            params={
                "command": "bash",
                "wait_for_prompt": True,
                "prompt_pattern": r"([$#>]\s*$|%\s*$)",
                "prompt_timeout": 2.0,
            }
        )
        from unittest.mock import patch
        with patch("termreel.scenario.runner.create_supervisor", return_value=mock_sup):
            runner._execute_step(step, 0)
        self.assertTrue(mock_sup.is_alive())

    def test_launch_wait_for_prompt_timeout_raises(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="Compiling source files...")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="launch",
            params={
                "command": "bash",
                "wait_for_prompt": True,
                "prompt_pattern": r"([$#>]\s*$|%\s*$)",
                "prompt_timeout": 0.1,
            }
        )
        from unittest.mock import patch
        with patch("termreel.scenario.runner.create_supervisor", return_value=mock_sup):
            with self.assertRaises(TimeoutError) as ctx:
                runner._execute_step(step, 0)
            self.assertIn("Timed out waiting for prompt pattern", str(ctx.exception))

    def test_wait_for_idle_wait_for_prompt_success(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="Operation complete.\nuser@host:~/repo$ ")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="wait_for_idle",
            params={
                "wait_for_prompt": True,
                "prompt_pattern": r"([$#>]\s*$|%\s*$)",
                "timeout": 2.0,
                "reading_pause": 0.0,
            }
        )
        runner._execute_step(step, 0)

    def test_wait_for_idle_wait_for_prompt_timeout_raises(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="Still executing background work without prompt...")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="wait_for_idle",
            params={
                "wait_for_prompt": True,
                "prompt_pattern": r"([$#>]\s*$|%\s*$)",
                "prompt_timeout": 0.1,
                "timeout": 0.5,
                "reading_pause": 0.0,
            }
        )
        with self.assertRaises(TimeoutError) as ctx:
            runner._execute_step(step, 0)
        self.assertIn("Prompt pattern", str(ctx.exception))


class TestInspectModal(unittest.TestCase):
    """Tests for inspect_modal timeline action primitive."""

    def test_inspect_modal_schema_parsing(self):
        yaml_str = """
version: "1.0"
timeline:
  - inspect_modal:
      open_command: "/context"
      wait_for_render: "Context Inspector"
      display_duration: 1.5
      dismiss_key: "Escape"
      pause_after: 0.2
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_str)
        self.assertEqual(len(manifest.timeline), 1)
        step = manifest.timeline[0]
        self.assertEqual(step.step_type, "inspect_modal")
        self.assertEqual(step.params["open_command"], "/context")
        self.assertEqual(step.params["wait_for_render"], "Context Inspector")
        self.assertEqual(step.params["display_duration"], 1.5)
        self.assertEqual(step.params["dismiss_key"], "Escape")
        self.assertEqual(step.params["pause_after"], 0.2)

    def test_inspect_modal_execution_open_command(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="Context Inspector Modal [Ready]")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="inspect_modal",
            params={
                "open_command": "/context",
                "wait_for_render": "Context Inspector",
                "display_duration": 0.05,
                "dismiss_key": "Escape",
                "pause_after": 0.05,
                "speed": 0.0001,
            }
        )

        runner._execute_step(step, 0)

        full_typed = "".join(mock_sup.text_sent)
        self.assertIn("/context", full_typed)
        self.assertIn("Enter", mock_sup.keys_sent)
        self.assertIn("Escape", mock_sup.keys_sent)

    def test_inspect_modal_execution_open_key(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="File Search Picker")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="inspect_modal",
            params={
                "open_key": "C-o",
                "wait_for_render": "File Search",
                "display_duration": 0.05,
                "dismiss_key": "Escape",
                "pause_after": 0.05,
            }
        )

        runner._execute_step(step, 0)

        self.assertIn("C-o", mock_sup.keys_sent)
        self.assertIn("Escape", mock_sup.keys_sent)

    def test_inspect_modal_wait_for_render_timeout_raises(self):
        runner = ScenarioRunner(manifest=ScenarioManifest(), verbose=False)
        mock_sup = MockSupervisor(screen_text="Regular console screen")
        runner.supervisor = mock_sup
        runner.monitor.supervisor = mock_sup

        step = TimelineStep(
            step_type="inspect_modal",
            params={
                "open_key": "C-p",
                "wait_for_render": "Modal Window Not Appearing",
                "timeout": 0.1,
                "display_duration": 0.01,
            }
        )

        with self.assertRaises(TimeoutError) as ctx:
            runner._execute_step(step, 0)
        self.assertIn("Modal render pattern", str(ctx.exception))


class TestTriggerMaxCount(unittest.TestCase):
    """Tests for Trigger max_count and max_firings accurate limit enforcement."""

    def test_trigger_max_count_limits_firing(self):
        trig = Trigger(
            pattern=r"Confirm\?",
            action=TriggerAction(action_type=ActionType.SEND_KEY, value="Enter"),
            max_count=3,
            cooldown_seconds=0.0,
        )

        self.assertFalse(trig.once)
        self.assertEqual(trig.max_count, 3)
        self.assertEqual(trig.max_firings, 3)

        # Fire 1
        self.assertTrue(trig.can_fire(time.time()))
        trig.mark_fired(time.time())
        self.assertEqual(trig.times_fired, 1)

        # Fire 2
        self.assertTrue(trig.can_fire(time.time() + 0.1))
        trig.mark_fired(time.time() + 0.1)
        self.assertEqual(trig.times_fired, 2)

        # Fire 3
        self.assertTrue(trig.can_fire(time.time() + 0.2))
        trig.mark_fired(time.time() + 0.2)
        self.assertEqual(trig.times_fired, 3)

        # Attempt fire 4 -> should be rejected
        self.assertFalse(trig.can_fire(time.time() + 0.3))

    def test_trigger_config_manifest_parsing_with_max_count(self):
        yaml_str = """
version: "1.0"
timeline: []
triggers:
  - on_match: 'Do you want to continue\\?'
    action: "y"
    max_count: 5
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_str)
        self.assertEqual(len(manifest.triggers), 1)
        tc = manifest.triggers[0]
        self.assertEqual(tc.max_count, 5)
        self.assertEqual(tc.max_firings, 5)
        self.assertFalse(tc.once)

    def test_screen_monitor_evaluate_and_react_stops_at_max_count(self):
        monitor = ScreenMonitor()
        mock_sup = MockSupervisor(screen_text="Do you want to continue? [y/N]")
        monitor.supervisor = mock_sup

        trig = Trigger(
            pattern=r"Do you want to continue\?",
            action=TriggerAction(action_type=ActionType.SEND_KEY, value="y"),
            max_count=2,
            cooldown_seconds=0.0,
        )
        monitor.add_trigger(trig)

        # Evaluation 1
        fired1 = monitor.evaluate_and_react(mock_sup, async_action=False)
        self.assertEqual(len(fired1), 1)
        self.assertEqual(trig.times_fired, 1)

        # Evaluation 2
        fired2 = monitor.evaluate_and_react(mock_sup, async_action=False)
        self.assertEqual(len(fired2), 1)
        self.assertEqual(trig.times_fired, 2)

        # Evaluation 3 -> Should not fire because max_count is 2
        fired3 = monitor.evaluate_and_react(mock_sup, async_action=False)
        self.assertEqual(len(fired3), 0)
        self.assertEqual(trig.times_fired, 2)


class TestEndToEndFieldEnhancementsScenario(unittest.TestCase):
    """End-to-end integration test with live PTY and ScenarioRunner."""

    def test_run_manifest_with_all_enhancements(self):
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            out_mp4 = f.name

        yaml_content = f"""
version: "1.0"
metadata:
  title: "Field Enhancements Integration"
  output: "{out_mp4}"
  resolution: [640, 360]
  fps: 15
timeline:
  - launch:
      command: "bash"
      wait_for_prompt: true
      prompt_timeout: 5.0
  - type:
      text: |
        echo "Line one"
        echo "Line two"
      speed: 0.01
      send_key: "Enter"
      pause: 0.2
  - send_key:
      key: "Enter"
      delay_before: 0.05
      delay_after: 0.05
  - inspect_modal:
      open_command: "echo 'MODAL_CONTENT'"
      wait_for_render: "MODAL_CONTENT"
      display_duration: 0.1
      dismiss_key: "Enter"
      pause_after: 0.1
  - wait_for_idle:
      wait_for_prompt: true
      timeout: 5.0
      reading_pause: 0.1
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_content)
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        report = runner.run()

        self.assertEqual(report.status, "pass")
        self.assertTrue(os.path.exists(out_mp4))
        self.assertGreater(os.path.getsize(out_mp4), 500)

        if os.path.exists(out_mp4):
            os.unlink(out_mp4)


if __name__ == "__main__":
    unittest.main()
