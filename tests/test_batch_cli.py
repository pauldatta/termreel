"""
Tests for TermReel native Batch CLI subcommand and BatchOrchestrator.
"""

import json
import os
import shutil
import tempfile
import unittest

from termreel.batch import BatchOrchestrator, BatchReport, BatchScenarioResult
from termreel.cli import build_parser, main


class TestBatchCLI(unittest.TestCase):
    """Test suite for BatchOrchestrator parallel execution and CLI integration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_test_batch_")
        self.output_dir = os.path.join(self.temp_dir, "dist")
        os.makedirs(self.output_dir, exist_ok=True)

        # Create 2 fast declarative scenario manifests
        self.sc1_path = os.path.join(self.temp_dir, "scenario_alpha.yaml")
        with open(self.sc1_path, "w", encoding="utf-8") as f:
            f.write("""
version: "1.0"
metadata:
  title: "Batch Test Alpha"
  subtitle: "Worker 1"
  resolution: [480, 270]
  fps: 15
  theme: "catppuccin-mocha"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Alpha Batch Worker OK'"
      send_key: "Enter"
  - pause:
      seconds: 0.3
""")

        self.sc2_path = os.path.join(self.temp_dir, "scenario_beta.yaml")
        with open(self.sc2_path, "w", encoding="utf-8") as f:
            f.write("""
version: "1.0"
metadata:
  title: "Batch Test Beta"
  subtitle: "Worker 2"
  resolution: [480, 270]
  fps: 15
  theme: "dracula"
environment:
  create_temp_workspace: true
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Beta Batch Worker OK'"
      send_key: "Enter"
  - pause:
      seconds: 0.3
""")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_batch_orchestrator_parallel_execution(self):
        """Test BatchOrchestrator concurrently running 2 scenarios in parallel."""
        report_json = os.path.join(self.temp_dir, "batch_report.json")

        orchestrator = BatchOrchestrator(
            scenarios=[self.sc1_path, self.sc2_path],
            concurrency=2,
            output_dir=self.output_dir,
            generate_posters=True,
            poster_time=0.2,
            report_file=report_json,
            quiet=True,
        )

        resolved = orchestrator.resolve_scenario_files()
        self.assertEqual(len(resolved), 2)

        report = orchestrator.run()

        self.assertIsInstance(report, BatchReport)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 2)
        self.assertEqual(report.failed, 0)
        self.assertGreater(report.elapsed_seconds, 0.0)
        self.assertEqual(len(report.scenarios), 2)

        # Check files generated on disk
        mp4_alpha = os.path.join(self.output_dir, "scenario_alpha.mp4")
        mp4_beta = os.path.join(self.output_dir, "scenario_beta.mp4")
        png_alpha = os.path.join(self.output_dir, "scenario_alpha.png")
        png_beta = os.path.join(self.output_dir, "scenario_beta.png")

        self.assertTrue(os.path.exists(mp4_alpha), f"Missing {mp4_alpha}")
        self.assertTrue(os.path.exists(mp4_beta), f"Missing {mp4_beta}")
        self.assertTrue(os.path.exists(png_alpha), f"Missing {png_alpha}")
        self.assertTrue(os.path.exists(png_beta), f"Missing {png_beta}")

        # Check generated report file
        self.assertTrue(os.path.exists(report_json))
        with open(report_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["passed"], 2)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(len(data["scenarios"]), 2)

    def test_cli_argument_parsing(self):
        """Test argument parsing for 'termreel batch' CLI subcommand."""
        parser = build_parser()
        args = parser.parse_args([
            "batch",
            "scenarios/*.yaml",
            "-c", "8",
            "-o", "/tmp/out",
            "--poster-time", "1.5",
            "--report", "summary.md",
            "--theme", "nord",
            "--fps", "60",
            "-q",
        ])

        self.assertEqual(args.subcommand, "batch")
        self.assertEqual(args.scenarios, ["scenarios/*.yaml"])
        self.assertEqual(args.concurrency, 8)
        self.assertEqual(args.output_dir, "/tmp/out")
        self.assertEqual(args.poster_time, 1.5)
        self.assertEqual(args.report, "summary.md")
        self.assertEqual(args.theme, "nord")
        self.assertEqual(args.fps, 60)
        self.assertTrue(args.quiet)
        self.assertTrue(args.generate_posters)

    def test_cli_main_batch_execution(self):
        """Test invoking 'termreel batch' through CLI main function."""
        report_md = os.path.join(self.temp_dir, "cli_report.md")
        exit_code = main([
            "batch",
            self.sc1_path,
            self.sc2_path,
            "-c", "2",
            "-o", self.output_dir,
            "--report", report_md,
            "-q",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(report_md))
        with open(report_md, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# 🎬 TermReel Batch Execution Report", content)
        self.assertIn("scenario_alpha.yaml", content)
        self.assertIn("scenario_beta.yaml", content)
        self.assertIn("✅ PASS", content)

    def test_batch_report_formatting(self):
        """Test BatchReport serialization to JSON and Markdown."""
        report = BatchReport(
            total=1,
            passed=1,
            failed=0,
            elapsed_seconds=3.45,
            scenarios=[
                BatchScenarioResult(
                    file="test.yaml",
                    status="pass",
                    duration=3.45,
                    frames=90,
                    output="/tmp/test.mp4",
                    poster="/tmp/test.png",
                    error=None,
                )
            ],
        )

        md = report.to_markdown()
        self.assertIn("Total Scenarios**: 1", md)
        self.assertIn("Passed**: 1", md)
        self.assertIn("`test.yaml`", md)

        js = report.to_json()
        data = json.loads(js)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["scenarios"][0]["frames"], 90)


if __name__ == "__main__":
    unittest.main()
