"""
Comprehensive unit and integration tests for TermReel CLI subcommands:
1. termreel info: Environment diagnostics, version, capabilities, dependencies.
2. termreel themes: Visual themes and color palettes listing.
3. termreel validate: Valid, invalid manifests, and missing files.
4. termreel exec: Direct command recording to MP4 video.
5. termreel cast2video: Asciinema .cast rendering and missing file error handling.
6. termreel probe: CLI binary capability exploration and nonexistent tools.
7. termreel generate: YAML generation with custom theme, title, and stdout streaming.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import termreel
from termreel.cli import main, build_parser
from termreel.scenario.schema import ScenarioManifest
from termreel.utils.asciicast import AsciicastRecorder

# Threading lock to serialize sys.stdout / sys.stderr patching during concurrent test runs
_CLI_LOCK = threading.Lock()


class TestCLICommandsComplete(unittest.TestCase):
    """Test suite for all TermReel CLI subcommands."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_cli_test_")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ───────────────────────────────────────────────────────────
    # 1. termreel info
    # ───────────────────────────────────────────────────────────

    def test_cli_info_command(self):
        """Test 'termreel info' prints diagnostics, version, themes, and capabilities with exit code 0."""
        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["info"])

            output = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn(f"TermReel v{termreel.__version__}", output)
            self.assertIn("Environment Diagnostics:", output)
            self.assertIn("Python:", output)
            self.assertIn("PyCairo:", output)
            self.assertIn("Tmux:", output)
            self.assertIn("FFmpeg:", output)
            self.assertIn("FFprobe:", output)
            self.assertIn("Themes:", output)
            self.assertIn("available", output)

    # ───────────────────────────────────────────────────────────
    # 2. termreel themes
    # ───────────────────────────────────────────────────────────

    def test_cli_themes_command(self):
        """Test 'termreel themes' lists built-in themes including catppuccin-mocha, tokyo-night, nord."""
        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["themes"])

            output = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Available TermReel Visual Themes", output)
            self.assertIn("catppuccin-mocha", output)
            self.assertIn("tokyo-night", output)
            self.assertIn("nord", output)
            self.assertIn("dracula", output)
            self.assertIn("monokai", output)
            self.assertIn("one-dark", output)
            self.assertIn("github-dark", output)

    # ───────────────────────────────────────────────────────────
    # 3. termreel validate
    # ───────────────────────────────────────────────────────────

    def test_cli_validate_valid_manifest(self):
        """Test 'termreel validate' with a valid scenario manifest passes with exit code 0."""
        valid_yaml_path = os.path.join(self.temp_dir, "valid_scenario.yaml")
        with open(valid_yaml_path, "w", encoding="utf-8") as f:
            f.write("""version: "1.0"
metadata:
  title: "CLI Validation Test"
  theme: "tokyo-night"
  fps: 25
  resolution: [1280, 720]
timeline:
  - launch:
      command: "bash"
  - type:
      text: "echo 'Validation Pass'"
      send_key: "Enter"
  - pause:
      seconds: 0.5
""")

        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["validate", valid_yaml_path])

            output = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Valid scenario manifest", output)
            self.assertIn("CLI Validation Test", output)
            self.assertIn("tokyo-night", output)
            self.assertIn("1280x720 @ 25 fps", output)

    def test_cli_validate_invalid_manifest(self):
        """Test 'termreel validate' with an invalid manifest fails with code 1 and error message."""
        invalid_yaml_path = os.path.join(self.temp_dir, "invalid_scenario.yaml")
        with open(invalid_yaml_path, "w", encoding="utf-8") as f:
            f.write("""version: "1.0"
metadata:
  title: "Invalid Test"
  theme: "definitely_nonexistent_theme_999"
timeline:
  - nonexistent_step_action:
      param: "bad"
""")

        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["validate", invalid_yaml_path])

            stderr_output = stderr_buf.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("Scenario validation failed", stderr_output)

    def test_cli_validate_missing_file(self):
        """Test 'termreel validate' with a missing file fails with exit code 1."""
        missing_path = os.path.join(self.temp_dir, "nonexistent_scenario.yaml")

        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["validate", missing_path])

            stderr_output = stderr_buf.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("Scenario file not found", stderr_output)

    # ───────────────────────────────────────────────────────────
    # 4. termreel exec
    # ───────────────────────────────────────────────────────────

    def test_cli_exec_quick_command(self):
        """Test 'termreel exec' directly records a CLI command to MP4 video."""
        out_mp4 = os.path.join(self.temp_dir, "exec_output.mp4")

        exit_code = main([
            "exec",
            "echo 'TermReel CLI Exec'",
            "-o", out_mp4,
            "--fps", "15",
            "--timeout", "5",
            "--title", "Exec Test Session",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(out_mp4), f"Output video was not generated at {out_mp4}")
        file_size = os.path.getsize(out_mp4)
        self.assertGreater(file_size, 0, f"Generated video has 0 bytes: {out_mp4}")

    # ───────────────────────────────────────────────────────────
    # 5. termreel cast2video
    # ───────────────────────────────────────────────────────────

    def test_cli_cast2video_successful_conversion(self):
        """Test 'termreel cast2video' renders an existing .cast file to MP4 video."""
        cast_file = os.path.join(self.temp_dir, "sample.cast")
        rec = AsciicastRecorder(cast_file, width=80, height=24, title="Sample Cast")
        rec.start()
        rec.record_output("TermReel Cast2Video conversion test\r\n")
        rec.record_output("Step completed.\r\n")
        rec.close()

        out_mp4 = os.path.join(self.temp_dir, "rendered_cast.mp4")

        exit_code = main([
            "cast2video",
            cast_file,
            "-o", out_mp4,
            "--fps", "15",
            "--speed", "2.0",
            "--title", "Cast Replay Test",
        ])

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(out_mp4))
        self.assertGreater(os.path.getsize(out_mp4), 0)

    def test_cli_cast2video_missing_cast_file(self):
        """Test 'termreel cast2video' with a nonexistent cast file fails gracefully."""
        missing_cast = os.path.join(self.temp_dir, "missing_recording.cast")
        out_mp4 = os.path.join(self.temp_dir, "dummy_out.mp4")

        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["cast2video", missing_cast, "-o", out_mp4])

            stderr_output = stderr_buf.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("Asciicast file not found", stderr_output)

    # ───────────────────────────────────────────────────────────
    # 6. termreel probe
    # ───────────────────────────────────────────────────────────

    def test_cli_probe_standard_binaries(self):
        """Test 'termreel probe' exploring standard tools like git and ls."""
        for tool_name in ["git", "ls"]:
            if not shutil.which(tool_name):
                continue
            with _CLI_LOCK:
                stdout_buf = io.StringIO()
                stderr_buf = io.StringIO()

                with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                    exit_code = main(["probe", tool_name])

                output = stdout_buf.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertIn(f"Discovered CLI Specification for: {tool_name}", output)
                self.assertIn("Path:", output)
                self.assertIn("Version:", output)
                self.assertIn("Category:", output)

    def test_cli_probe_nonexistent_binary(self):
        """Test 'termreel probe' gracefully handles nonexistent binary."""
        nonexistent = "nonexistent_binary_xyz_probe_9999"
        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main(["probe", nonexistent])

            stderr_output = stderr_buf.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn(f"CLI binary '{nonexistent}' was not found on PATH", stderr_output)

    # ───────────────────────────────────────────────────────────
    # 7. termreel generate
    # ───────────────────────────────────────────────────────────

    def test_cli_generate_with_custom_theme_and_title(self):
        """Test 'termreel generate' scaffolds scenario YAML with custom theme and title."""
        target_yaml = os.path.join(self.temp_dir, "generated_git.yaml")

        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main([
                    "generate",
                    "git",
                    "-o", target_yaml,
                    "--theme", "nord",
                    "--title", "Custom Git Deep Dive",
                    "--fps", "24",
                ])

            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.exists(target_yaml))

            with open(target_yaml, "r", encoding="utf-8") as f:
                content = f.read()

            # Validate that the generated file contains custom theme, title, and fps
            self.assertIn("theme: nord", content)
            self.assertIn("title: Custom Git Deep Dive", content)
            self.assertIn("fps: 24", content)

            # Validate that the generated file passes schema validation
            manifest = ScenarioManifest.from_yaml_file(target_yaml)
            self.assertEqual(manifest.metadata.theme, "nord")
            self.assertEqual(manifest.metadata.title, "Custom Git Deep Dive")
            self.assertEqual(manifest.metadata.fps, 24)

    def test_cli_generate_print_to_stdout(self):
        """Test 'termreel generate -p' / '--print' streams generated YAML to stdout."""
        with _CLI_LOCK:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()

            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = main([
                    "generate",
                    "git",
                    "-p",
                    "--theme", "catppuccin-latte",
                    "--title", "Stdout Git Demo",
                ])

            output = stdout_buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("version:", output)
            self.assertIn("Stdout Git Demo", output)
            self.assertIn("catppuccin-latte", output)
            self.assertIn("timeline:", output)

            # Isolate the generated YAML manifest string starting at version:
            version_idx = output.find("version:")
            self.assertNotEqual(version_idx, -1, "Generated output did not contain 'version:'")
            yaml_manifest_str = output[version_idx:]

            # Verify output is valid YAML manifest
            manifest = ScenarioManifest.from_yaml_str(yaml_manifest_str)
            self.assertEqual(manifest.metadata.theme, "catppuccin-latte")
            self.assertEqual(manifest.metadata.title, "Stdout Git Demo")


if __name__ == "__main__":
    unittest.main()
