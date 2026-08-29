import unittest
from termreel.scenario.schema import ScenarioManifest


class TestScenarioSchema(unittest.TestCase):

    def test_parse_minimal_manifest(self):
        yaml_content = """
version: "1.0"
metadata:
  title: "Test Video"
  output: "out.mp4"
  resolution: [1280, 720]
  fps: 30
timeline:
  - show_card:
      tag: "Mod 1"
      title: "Title"
      duration: 1.0
  - launch:
      command: "echo test"
"""
        manifest = ScenarioManifest.from_yaml_str(yaml_content)
        self.assertEqual(manifest.metadata.title, "Test Video")
        self.assertEqual(manifest.metadata.resolution, (1280, 720))
        self.assertEqual(len(manifest.timeline), 2)
        self.assertEqual(manifest.timeline[0].step_type, "show_card")
        self.assertEqual(manifest.timeline[0].params["tag"], "Mod 1")


if __name__ == "__main__":
    unittest.main()
