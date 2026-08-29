import unittest
import tempfile
import os
import subprocess
from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner


class TestScenarioRunner(unittest.TestCase):

    def test_run_simple_scenario_to_mp4(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            out_mp4 = f.name
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f2:
            out_poster = f2.name

        manifest_dict = {
            "version": "1.0",
            "metadata": {
                "title": "Unit Test Run",
                "subtitle": "Echo Test",
                "output": out_mp4,
                "poster_output": out_poster,
                "resolution": [640, 360],
                "fps": 15,
                "theme": "catppuccin-mocha",
            },
            "environment": {
                "create_temp_workspace": True,
            },
            "timeline": [
                {"show_card": {"tag": "Test", "title": "Starting Echo", "duration": 0.5}},
                {"launch": {"command": "bash"}},
                {"type": {"text": "echo 'TermReel is working'", "speed": 0.01, "send_key": "Enter"}},
                {"pause": {"seconds": 0.5}},
            ],
        }

        manifest = ScenarioManifest.from_dict(manifest_dict)
        runner = ScenarioRunner(manifest=manifest, verbose=False)
        report = runner.run()

        self.assertEqual(report.status, "pass")
        self.assertTrue(os.path.exists(out_mp4))
        self.assertGreater(os.path.getsize(out_mp4), 1000)

        # Check with ffprobe
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name", "-of", "default=noprint_wrappers=1", out_mp4],
            capture_output=True,
            text=True,
        )
        self.assertIn("codec_name=h264", res.stdout)
        self.assertIn("width=640", res.stdout)
        self.assertIn("height=360", res.stdout)

        if os.path.exists(out_mp4):
            os.unlink(out_mp4)
        if os.path.exists(out_poster):
            os.unlink(out_poster)


if __name__ == "__main__":
    unittest.main()
