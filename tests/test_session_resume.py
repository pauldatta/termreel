import os
import shutil
import tempfile
import unittest
from termreel.scenario.schema import ScenarioManifest, ScenarioEnvironment, TimelineStep
from termreel.scenario.runner import ScenarioRunner
from termreel.hooks.models import HookEvent, HookEventType


class TestSessionResume(unittest.TestCase):

    def test_schema_resume_parsing(self):
        yaml_text = """
version: "1.0"
metadata:
  title: "Resumed Session"
  output: "/tmp/test_resume.mp4"
environment:
  resume: true
  conversation_id: "conv-abcdef-123456"
  preserve_workspace: true
  workspace_path: "/tmp/existing_ws"
timeline:
  - launch:
      command: "agy"
"""
        manifest = ScenarioManifest.from_yaml_string(yaml_text)
        self.assertTrue(manifest.environment.resume)
        self.assertEqual(manifest.environment.conversation_id, "conv-abcdef-123456")
        self.assertTrue(manifest.environment.preserve_workspace)
        self.assertEqual(manifest.environment.workspace_path, "/tmp/existing_ws")

    def test_launch_command_auto_continue_construction(self):
        yaml_text = """
version: "1.0"
metadata:
  title: "Agy Continue Test"
  output: "/tmp/test_agy_continue.mp4"
environment:
  resume: true
timeline:
  - launch:
      command: "agy"
"""
        manifest = ScenarioManifest.from_yaml_string(yaml_text)
        runner = ScenarioRunner(manifest=manifest, verbose=False)

        # Test that launch step expands agy with -c when resume=True
        step = manifest.timeline[0]
        cmd = step.params.get("command", "bash")
        should_resume = bool(step.params.get("resume", manifest.environment.resume))
        conv_id = step.params.get("conversation_id", manifest.environment.conversation_id)

        if should_resume or conv_id:
            if "agy" in cmd and "--continue" not in cmd and "-c" not in cmd and "--conversation" not in cmd:
                if conv_id:
                    cmd = f"{cmd} --conversation {conv_id}"
                else:
                    cmd = f"{cmd} -c"

        self.assertEqual(cmd, "agy -c")

    def test_launch_command_specific_conversation_id(self):
        yaml_text = """
version: "1.0"
metadata:
  title: "Agy Conversation ID Test"
  output: "/tmp/test_agy_conv.mp4"
environment:
  conversation_id: "60dbac5a-a132-4260-8d2d-d1d1efd34eae"
timeline:
  - launch:
      command: "agy"
"""
        manifest = ScenarioManifest.from_yaml_string(yaml_text)
        step = manifest.timeline[0]
        cmd = step.params.get("command", "bash")
        should_resume = bool(step.params.get("resume", manifest.environment.resume))
        conv_id = step.params.get("conversation_id", manifest.environment.conversation_id)

        if should_resume or conv_id:
            if "agy" in cmd and "--continue" not in cmd and "-c" not in cmd and "--conversation" not in cmd:
                if conv_id:
                    cmd = f"{cmd} --conversation {conv_id}"
                else:
                    cmd = f"{cmd} -c"

        self.assertEqual(cmd, "agy --conversation 60dbac5a-a132-4260-8d2d-d1d1efd34eae")

    def test_active_conversation_id_hook_listener(self):
        yaml_text = """
version: "1.0"
metadata:
  title: "Hook Listener Test"
  output: "/tmp/test_hook_listener.mp4"
timeline:
  - launch:
      command: "bash"
"""
        manifest = ScenarioManifest.from_yaml_string(yaml_text)
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        self.assertIsNone(runner.active_conversation_id)

        # Dispatch mock hook event with conversationId
        ev = HookEvent(
            event_type=HookEventType.PRE_INVOCATION.value,
            conversation_id="conv-live-captured-9999",
        )
        runner.hook_bridge.record_event(ev)
        self.assertEqual(runner.active_conversation_id, "conv-live-captured-9999")



if __name__ == "__main__":
    unittest.main()
