"""
Tests for TermReel Field Features:
1. Dynamic Terminal Video Speedup (speedup / timelapse)
2. Hermetic Vim Driver (edit_file / edit)
3. Semantic Output Assertion Gate (assert / assert_output / assert_screen)
4. Multi-Window / Split-Pane Tmux Layouts (split_pane, select_pane, close_pane)
5. Native Vertex AI / ADC Support in VideoAuditor
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

from termreel.scenario.schema import parse_manifest_dict, TimelineStep
from termreel.scenario.runner import ScenarioRunner
from termreel.audit import VideoAuditor
from termreel.cli import build_parser
from termreel.supervisor.tmux_session import TmuxSupervisor


class TestSpeedupFeature(unittest.TestCase):
    """Test dynamic video speedup, frame decimation, and status pill indicator."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_test_speedup_")
        self.output_mp4 = os.path.join(self.temp_dir, "test.mp4")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_minimal_runner(self):
        manifest_data = {
            "version": 1,
            "metadata": {
                "title": "Speedup Test",
                "output": self.output_mp4,
                "fps": 30,
            },
            "timeline": [
                {"pause": 0.1},
            ],
        }
        manifest = parse_manifest_dict(manifest_data)
        return ScenarioRunner(manifest=manifest, verbose=False)

    def test_set_speedup_numeric(self):
        runner = self._create_minimal_runner()
        self.assertEqual(runner._speedup_factor, 1.0)
        self.assertIsNone(runner._speedup_indicator)

        runner.set_speedup(8.0)
        self.assertEqual(runner._speedup_factor, 8.0)
        self.assertEqual(runner._speedup_indicator, "⏩ 8x")

        # Decimal factor
        runner.set_speedup(2.5)
        self.assertEqual(runner._speedup_factor, 2.5)
        self.assertEqual(runner._speedup_indicator, "⏩ 2.5x")

        # Reset
        runner.set_speedup(1.0)
        self.assertEqual(runner._speedup_factor, 1.0)
        self.assertIsNone(runner._speedup_indicator)

    def test_set_speedup_dict(self):
        runner = self._create_minimal_runner()
        runner.set_speedup({"factor": 4.0, "indicator": "🚀 FAST"})
        self.assertEqual(runner._speedup_factor, 4.0)
        self.assertEqual(runner._speedup_indicator, "🚀 FAST")

        runner.set_speedup(None)
        self.assertEqual(runner._speedup_factor, 1.0)
        self.assertIsNone(runner._speedup_indicator)

    def test_frame_decimation_math(self):
        """Simulate capture loop decimation ticks and verify frame rate reduction."""
        runner = self._create_minimal_runner()
        runner.set_speedup(4.0)

        rendered_frames = 0
        total_ticks = 16  # 16 ticks at 4x speedup should yield 4 rendered frames

        for _ in range(total_ticks):
            speedup = runner._speedup_factor
            if speedup <= 1.0:
                rendered_frames += 1
            else:
                runner._frame_accumulator += 1.0
                if runner._frame_accumulator >= speedup:
                    runner._frame_accumulator -= speedup
                    rendered_frames += 1

        self.assertEqual(rendered_frames, 4)

    def test_inline_step_speedup_restoration(self):
        """Verify inline speedup on a step applies during execution and restores afterwards."""
        runner = self._create_minimal_runner()
        runner.supervisor = MagicMock()

        step = TimelineStep(
            step_type="pause",
            params={"seconds": 0.01, "speedup": {"factor": 6.0, "indicator": "⏩ 6x"}},
        )

        # Before step
        self.assertEqual(runner._speedup_factor, 1.0)
        self.assertIsNone(runner._speedup_indicator)

        runner._execute_step(step, 0)

        # After step completes, speedup must be restored to 1.0
        self.assertEqual(runner._speedup_factor, 1.0)
        self.assertIsNone(runner._speedup_indicator)


class TestHermeticVimDriver(unittest.TestCase):
    """Test edit_file primitive and Vim buffer handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_test_edit_")
        self.output_mp4 = os.path.join(self.temp_dir, "test.mp4")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_runner(self):
        manifest_data = {
            "version": 1,
            "metadata": {
                "title": "Edit Test",
                "output": self.output_mp4,
            },
            "timeline": [
                {"pause": 0.1},
            ],
        }
        manifest = parse_manifest_dict(manifest_data)
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        runner._work_dir = self.temp_dir
        return runner

    def test_edit_new_or_empty_file_avoids_e16(self):
        """New or empty file MUST NOT execute :%d, which causes Vim E16: Invalid range."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        runner.supervisor = mock_sup

        target_file = "new_code.py"
        # File does not exist yet
        params = {
            "path": target_file,
            "action": "replace",
            "content": "print('hello world')\n",
            "pause_after": 0.0,
        }

        runner._execute_edit_file(params)

        # Gather all inputs sent to supervisor
        sent_inputs = [c.args[0] for c in mock_sup.send_input.call_args_list]

        # Verify vim command was launched with clean options
        self.assertTrue(any("vim -u NONE -i NONE -n" in inp and target_file in inp for inp in sent_inputs))

        # Crucial check: :%d must NOT be sent on empty/new file
        self.assertFalse(any(":%d" in inp for inp in sent_inputs), "Expected :%d NOT to be sent on empty file")

        # Verify insert mode and bracketed paste
        self.assertTrue(any(inp == "i" for inp in sent_inputs))
        self.assertTrue(any("\x1b[200~print('hello world')\n\x1b[201~" in inp for inp in sent_inputs))

        # Verify save and quit
        self.assertTrue(any(inp == "\x1b" for inp in sent_inputs))
        self.assertTrue(any(":wq\r" in inp for inp in sent_inputs))

    def test_edit_existing_file_replaces_cleanly(self):
        """Existing non-empty file MUST execute :%d to clear buffer before replacing."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        runner.supervisor = mock_sup

        target_file = "existing.py"
        full_path = os.path.join(self.temp_dir, target_file)
        with open(full_path, "w") as f:
            f.write("# Old boilerplate code\n")

        params = {
            "path": target_file,
            "action": "replace",
            "content": "def new_function():\n    pass\n",
            "pause_after": 0.0,
        }

        runner._execute_edit_file(params)

        sent_inputs = [c.args[0] for c in mock_sup.send_input.call_args_list]

        # Since file is non-empty, :%d\r must be sent
        self.assertTrue(any(":%d\r" in inp for inp in sent_inputs), "Expected :%d\\r to be sent on non-empty file")
        # And bracketed paste contains the new content
        self.assertTrue(any("\x1b[200~def new_function():\n    pass\n\x1b[201~" in inp for inp in sent_inputs))

    def test_edit_append_action(self):
        """Append action must navigate to bottom with G and o."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        runner.supervisor = mock_sup

        params = {
            "path": "test.txt",
            "action": "append",
            "content": "appended line\n",
            "pause_after": 0.0,
        }

        runner._execute_edit_file(params)

        sent_inputs = [c.args[0] for c in mock_sup.send_input.call_args_list]
        self.assertTrue(any(inp == "G" for inp in sent_inputs))
        self.assertTrue(any(inp == "o" for inp in sent_inputs))


class TestAssertionGateFeature(unittest.TestCase):
    """Test standalone assert and run_shell assert_output gates."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_test_assert_")
        self.output_mp4 = os.path.join(self.temp_dir, "test.mp4")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_runner(self):
        manifest_data = {
            "version": 1,
            "metadata": {
                "title": "Assert Test",
                "output": self.output_mp4,
            },
            "timeline": [
                {"pause": 0.1},
            ],
        }
        manifest = parse_manifest_dict(manifest_data)
        return ScenarioRunner(manifest=manifest, verbose=False)

    def test_assert_contains_pass_and_fail(self):
        runner = self._create_runner()
        mock_sup = MagicMock()
        mock_sup.capture_plain.return_value = "Build succeeded! 42 tests passed.\n"
        runner.supervisor = mock_sup

        # Passing assertion
        runner._execute_assert({"contains": "Build succeeded!", "timeout": 0.5})

        # Failing assertion
        with self.assertRaises(AssertionError) as ctx:
            runner._execute_assert({"contains": "FATAL ERROR", "timeout": 0.2})
        self.assertIn("Assertion failed", str(ctx.exception))

    def test_assert_not_contains(self):
        runner = self._create_runner()
        mock_sup = MagicMock()
        mock_sup.capture_plain.return_value = "Everything completed normally.\n"
        runner.supervisor = mock_sup

        # Passing
        runner._execute_assert({"not_contains": "Traceback", "timeout": 0.5})

        # Failing when forbidden string is present
        mock_sup.capture_plain.return_value = "Traceback (most recent call last):\nIndexError: list index out of range"
        with self.assertRaises(AssertionError):
            runner._execute_assert({"not_contains": "Traceback", "timeout": 0.2})

    def test_assert_scope_all_scrollback(self):
        """Verify scope: 'all' requests full scrollback history."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        mock_sup.capture_plain.side_effect = lambda include_scrollback=False: (
            "[SCROLLBACK HISTORICAL OUTPUT]\nVisible Line 1\n"
            if include_scrollback
            else "Visible Line 1\n"
        )
        runner.supervisor = mock_sup

        # Asserting on historical output with scope: "all" succeeds
        runner._execute_assert({
            "contains": "[SCROLLBACK HISTORICAL OUTPUT]",
            "scope": "all",
            "timeout": 0.5,
        })

        # But with scope: "visible", it fails
        with self.assertRaises(AssertionError):
            runner._execute_assert({
                "contains": "[SCROLLBACK HISTORICAL OUTPUT]",
                "scope": "visible",
                "timeout": 0.2,
            })

    def test_assert_on_fail_warn(self):
        """on_fail: 'warn' must log without raising an exception."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        mock_sup.capture_plain.return_value = "Normal output"
        runner.supervisor = mock_sup

        # Does not raise
        runner._execute_assert({
            "contains": "MISSING_TEXT",
            "on_fail": "warn",
            "timeout": 0.2,
        })

    def test_run_shell_with_assert_output(self):
        """Test run_shell executing and checking assert_output."""
        runner = self._create_runner()
        mock_sup = MagicMock()
        mock_sup.capture_plain.return_value = "All 10 tests passed successfully!"
        runner.supervisor = mock_sup

        # run_shell step with matching assert_output
        step = TimelineStep(
            step_type="run_shell",
            params={
                "command": "pytest",
                "pause": 0.0,
                "assert_output": {
                    "contains": "10 tests passed",
                    "not_contains": "FAIL",
                },
            },
        )
        runner._execute_step(step, 0)

        # run_shell step with failing assert_output
        step_fail = TimelineStep(
            step_type="run_shell",
            params={
                "command": "pytest",
                "pause": 0.0,
                "assert_output": {
                    "contains": "AssertionError",
                    "timeout": 0.2,
                },
            },
        )
        with self.assertRaises(AssertionError):
            runner._execute_step(step_fail, 1)


# Split/select/close are tested against a real private tmux server in
# tests/test_tmux_backend_real.py. The mock-based tests that lived here only
# asserted the argv handed to a patched subprocess.run, including a
# hard-coded ``session:0.N`` target that was wrong under pane-base-index 1.


class TestVertexAIAuditSupport(unittest.TestCase):
    """Test Native Vertex AI / ADC integration in VideoAuditor."""

    def setUp(self):
        self.test_video = os.path.abspath("output/agy_demo.mp4")
        if not os.path.exists(self.test_video):
            self.skipTest(f"Test video not found: {self.test_video}")

    def test_auditor_vertex_initialization(self):
        auditor = VideoAuditor(
            video_path=self.test_video,
            vertexai=True,
            project="acme-corp-demo",
            location="us-central1",
        )
        self.assertTrue(auditor.vertexai)
        self.assertEqual(auditor.project, "acme-corp-demo")
        self.assertEqual(auditor.location, "us-central1")

    def test_extract_jpeg_frame(self):
        """Extract a frame from test video and verify it has valid JPEG magic bytes."""
        auditor = VideoAuditor(video_path=self.test_video)
        jpeg_bytes = auditor._extract_jpeg_frame(1.0)
        self.assertIsNotNone(jpeg_bytes)
        self.assertGreater(len(jpeg_bytes), 100)
        # JPEG SOI marker is 0xFF 0xD8
        self.assertTrue(jpeg_bytes.startswith(b"\xff\xd8"), "Expected valid JPEG SOI header")

    def test_extract_keyframe_parts(self):
        """Verify keyframe extraction generates Part objects with image/jpeg mime type."""
        auditor = VideoAuditor(video_path=self.test_video)

        class MockPart:
            @classmethod
            def from_bytes(cls, data, mime_type):
                return {"data_len": len(data), "mime_type": mime_type}

        class MockTypes:
            Part = MockPart

        parts = auditor._extract_keyframe_parts(MockTypes, count=3)
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertEqual(p["mime_type"], "image/jpeg")
            self.assertGreater(p["data_len"], 100)

    def test_cli_audit_vertex_flags(self):
        """Verify CLI parser parses --vertexai, --project, --location correctly."""
        parser = build_parser()
        args = parser.parse_args([
            "audit",
            self.test_video,
            "--vertexai",
            "--project", "gcp-elevate-2026",
            "--location", "us-east4",
        ])
        self.assertTrue(args.vertexai)
        self.assertEqual(args.project, "gcp-elevate-2026")
        self.assertEqual(args.location, "us-east4")


if __name__ == "__main__":
    unittest.main()
