"""
Unit tests for Antigravity (agy) hooks, presets, bridge, and HookManager.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from termreel.hooks.models import (
    HookEventType,
    HookDecision,
    HookResult,
    HookEvent,
    HookHandlerConfig,
)
from termreel.hooks.bridge import AgyHookBridge
from termreel.hooks.presets import (
    generate_hook_script,
    create_agy_hooks_config,
    create_auto_approve_policy,
)
from termreel.hooks.manager import HookManager
from termreel.scenario.schema import ScenarioManifest, TimelineStep


class TestHookModels(unittest.TestCase):
    """Test data models and event serialization."""

    def test_hook_event_type_normalization(self):
        self.assertEqual(HookEventType.from_string("pre_tool_use"), HookEventType.PRE_TOOL_USE)
        self.assertEqual(HookEventType.from_string("PreToolUse"), HookEventType.PRE_TOOL_USE)
        self.assertEqual(HookEventType.from_string("post_tool"), HookEventType.POST_TOOL_USE)
        self.assertEqual(HookEventType.from_string("pre_turn"), HookEventType.PRE_INVOCATION)
        self.assertEqual(HookEventType.from_string("post_invocation"), HookEventType.POST_INVOCATION)
        self.assertEqual(HookEventType.from_string("stop"), HookEventType.STOP)
        self.assertEqual(HookEventType.from_string("session_start"), HookEventType.SESSION_START)
        self.assertEqual(HookEventType.from_string("session_end"), HookEventType.SESSION_END)
        # Verify unknown custom string does not throw ValueError
        self.assertEqual(HookEventType.from_string("nonexistent_event"), HookEventType.PRE_TOOL_USE)

    def test_hook_result_serialization(self):
        res = HookResult(
            allow=True,
            decision="allow",
            reason="Approved by policy",
            permission_overrides=["command(npm test)"],
            inject_steps=[{"ephemeralMessage": "hello"}],
        )
        d = res.to_dict()
        self.assertTrue(d["allow"])
        self.assertEqual(d["decision"], "allow")
        self.assertEqual(d["reason"], "Approved by policy")
        self.assertEqual(d["permissionOverrides"], ["command(npm test)"])
        self.assertEqual(d["injectSteps"], [{"ephemeralMessage": "hello"}])

        deser = HookResult.from_dict(d)
        self.assertTrue(deser.allow)
        self.assertEqual(deser.decision, "allow")
        self.assertEqual(deser.reason, "Approved by policy")
        self.assertEqual(deser.permission_overrides, ["command(npm test)"])

    def test_hook_event_serialization(self):
        ev = HookEvent(
            event_type="PreToolUse",
            tool_name="read_file",
            tool_args={"path": "main.py"},
            decision="allow",
            step_idx=5,
            session_id="conv-12345",
        )
        json_str = ev.to_json()
        deser = HookEvent.from_json(json_str)
        self.assertEqual(deser.event_type, "PreToolUse")
        self.assertEqual(deser.tool_name, "read_file")
        self.assertEqual(deser.tool_args, {"path": "main.py"})
        self.assertEqual(deser.decision, "allow")
        self.assertEqual(deser.step_idx, 5)
        self.assertEqual(deser.session_id, "conv-12345")

    def test_hook_event_from_antigravity_protojson(self):
        """Verify parsing authentic Antigravity camelCase protojson payload."""
        agy_payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "/workspace/main.py",
                    "CodeContent": "print('hello')",
                },
            },
            "stepIdx": 12,
            "conversationId": "ag-sess-9988",
            "workspacePaths": ["/workspace"],
            "modelName": "gemini-2.5-pro",
        }
        ev = HookEvent.from_dict(agy_payload)
        self.assertEqual(ev.tool_name, "write_to_file")
        self.assertEqual(ev.tool_args["TargetFile"], "/workspace/main.py")
        self.assertEqual(ev.step_idx, 12)
        self.assertEqual(ev.session_id, "ag-sess-9988")
        self.assertEqual(ev.model_name, "gemini-2.5-pro")


class TestHookBridge(unittest.TestCase):
    """Test AgyHookBridge real-time event ingestion and assertions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events_file = os.path.join(self.temp_dir, "events.jsonl")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_bridge_record_and_get_events(self):
        bridge = AgyHookBridge(events_file=self.events_file)
        bridge.start()

        ev1 = HookEvent(event_type="SessionStart")
        ev2 = HookEvent(event_type="PreToolUse", tool_name="write_file", decision="allow")
        ev3 = HookEvent(event_type="PreToolUse", tool_name="dangerous_tool", decision="deny")
        ev4 = HookEvent(event_type="PostToolUse", tool_name="write_file")

        bridge.record_event(ev1)
        bridge.record_event(ev2)
        bridge.record_event(ev3)
        bridge.record_event(ev4)

        evs = bridge.get_events(event_type="PreToolUse")
        self.assertEqual(len(evs), 2)

        # Test decision filtering
        allowed_evs = bridge.get_events(event_type="PreToolUse", decision="allow")
        self.assertEqual(len(allowed_evs), 1)
        self.assertEqual(allowed_evs[0].tool_name, "write_file")

        denied_evs = bridge.get_events(event_type="PreToolUse", decision="deny")
        self.assertEqual(len(denied_evs), 1)
        self.assertEqual(denied_evs[0].tool_name, "dangerous_tool")

        # Test wait_for_event
        found = bridge.wait_for_event(event_type="PostToolUse", tool_name="write_file", timeout=1.0)
        self.assertIsNotNone(found)
        self.assertEqual(found.tool_name, "write_file")

        # Test assertions
        bridge.assert_event_present("PreToolUse", tool_name="write_file", decision="allow", timeout=1.0)
        bridge.assert_event_present("PreToolUse", tool_name="dangerous_tool", decision="deny", timeout=1.0)
        bridge.assert_event_absent("PreToolUse", tool_name="nonexistent_tool", timeout=0.1)

        bridge.stop()

    def test_bridge_background_file_tailing(self):
        bridge = AgyHookBridge(events_file=self.events_file)
        bridge.start()

        # Write directly to file like an external process would
        ev = HookEvent(event_type="PreInvocation", prompt="Refactor database schema")
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(ev.to_json() + "\n")

        # Bridge should tail and discover event
        found = bridge.wait_for_event(event_type="PreInvocation", timeout=2.0)
        self.assertIsNotNone(found)
        self.assertEqual(found.prompt, "Refactor database schema")

        bridge.stop()


class TestHookScriptExecution(unittest.TestCase):
    """Test standalone generated hook script execution via subprocess."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.events_file = os.path.join(self.temp_dir, "bridge_events.jsonl")
        self.script_path = os.path.join(self.temp_dir, "hook.py")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_generated_script_antigravity_pre_tool_use_auto_approves(self):
        """Test with authentic Antigravity PreToolUse payload format."""
        script_src = generate_hook_script(
            bridge_file=self.events_file,
            auto_approve=True,
            log_events=True,
        )
        with open(self.script_path, "w", encoding="utf-8") as f:
            f.write(script_src)

        # Authentic Antigravity PreToolUse payload sent to stdin
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "pytest -v"},
            },
            "stepIdx": 3,
            "conversationId": "ag-test-session",
        }
        res = subprocess.run(
            [sys.executable, self.script_path, "PreToolUse"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout.strip())
        self.assertTrue(out.get("allow"))
        self.assertEqual(out.get("decision"), "allow")

        # Verify event was logged to bridge file with extracted tool_name
        self.assertTrue(os.path.exists(self.events_file))
        with open(self.events_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        logged_ev = json.loads(lines[0])
        self.assertEqual(logged_ev["tool_name"], "run_command")
        self.assertEqual(logged_ev["tool_args"]["CommandLine"], "pytest -v")
        self.assertEqual(logged_ev["session_id"], "ag-test-session")
        self.assertEqual(logged_ev["step_idx"], 3)

    def test_generated_script_custom_policy_denial(self):
        policy = {"dangerous_exec": "deny", "read_file": "allow"}
        script_src = generate_hook_script(
            bridge_file=self.events_file,
            auto_approve=True,
            custom_policy=policy,
        )
        with open(self.script_path, "w", encoding="utf-8") as f:
            f.write(script_src)

        payload = {
            "toolCall": {
                "name": "dangerous_exec",
                "args": {"cmd": "rm -rf /"},
            },
            "stepIdx": 1,
        }
        res = subprocess.run(
            [sys.executable, self.script_path, "PreToolUse"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )
        # Hooks exit with code 0 to pass verdict JSON to agy
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout.strip())
        self.assertFalse(out.get("allow"))
        self.assertEqual(out.get("decision"), "deny")
        self.assertIn("reason", out)

    def test_generated_script_lifecycle_events_contracts(self):
        """Test output JSON contracts for PreInvocation, PostInvocation, PostToolUse, Stop."""
        script_src = generate_hook_script(
            bridge_file=self.events_file,
            auto_approve=True,
            log_events=True,
        )
        with open(self.script_path, "w", encoding="utf-8") as f:
            f.write(script_src)

        # 1. PreInvocation -> {"injectSteps": []}
        res = subprocess.run(
            [sys.executable, self.script_path, "PreInvocation"],
            input=json.dumps({"invocationNum": 1}),
            text=True,
            capture_output=True,
        )
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout.strip())
        self.assertIn("injectSteps", out)

        # 2. PostToolUse -> {}
        res = subprocess.run(
            [sys.executable, self.script_path, "PostToolUse"],
            input=json.dumps({"stepIdx": 1, "toolOutput": "success"}),
            text=True,
            capture_output=True,
        )
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout.strip())
        self.assertEqual(out, {})


class TestHookManager(unittest.TestCase):
    """Test workspace provisioning and cleanup of hooks."""

    def setUp(self):
        self.temp_ws = tempfile.mkdtemp()
        self.bridge = AgyHookBridge()

    def tearDown(self):
        shutil.rmtree(self.temp_ws, ignore_errors=True)

    def test_provision_and_cleanup(self):
        hm = HookManager(workspace_dir=self.temp_ws, bridge=self.bridge, auto_approve=True)
        prov = hm.provision()

        self.assertTrue(os.path.exists(prov["hooks_json"]))
        self.assertTrue(os.path.exists(prov["hook_script"]))

        with open(prov["hooks_json"], "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # Top-level key must strictly be the hook name (e.g. 'termreel')
        self.assertIn("termreel", cfg)
        self.assertIsInstance(cfg["termreel"], dict)
        self.assertIn("PreToolUse", cfg["termreel"])
        self.assertIn("PostToolUse", cfg["termreel"])
        self.assertIn("PreInvocation", cfg["termreel"])
        self.assertIn("Stop", cfg["termreel"])
        # Must NOT pollute top-level with raw event keys
        self.assertNotIn("PreToolUse", cfg.keys() - {"termreel"})

        # Clean up
        hm.cleanup()
        self.assertFalse(os.path.exists(prov["hooks_json"]))
        self.assertFalse(os.path.exists(prov["hook_script"]))


class TestScenarioManifestWithHooks(unittest.TestCase):
    """Test parsing YAML scenario manifest with hooks configuration."""

    def test_parse_manifest_with_hooks(self):
        yaml_content = """
version: "1.0"
metadata:
  title: "Agy Interactive Recording with Hooks"
  output: "output/agy_hooks.mp4"
environment:
  agy_hooks: true
  agy_auto_approve: true
  agy_event_bridge: true
  agy_custom_policy:
    dangerous_tool: deny
timeline:
  - launch:
      command: "bash"
  - wait_for_hook_event:
      event: "PreInvocation"
      timeout: 10.0
  - assert_hook_event:
      event: "PreInvocation"
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_content)
        self.assertTrue(manifest.environment.agy_hooks)
        self.assertTrue(manifest.environment.agy_auto_approve)
        self.assertTrue(manifest.environment.agy_event_bridge)
        self.assertEqual(manifest.environment.agy_custom_policy.get("dangerous_tool"), "deny")
        self.assertEqual(len(manifest.timeline), 3)
        self.assertEqual(manifest.timeline[1].step_type, "wait_for_hook_event")
        self.assertEqual(manifest.timeline[2].step_type, "assert_hook_event")


if __name__ == "__main__":
    unittest.main()
