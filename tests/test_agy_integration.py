import unittest
import tempfile
import os
import shutil
import subprocess
from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner


class TestAgyIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_agy_test_")
        self.output_mp4 = os.path.join(self.temp_dir, "agy_session.mp4")
        self.output_cast = os.path.join(self.temp_dir, "agy_session.cast")

        # Initialize temporary Git repository for Antigravity workspace
        subprocess.run(["git", "init"], cwd=self.temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Paul Datta"], cwd=self.temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "pkdatta2000@gmail.com"], cwd=self.temp_dir, check=True, capture_output=True)
        with open(os.path.join(self.temp_dir, "README.md"), "w") as f:
            f.write("# Sample Antigravity Project\nThis is a test project for TermReel recording.\n")
        subprocess.run(["git", "add", "."], cwd=self.temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.temp_dir, check=True, capture_output=True)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_record_agy_cli_session(self):
        """Record a live session invoking the real agy CLI in the temp workspace."""
        manifest_dict = {
            "version": "1.0",
            "metadata": {
                "title": "Antigravity CLI Integration Test",
                "subtitle": "agy verification",
                "output": self.output_mp4,
                "cast_output": self.output_cast,
                "resolution": [1280, 720],
                "fps": 20,
                "theme": "catppuccin-mocha",
                "statusbar_left": "agy 1.1.22 | Real TTY | UTF-8",
                "statusbar_right": "TermReel HD",
            },
            "environment": {
                "cwd": self.temp_dir,
            },
            "triggers": [
                {
                    "on_match": "Do you trust the contents of this project|Yes, I trust|Trust project",
                    "action": "Enter",
                    "once": True,
                }
            ],
            "timeline": [
                {
                    "show_card": {
                        "tag": "Verification",
                        "title": "Testing agy CLI Harness",
                        "desc": "Spawning Antigravity CLI and capturing live terminal output",
                        "duration": 1.5,
                    }
                },
                {
                    "launch": {
                        "command": "bash",
                    }
                },
                {
                    "type": {
                        "text": "agy --version",
                        "speed": 0.02,
                        "send_key": "Enter",
                        "pause": 1.0,
                    }
                },
                {
                    "type": {
                        "text": "agy --help | head -n 12",
                        "speed": 0.02,
                        "send_key": "Enter",
                        "pause": 1.5,
                    }
                },
                {
                    "show_card": {
                        "tag": "Done",
                        "title": "Test Completed Successfully",
                        "duration": 1.0,
                    }
                },
            ],
        }

        manifest = ScenarioManifest.from_dict(manifest_dict)
        runner = ScenarioRunner(manifest=manifest, verbose=True)
        report = runner.run()

        self.assertEqual(report.status, "pass")
        self.assertTrue(os.path.exists(self.output_mp4))
        self.assertGreater(os.path.getsize(self.output_mp4), 5000)
        self.assertTrue(os.path.exists(self.output_cast))

        # Inspect generated video with ffprobe
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of", "default=noprint_wrappers=1", self.output_mp4],
            capture_output=True,
            text=True,
        )
        self.assertIn("codec_name=h264", res.stdout)
        self.assertIn("width=1280", res.stdout)
        self.assertIn("height=720", res.stdout)

    def test_record_agy_pure_interactive_mode(self):
        """Record real agy in fully interactive mode (no -p, no --dangerously-skip-permissions)."""
        interactive_mp4 = os.path.join(self.temp_dir, "agy_interactive.mp4")
        interactive_cast = os.path.join(self.temp_dir, "agy_interactive.cast")

        # Create a sample python file in the workspace
        with open(os.path.join(self.temp_dir, "calculator.py"), "w") as f:
            f.write("def multiply(x, y):\n    return x * y\n")
        subprocess.run(["git", "add", "."], cwd=self.temp_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add calculator"], cwd=self.temp_dir, check=True, capture_output=True)

        manifest_dict = {
            "version": "1.0",
            "metadata": {
                "title": "Antigravity Interactive TUI",
                "subtitle": "Live Session Capture",
                "output": interactive_mp4,
                "cast_output": interactive_cast,
                "resolution": [1280, 720],
                "fps": 30,
                "theme": "catppuccin-mocha",
                "statusbar_left": "agy 1.1.22 | Full Interactive Mode | UTF-8",
                "statusbar_right": "TermReel HD",
            },
            "environment": {
                "cwd": self.temp_dir,
                "auto_trust": True,
            },
            "timeline": [
                {
                    "show_card": {
                        "tag": "Module 1",
                        "title": "Pure Interactive TUI",
                        "desc": "Testing interactive agy session with automated workspace trust",
                        "duration": 1.5,
                    }
                },
                {
                    "launch": {
                        "command": "agy",
                        "wait_for_idle": True,
                        "timeout": 20.0,
                    }
                },
                {
                    "type": {
                        "text": "What does calculator.py do?",
                        "speed": 0.03,
                        "send_key": "Enter",
                    }
                },
                {
                    "wait_for_idle": {
                        "timeout": 35.0,
                        "reading_pause": 2.0,
                    }
                },
                {
                    "type": {
                        "text": "/exit",
                        "speed": 0.03,
                        "send_key": "Enter",
                        "pause": 1.0,
                    }
                },
                {
                    "show_card": {
                        "tag": "Complete",
                        "title": "Interactive Session Verification Passed",
                        "duration": 1.0,
                    }
                },
            ],
        }

        manifest = ScenarioManifest.from_dict(manifest_dict)
        runner = ScenarioRunner(manifest=manifest, verbose=True)
        report = runner.run()

        self.assertEqual(report.status, "pass")
        self.assertTrue(os.path.exists(interactive_mp4))
        self.assertGreater(os.path.getsize(interactive_mp4), 10000)
        self.assertTrue(os.path.exists(interactive_cast))

        # Inspect generated video with ffprobe
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of", "default=noprint_wrappers=1", interactive_mp4],
            capture_output=True,
            text=True,
        )
        self.assertIn("codec_name=h264", res.stdout)
        self.assertIn("width=1280", res.stdout)
        self.assertIn("height=720", res.stdout)


if __name__ == "__main__":
    unittest.main()
