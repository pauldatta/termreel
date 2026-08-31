"""
Tests for TermReel native Multimodal Audit CLI subcommand and VideoAuditor.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from termreel.audit import VideoAuditor, AuditReport, CriterionScore
from termreel.cli import build_parser, main


class TestAuditCLI(unittest.TestCase):
    """Test suite for VideoAuditor inspection, scoring heuristics, and CLI integration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_test_audit_")
        self.test_video = os.path.abspath("output/agy_demo.mp4")
        if not os.path.exists(self.test_video):
            self.skipTest(f"Test video not found: {self.test_video}")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_auditor_local_heuristic_evaluation(self):
        """Test VideoAuditor inspecting real video with local heuristic scorecard."""
        auditor = VideoAuditor(
            video_path=self.test_video,
            threshold=80,
            model_name="gemini-3.1-pro-preview",
        )
        report = auditor.audit()

        self.assertIsInstance(report, AuditReport)
        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.overall_score, 80)
        self.assertEqual(report.threshold, 80)
        self.assertEqual(report.evaluation_mode, "local_heuristic")

        # Verify all 4 required criteria are present and scored
        expected_criteria = [
            "Visual Stability",
            "TUI Formatting",
            "Execution Completion",
            "Error-Free Output",
        ]
        for crit_name in expected_criteria:
            self.assertIn(crit_name, report.criteria)
            crit = report.criteria[crit_name]
            self.assertIsInstance(crit, CriterionScore)
            self.assertGreaterEqual(crit.score, 15)
            self.assertEqual(crit.max_score, 25)
            self.assertEqual(crit.status, "pass")

        # Verify checklist items
        self.assertIn("Visual Clarity", report.checklist)
        self.assertIn("Command Execution", report.checklist)
        self.assertIn("No Unhandled Exceptions", report.checklist)
        self.assertIn("Clean Prompt Termination", report.checklist)
        self.assertTrue(report.checklist["Visual Clarity"])
        self.assertTrue(report.checklist["Command Execution"])
        self.assertTrue(report.checklist["No Unhandled Exceptions"])
        self.assertTrue(report.checklist["Clean Prompt Termination"])

        # Verify metadata extraction
        self.assertEqual(report.metadata["width"], 1280)
        self.assertEqual(report.metadata["height"], 720)
        self.assertAlmostEqual(report.metadata["fps"], 30.0, places=0)
        self.assertGreater(report.metadata["duration"], 10.0)

        # Verify findings and timestamped notes
        self.assertGreater(len(report.findings), 0)
        self.assertGreater(len(report.timestamped_notes), 0)

    def test_threshold_pass_fail(self):
        """Test pass/fail status thresholding logic."""
        # Lenient threshold: should pass
        lenient_auditor = VideoAuditor(self.test_video, threshold=50)
        report_pass = lenient_auditor.audit()
        self.assertTrue(report_pass.passed)

        # Unreachable threshold: should fail
        strict_auditor = VideoAuditor(self.test_video, threshold=101)
        report_fail = strict_auditor.audit()
        self.assertFalse(report_fail.passed)

    def test_report_save_and_formats(self):
        """Test exporting audit reports to JSON and Markdown files."""
        auditor = VideoAuditor(self.test_video, threshold=80)
        report = auditor.audit()

        # Markdown export
        md_path = os.path.join(self.temp_dir, "scorecard.md")
        report.save(md_path)
        self.assertTrue(os.path.exists(md_path))
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        self.assertIn("# 🎬 TermReel Multimodal Video Audit Scorecard", md_content)
        self.assertIn("Quality Checklist", md_content)
        self.assertIn("Criteria Breakdown", md_content)
        self.assertIn("Visual Stability", md_content)

        # JSON export
        json_path = os.path.join(self.temp_dir, "scorecard.json")
        report.save(json_path)
        self.assertTrue(os.path.exists(json_path))
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["overall_score"], report.overall_score)
        self.assertTrue(data["passed"])
        self.assertIn("Visual Clarity", data["checklist"])

    def test_audit_nonexistent_video(self):
        """Test auditing a missing file returns zero score and failed status."""
        auditor = VideoAuditor(os.path.join(self.temp_dir, "nonexistent.mp4"), threshold=80)
        report = auditor.audit()
        self.assertFalse(report.passed)
        self.assertEqual(report.overall_score, 0)
        self.assertEqual(report.evaluation_mode, "error")
        self.assertFalse(report.checklist["Visual Clarity"])
        self.assertIn("does not exist", report.findings[0])

    def test_cli_argument_parsing(self):
        """Test argument parsing for 'termreel audit' CLI subcommand."""
        parser = build_parser()
        args = parser.parse_args([
            "audit",
            "output/demo.mp4",
            "--spec", "scenarios/demo.yaml",
            "--model", "gemini-3.1-pro-preview",
            "--threshold", "85",
            "--report", "audit_report.md",
            "--json",
        ])

        self.assertEqual(args.subcommand, "audit")
        self.assertEqual(args.video, "output/demo.mp4")
        self.assertEqual(args.spec, "scenarios/demo.yaml")
        self.assertEqual(args.model, "gemini-3.1-pro-preview")
        self.assertEqual(args.threshold, 85)
        self.assertEqual(args.report, "audit_report.md")
        self.assertTrue(args.json)

    def test_cli_main_audit_execution(self):
        """Test invoking 'termreel audit' via CLI main function."""
        report_file = os.path.join(self.temp_dir, "cli_audit.json")
        exit_code = main([
            "audit",
            self.test_video,
            "--threshold", "80",
            "--report", report_file,
            "--json",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(report_file))
        with open(report_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["passed"])
        self.assertGreaterEqual(data["overall_score"], 80)

    def test_cli_main_missing_video(self):
        """Test invoking 'termreel audit' on missing video returns exit code 1."""
        missing = os.path.join(self.temp_dir, "missing.mp4")
        exit_code = main(["audit", missing])
        self.assertEqual(exit_code, 1)

    def test_mock_gemini_multimodal_evaluation(self):
        """Test that Gemini response dictionary is correctly parsed into AuditReport."""
        mock_response = {
            "overall_score": 92,
            "checklist": {
                "Visual Clarity": True,
                "Command Execution": True,
                "No Unhandled Exceptions": True,
                "Clean Prompt Termination": True,
            },
            "criteria": {
                "Visual Stability": {"score": 24, "notes": "Solid framing"},
                "TUI Formatting": {"score": 23, "notes": "Clean borders"},
                "Execution Completion": {"score": 23, "notes": "All steps ran"},
                "Error-Free Output": {"score": 22, "notes": "No errors"},
            },
            "findings": ["Gemini multimodal model verified scenario steps."],
            "timestamped_notes": [{"timestamp": 1.0, "note": "Shell prompt rendered"}],
        }

        auditor = VideoAuditor(self.test_video, model_name="gemini-3.1-pro-preview", threshold=80)
        with patch.object(auditor, "_evaluate_with_gemini") as mock_gem:
            mock_gem.return_value = auditor._build_report_from_dict(
                mock_response,
                mode="multimodal (gemini-3.1-pro-preview)",
                metadata={"width": 1280, "height": 720, "fps": 30.0, "duration": 20.0, "codec": "h264"},
            )
            with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-key"}):
                report = auditor.audit()

        self.assertTrue(report.passed)
        self.assertEqual(report.overall_score, 92)
        self.assertEqual(report.evaluation_mode, "multimodal (gemini-3.1-pro-preview)")
        self.assertEqual(report.criteria["Visual Stability"].score, 24)
        self.assertEqual(report.findings, ["Gemini multimodal model verified scenario steps."])

    def test_auditor_auto_chunking_execution(self):
        """Test VideoAuditor windowed chunking on video with chunk_duration < total duration."""
        # Video is ~21s, so chunk_duration=6.0 should produce ~4 chunks
        auditor = VideoAuditor(
            video_path=self.test_video,
            threshold=80,
            chunk_duration=6.0,
            auto_chunk=True,
        )
        report = auditor.audit()

        self.assertIsInstance(report, AuditReport)
        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.overall_score, 80)
        self.assertTrue(report.evaluation_mode.startswith("chunked"))
        self.assertIn("segments", report.metadata)
        self.assertGreaterEqual(report.metadata["chunk_count"], 3)
        self.assertEqual(report.metadata["chunk_duration_sec"], 6.0)

        # Verify segments metadata
        segments = report.metadata["segments"]
        self.assertEqual(len(segments), report.metadata["chunk_count"])
        for seg in segments:
            self.assertIn("segment_index", seg)
            self.assertIn("start_sec", seg)
            self.assertIn("end_sec", seg)
            self.assertIn("score", seg)
            self.assertGreater(seg["duration"], 0.0)

        # Verify markdown report includes segment breakdown
        md = report.to_markdown()
        self.assertIn("Windowed Segment Breakdown", md)
        self.assertIn("Chunk 1", md)
        self.assertIn("Chunk 2", md)

    def test_cli_chunk_arguments(self):
        """Test CLI argument parsing and execution with --chunk-duration and --no-chunk."""
        parser = build_parser()
        args = parser.parse_args(["audit", "demo.mp4", "--chunk-duration", "120.0"])
        self.assertEqual(args.chunk_duration, 120.0)
        self.assertFalse(args.no_chunk)

        args_no_chunk = parser.parse_args(["audit", "demo.mp4", "--no-chunk"])
        self.assertTrue(args_no_chunk.no_chunk)

        # Test CLI execution with chunking enabled
        report_file = os.path.join(self.temp_dir, "chunked_audit.md")
        code = main(["audit", self.test_video, "--chunk-duration", "7.0", "--report", report_file])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(report_file))
        with open(report_file, "r") as f:
            content = f.read()
        self.assertIn("Windowed Segment Breakdown", content)


if __name__ == "__main__":
    unittest.main()

