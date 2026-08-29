"""
Integration tests for Antigravity (agy) hooks in TermReel scenario executions.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner
from termreel.hooks.models import HookEvent


class TestAgyHooksIntegration(unittest.TestCase):
    """Test full scenario execution with hooks active."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_hook_test_")
        self.output_mp4 = os.path.join(self.temp_dir, "hooks_session.mp4")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_runner_with_hooks_auto_approval_and_events(self):
        # Create a mock agent script that interacts with .agents/hooks/termreel_hook.py
        mock_agent_path = os.path.join(self.temp_dir, "mock_agent.sh")
        with open(mock_agent_path, "w", encoding="utf-8") as f:
            f.write("""#!/bin/bash
echo "Agent starting up..."
# Invoke PreInvocation hook
if [ -f .agents/hooks/termreel_hook.py ]; then
    echo '{"event_type": "PreInvocation", "prompt": "list files"}' | python3 .agents/hooks/termreel_hook.py
fi

# Invoke PreToolUse hook
if [ -f .agents/hooks/termreel_hook.py ]; then
    DECISION=$(echo '{"event_type": "PreToolUse", "tool_name": "list_files", "tool_args": {"dir": "."}}' | python3 .agents/hooks/termreel_hook.py)
    echo "Hook decision: $DECISION"
fi

# Tool work
echo "file1.txt file2.txt"

# Invoke PostToolUse hook
if [ -f .agents/hooks/termreel_hook.py ]; then
    echo '{"event_type": "PostToolUse", "tool_name": "list_files", "tool_output": "file1.txt"}' | python3 .agents/hooks/termreel_hook.py
fi

# Invoke PostInvocation hook
if [ -f .agents/hooks/termreel_hook.py ]; then
    echo '{"event_type": "PostInvocation", "response": "Found files"}' | python3 .agents/hooks/termreel_hook.py
fi
echo "Agent finished."
""")
        os.chmod(mock_agent_path, 0o755)

        manifest_dict = {
            "version": "1.0",
            "metadata": {
                "title": "Agy Hooks Test",
                "output": self.output_mp4,
                "fps": 15,
            },
            "environment": {
                "cwd": self.temp_dir,
                "agy_hooks": True,
                "agy_auto_approve": True,
                "agy_event_bridge": True,
            },
            "timeline": [
                {
                    "launch": {
                        "command": "bash",
                    }
                },
                {
                    "run_shell": {
                        "command": f"bash {mock_agent_path}",
                        "pause": 0.5,
                    }
                },
                {
                    "wait_for_hook_event": {
                        "event": "PostInvocation",
                        "timeout": 10.0,
                        "pause": 0.5,
                    }
                },
                {
                    "assert_hook_event": {
                        "event": "PreToolUse",
                        "tool": "list_files",
                    }
                },
                {
                    "assert": {
                        "pattern": "Agent finished",
                        "timeout": 5.0,
                    }
                },
            ],
        }

        manifest = ScenarioManifest.from_dict(manifest_dict)
        runner = ScenarioRunner(manifest=manifest, verbose=True)
        report = runner.run()

        self.assertEqual(report.status, "pass")
        self.assertTrue(os.path.exists(self.output_mp4))
        self.assertGreater(report.frame_count, 0)
        self.assertGreater(report.file_size_bytes, 1000)

        # Verify hook events were captured in bridge
        pre_tool_evs = runner.hook_bridge.get_events(event_type="PreToolUse", tool_name="list_files")
        self.assertEqual(len(pre_tool_evs), 1)
        self.assertEqual(pre_tool_evs[0].decision, "allow")


if __name__ == "__main__":
    unittest.main()
