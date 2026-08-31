"""
Robustness and failure condition test suite for TermReel BatchOrchestrator:
1. Empty scenario list handling and empty glob matching.
2. Missing scenario files handling and failure reporting.
3. Mixed batch execution (successful scenarios alongside failing scenarios).
4. Report generation (JSON and Markdown structured reports recording failures with error details).
5. Theme override propagation across all batch scenarios (--theme nord).
6. FPS override propagation across all batch scenarios (--fps 24).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from termreel.batch import BatchOrchestrator, BatchReport, BatchScenarioResult


class TestBatchRobustness(unittest.TestCase):
    """Test suite for BatchOrchestrator edge cases, error resilience, and report generation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_batch_robust_")
        self.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(self.output_dir, exist_ok=True)

        # Helper scenario templates
        self.fast_success_yaml = """version: "1.0"
metadata:
  title: "Fast Success Scenario"
  fps: 15
  resolution: [320, 180]
  theme: "dracula"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Success'"
      send_key: "Enter"
  - pause:
      seconds: 0.2
"""

        self.failing_schema_yaml = """version: "1.0"
metadata:
  title: "Failing Scenario"
  fps: 15
  resolution: [320, 180]
  theme: "nonexistent_broken_theme_99"
timeline:
  - launch:
      command: "bash"
"""

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_scenario_file(self, filename: str, content: str) -> str:
        path = os.path.join(self.temp_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # ───────────────────────────────────────────────────────────
    # 1. Empty Scenario List Handling
    # ───────────────────────────────────────────────────────────

    def test_empty_scenario_list_handling(self):
        """Test BatchOrchestrator returns clean zero-count report when scenarios list is empty."""
        report_path = os.path.join(self.temp_dir, "empty_report.json")
        orchestrator = BatchOrchestrator(
            scenarios=[],
            concurrency=2,
            output_dir=self.output_dir,
            report_file=report_path,
            quiet=True,
        )

        resolved = orchestrator.resolve_scenario_files()
        self.assertEqual(resolved, [])

        report = orchestrator.run()
        self.assertIsInstance(report, BatchReport)
        self.assertEqual(report.total, 0)
        self.assertEqual(report.passed, 0)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.scenarios, [])
        self.assertTrue(os.path.exists(report_path))

        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["scenarios"], [])

    def test_non_matching_glob_handling(self):
        """Test BatchOrchestrator handles glob patterns that match no files on disk."""
        nonexistent_glob = os.path.join(self.temp_dir, "no_such_dir_xyz", "*.yaml")
        orchestrator = BatchOrchestrator(
            scenarios=[nonexistent_glob],
            concurrency=2,
            output_dir=self.output_dir,
            quiet=True,
        )
        report = orchestrator.run()
        self.assertEqual(report.total, 1)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.passed, 0)
        self.assertIn("not found", report.scenarios[0].error.lower())

    # ───────────────────────────────────────────────────────────
    # 2. Missing Scenario Files
    # ───────────────────────────────────────────────────────────

    def test_missing_scenario_files_handling(self):
        """Test BatchOrchestrator records failed results with descriptive errors for missing files."""
        missing_1 = os.path.join(self.temp_dir, "missing_scenario_1.yaml")
        missing_2 = os.path.join(self.temp_dir, "missing_scenario_2.yaml")

        orchestrator = BatchOrchestrator(
            scenarios=[missing_1, missing_2],
            concurrency=2,
            output_dir=self.output_dir,
            quiet=True,
        )

        report = orchestrator.run()
        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 0)
        self.assertEqual(report.failed, 2)
        self.assertEqual(len(report.scenarios), 2)

        for sc_res in report.scenarios:
            self.assertEqual(sc_res.status, "fail")
            self.assertEqual(sc_res.duration, 0.0)
            self.assertEqual(sc_res.frames, 0)
            self.assertIsNotNone(sc_res.error)
            self.assertIn("Scenario file not found", sc_res.error)

    # ───────────────────────────────────────────────────────────
    # 3. Mixed Batch (1 Succeeds, 1 Fails)
    # ───────────────────────────────────────────────────────────

    def test_mixed_batch_success_and_failure(self):
        """Test BatchOrchestrator handles mixed batch containing 1 passing and 1 failing scenario."""
        sc_good = self._create_scenario_file("sc_good.yaml", self.fast_success_yaml)
        sc_bad = self._create_scenario_file("sc_bad.yaml", self.failing_schema_yaml)

        orchestrator = BatchOrchestrator(
            scenarios=[sc_good, sc_bad],
            concurrency=2,
            output_dir=self.output_dir,
            generate_posters=True,
            poster_time=0.1,
            quiet=True,
        )

        report = orchestrator.run()
        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 1)

        pass_results = [s for s in report.scenarios if s.status == "pass"]
        fail_results = [s for s in report.scenarios if s.status == "fail"]

        self.assertEqual(len(pass_results), 1)
        self.assertEqual(len(fail_results), 1)

        good_res = pass_results[0]
        self.assertEqual(good_res.file, sc_good)
        self.assertIsNone(good_res.error)
        self.assertTrue(os.path.exists(good_res.output))
        self.assertGreater(good_res.frames, 0)

        bad_res = fail_results[0]
        self.assertEqual(bad_res.file, sc_bad)
        self.assertIsNotNone(bad_res.error)
        self.assertIn("Unrecognized theme", bad_res.error)

    # ───────────────────────────────────────────────────────────
    # 4. Report Generation (JSON and Markdown)
    # ───────────────────────────────────────────────────────────

    def test_report_generation_json_and_markdown_with_failures(self):
        """Test both JSON and Markdown reports record execution failure details correctly."""
        sc_good = self._create_scenario_file("sc_report_good.yaml", self.fast_success_yaml)
        sc_bad = self._create_scenario_file("sc_report_bad.yaml", self.failing_schema_yaml)

        report_json = os.path.join(self.temp_dir, "summary_report.json")
        report_md = os.path.join(self.temp_dir, "summary_report.md")

        orchestrator = BatchOrchestrator(
            scenarios=[sc_good, sc_bad],
            concurrency=2,
            output_dir=self.output_dir,
            report_file=report_json,
            quiet=True,
        )

        report = orchestrator.run()
        self.assertTrue(os.path.exists(report_json))

        # 1. Verify JSON report contents
        with open(report_json, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        self.assertEqual(json_data["total"], 2)
        self.assertEqual(json_data["passed"], 1)
        self.assertEqual(json_data["failed"], 1)
        self.assertGreater(json_data["elapsed_seconds"], 0.0)

        sc_entries = json_data["scenarios"]
        self.assertEqual(len(sc_entries), 2)
        failed_entry = [e for e in sc_entries if e["status"] == "fail"][0]
        self.assertIn("Unrecognized theme", failed_entry["error"])

        # 2. Verify Markdown report generation
        report.save(report_md)
        self.assertTrue(os.path.exists(report_md))

        with open(report_md, "r", encoding="utf-8") as f:
            md_content = f.read()

        self.assertIn("# 🎬 TermReel Batch Execution Report", md_content)
        self.assertIn("- **Total Scenarios**: 2", md_content)
        self.assertIn("- **Passed**: 1", md_content)
        self.assertIn("- **Failed**: 1", md_content)
        self.assertIn("✅ PASS", md_content)
        self.assertIn("❌ FAIL", md_content)
        self.assertIn("sc_report_good.yaml", md_content)
        self.assertIn("sc_report_bad.yaml", md_content)
        self.assertIn("Unrecognized theme", md_content)

    # ───────────────────────────────────────────────────────────
    # 5. Theme Override Across All Batch Scenarios
    # ───────────────────────────────────────────────────────────

    def test_theme_override_across_batch_scenarios(self):
        """Test BatchOrchestrator propagates --theme override across all scenarios."""
        sc1_yaml = """version: "1.0"
metadata:
  title: "Theme Override 1"
  fps: 15
  resolution: [320, 180]
  theme: "dracula"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Theme 1'"
      send_key: "Enter"
  - pause:
      seconds: 0.2
"""

        sc2_yaml = """version: "1.0"
metadata:
  title: "Theme Override 2"
  fps: 15
  resolution: [320, 180]
  theme: "tokyo-night"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Theme 2'"
      send_key: "Enter"
  - pause:
      seconds: 0.2
"""

        sc1_path = self._create_scenario_file("sc_theme_1.yaml", sc1_yaml)
        sc2_path = self._create_scenario_file("sc_theme_2.yaml", sc2_yaml)

        orchestrator = BatchOrchestrator(
            scenarios=[sc1_path, sc2_path],
            theme_override="nord",
            concurrency=2,
            output_dir=self.output_dir,
            quiet=True,
        )

        self.assertEqual(orchestrator.theme_override, "nord")
        report = orchestrator.run()

        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 0)

        for s in report.scenarios:
            self.assertEqual(s.status, "pass")
            self.assertTrue(os.path.exists(s.output))
            self.assertGreater(os.path.getsize(s.output), 0)

    # ───────────────────────────────────────────────────────────
    # 6. FPS Override Across All Batch Scenarios
    # ───────────────────────────────────────────────────────────

    def test_fps_override_across_batch_scenarios(self):
        """Test BatchOrchestrator propagates --fps override to synthesized videos."""
        sc1_yaml = """version: "1.0"
metadata:
  title: "FPS Override 1"
  fps: 15
  resolution: [320, 180]
  theme: "catppuccin-mocha"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'FPS 1'"
      send_key: "Enter"
  - pause:
      seconds: 0.2
"""

        sc2_yaml = """version: "1.0"
metadata:
  title: "FPS Override 2"
  fps: 30
  resolution: [320, 180]
  theme: "catppuccin-mocha"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'FPS 2'"
      send_key: "Enter"
  - pause:
      seconds: 0.2
"""

        sc1_path = self._create_scenario_file("sc_fps_1.yaml", sc1_yaml)
        sc2_path = self._create_scenario_file("sc_fps_2.yaml", sc2_yaml)

        orchestrator = BatchOrchestrator(
            scenarios=[sc1_path, sc2_path],
            fps_override=24,
            concurrency=2,
            output_dir=self.output_dir,
            quiet=True,
        )

        self.assertEqual(orchestrator.fps_override, 24)
        report = orchestrator.run()

        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 0)

        # Inspect generated videos using ffprobe to verify 24 fps stream encoding
        for s in report.scenarios:
            self.assertTrue(os.path.exists(s.output))
            probe_res = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    s.output,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe_res.returncode, 0)
            self.assertEqual(probe_res.stdout.strip(), "24/1")


if __name__ == "__main__":
    unittest.main()
