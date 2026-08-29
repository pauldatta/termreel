import os
import tempfile
import unittest
from termreel.generator.explorer import CLIExplorer, CLISpec, SubcommandInfo
from termreel.generator.scaffold import ScenarioGenerator
from termreel.scenario.schema import ScenarioManifest


class TestGenerator(unittest.TestCase):

    def test_cli_explorer_installed_git(self):
        explorer = CLIExplorer("git")
        self.assertTrue(explorer.is_installed())
        spec = explorer.probe()
        self.assertEqual(spec.name, "git")
        self.assertEqual(spec.category, "vcs")
        self.assertIn("git", spec.usage.lower())
        self.assertTrue(len(spec.subcommands) > 0)
        self.assertIn("git", spec.recommended_permissions)

    def test_cli_explorer_missing_binary(self):
        explorer = CLIExplorer("nonexistent_binary_xyz_123")
        self.assertFalse(explorer.is_installed())
        with self.assertRaises(FileNotFoundError):
            explorer.probe()

    def test_category_inference(self):
        self.assertEqual(CLIExplorer("git")._infer_category("git version control"), "vcs")
        self.assertEqual(CLIExplorer("agy")._infer_category("antigravity ai assistant"), "agent")
        self.assertEqual(CLIExplorer("gcloud")._infer_category("google cloud sdk"), "cloud")
        self.assertEqual(CLIExplorer("python3")._infer_category("interactive interpreter"), "repl")
        self.assertEqual(CLIExplorer("npm")._infer_category("package manager"), "package")

    def test_scenario_generator_produces_valid_yaml(self):
        spec = CLISpec(
            name="testcli",
            executable_path="/usr/bin/testcli",
            version="1.0.0",
            summary="A test CLI tool",
            usage="testcli [options]",
            category="general",
            inferred_prompts=[
                {"title": "Check Version", "command": "testcli --version"},
                {"title": "Run Help", "command": "testcli --help"},
            ],
            suggested_setup_commands=["echo 'setup' > setup.txt"],
            recommended_permissions=["testcli", "cat"],
        )

        yaml_text = ScenarioGenerator.generate(
            spec=spec,
            title="Custom Test CLI Demonstration",
            theme="tokyo-night",
            fps=30,
        )

        self.assertIn("Custom Test CLI Demonstration", yaml_text)
        self.assertIn("tokyo-night", yaml_text)
        self.assertIn("testcli --version", yaml_text)

        # Verify YAML parses into a valid ScenarioManifest
        manifest = ScenarioManifest.from_yaml_str(yaml_text)
        self.assertEqual(manifest.metadata.title, "Custom Test CLI Demonstration")
        self.assertEqual(manifest.metadata.theme, "tokyo-night")
        self.assertEqual(len(manifest.timeline), 5)  # card + launch + 2 commands + card

    def test_scenario_generator_for_agent_category(self):
        spec = CLISpec(
            name="agy",
            executable_path="/usr/local/bin/agy",
            version="1.1.22",
            summary="Antigravity AI Agent",
            usage="agy [options]",
            category="agent",
            inferred_prompts=[
                {"title": "Prompt 1", "prompt": "Inspect codebase"},
            ],
            suggested_setup_commands=["git init"],
            recommended_permissions=["python3", "git"],
        )

        yaml_text = ScenarioGenerator.generate(spec=spec)
        manifest = ScenarioManifest.from_yaml_str(yaml_text)
        self.assertEqual(manifest.metadata.theme, "catppuccin-mocha")
        self.assertTrue(len(manifest.triggers) >= 2)
        # Check that launch step has wait_for_idle enabled
        launch_step = [s for s in manifest.timeline if s.step_type == "launch"][0]
        self.assertTrue(launch_step.params.get("wait_for_idle"))


if __name__ == "__main__":
    unittest.main()
