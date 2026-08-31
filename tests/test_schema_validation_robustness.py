"""
Comprehensive negative testing and schema boundary validation for TermReel scenario manifests.
Validates robust handling and descriptive ScenarioValidationError exceptions for:
1. Malformed YAML (syntax errors, invalid indentation, non-mapping roots).
2. Missing mandatory fields (version, timeline).
3. Invalid timeline steps (unknown actions, string/int instead of list, non-dict steps, empty step dicts).
4. Invalid parameters (negative durations, non-positive FPS, dimensions below terminal minimums).
5. Unrecognized theme names.
6. Malformed trigger regexes (unclosed parentheses, invalid regex syntax).
7. Missing command in launch or run_shell / exec steps.
8. Informative error messages and context preservation.
"""

import unittest
from termreel.exceptions import ScenarioValidationError
from termreel.scenario.schema import (
    ScenarioManifest,
    TriggerConfig,
    TimelineStep,
    VALID_ACTIONS,
)


class TestSchemaValidationRobustness(unittest.TestCase):
    """Robustness and boundary condition tests for scenario manifest validation."""

    # ───────────────────────────────────────────────────────────
    # 1. Malformed YAML
    # ───────────────────────────────────────────────────────────

    def test_malformed_yaml_syntax_error_unclosed_bracket(self):
        """Test malformed YAML syntax with unclosed bracket raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  title: "Bad Syntax"
  resolution: [1280, 720
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Malformed YAML syntax", str(ctx.exception))

    def test_malformed_yaml_invalid_indentation(self):
        """Test malformed YAML with broken indentation raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  title: "Bad Indent"
 timeline:
  - launch:
    command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Malformed YAML syntax", str(ctx.exception))

    def test_malformed_yaml_non_dict_root_scalar(self):
        """Test YAML scalar root raises ScenarioValidationError."""
        yaml_str = "just a plain string instead of mapping"
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("root must be a dictionary/mapping", str(ctx.exception))

    def test_malformed_yaml_non_dict_root_list(self):
        """Test YAML list root raises ScenarioValidationError."""
        yaml_str = """
- step_one
- step_two
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("root must be a dictionary/mapping", str(ctx.exception))

    def test_malformed_yaml_empty_content(self):
        """Test empty YAML string raises ScenarioValidationError."""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str("")
        self.assertIn("empty", str(ctx.exception).lower())

    # ───────────────────────────────────────────────────────────
    # 2. Missing Mandatory Fields
    # ───────────────────────────────────────────────────────────

    def test_missing_version_field_in_yaml(self):
        """Test missing mandatory 'version' field in YAML raises ScenarioValidationError."""
        yaml_str = """
metadata:
  title: "No Version Scenario"
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing mandatory field: 'version'", str(ctx.exception))

    def test_missing_version_field_in_dict_strict(self):
        """Test missing 'version' in dict with strict=True raises ScenarioValidationError."""
        dict_data = {
            "metadata": {"title": "No Version"},
            "timeline": [{"launch": {"command": "bash"}}],
        }
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_dict(dict_data, strict=True)
        self.assertIn("Missing mandatory field: 'version'", str(ctx.exception))

    def test_empty_version_field_raises_error(self):
        """Test empty string 'version' field raises ScenarioValidationError."""
        yaml_str = """
version: ""
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("version", str(ctx.exception).lower())

    def test_missing_timeline_field_in_yaml(self):
        """Test missing mandatory 'timeline' field in YAML raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  title: "No Timeline Manifest"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing mandatory field: 'timeline'", str(ctx.exception))

    def test_missing_timeline_field_in_dict_strict(self):
        """Test missing 'timeline' field in dict with strict=True raises ScenarioValidationError."""
        dict_data = {
            "version": "1.0",
            "metadata": {"title": "No Timeline"},
        }
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_dict(dict_data, strict=True)
        self.assertIn("Missing mandatory field: 'timeline'", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 3. Invalid Timeline Steps
    # ───────────────────────────────────────────────────────────

    def test_invalid_timeline_type_string(self):
        """Test timeline supplied as a string raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline: "launch bash then type echo"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("expected a list of steps", str(ctx.exception))

    def test_invalid_timeline_type_integer(self):
        """Test timeline supplied as an integer raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline: 42
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("expected a list of steps", str(ctx.exception))

    def test_unknown_timeline_action_raises_error(self):
        """Test unknown step action raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - nonexistent_action:
      foo: "bar"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Unknown timeline step action: 'nonexistent_action'", str(ctx.exception))

    def test_another_unknown_action_provides_supported_actions_list(self):
        """Test unknown action error lists supported actions for guidance."""
        yaml_str = """
version: "1.0"
timeline:
  - arbitrary_unsupported_verb: {}
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Supported actions:", str(ctx.exception))
        self.assertIn("launch", str(ctx.exception))
        self.assertIn("show_card", str(ctx.exception))

    def test_timeline_step_not_a_dictionary(self):
        """Test timeline step that is not a dictionary raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - "string step instead of mapping"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("expected dictionary mapping step action", str(ctx.exception))

    def test_timeline_step_empty_dictionary(self):
        """Test empty timeline step dictionary raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - {}
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("cannot be empty", str(ctx.exception).lower())

    # ───────────────────────────────────────────────────────────
    # 4. Invalid Parameters
    # ───────────────────────────────────────────────────────────

    def test_negative_duration_in_show_card(self):
        """Test negative duration in show_card step raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - show_card:
      title: "Negative Card"
      duration: -5.0
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("duration", str(ctx.exception).lower())
        self.assertIn("cannot be negative", str(ctx.exception).lower())

    def test_negative_seconds_in_pause(self):
        """Test negative seconds in pause step raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - pause:
      seconds: -2.5
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("cannot be negative", str(ctx.exception).lower())

    def test_negative_value_in_sleep(self):
        """Test negative value in sleep step raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - sleep: -1.0
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("cannot be negative", str(ctx.exception).lower())

    def test_negative_display_duration_in_inspect_modal(self):
        """Test negative display_duration in inspect_modal raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - inspect_modal:
      display_duration: -3.0
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("display_duration", str(ctx.exception).lower())
        self.assertIn("cannot be negative", str(ctx.exception).lower())

    def test_negative_timeout_in_wait_for_idle(self):
        """Test negative timeout in wait_for_idle raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - wait_for_idle:
      timeout: -10.0
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("timeout", str(ctx.exception).lower())
        self.assertIn("cannot be negative", str(ctx.exception).lower())

    def test_invalid_fps_zero(self):
        """Test fps=0 raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  fps: 0
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid FPS", str(ctx.exception))
        self.assertIn("positive integer", str(ctx.exception))

    def test_invalid_fps_negative(self):
        """Test negative fps raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  fps: -30
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid FPS", str(ctx.exception))
        self.assertIn("-30", str(ctx.exception))

    def test_invalid_dimensions_cols_too_small(self):
        """Test cols=5 below minimum 10 columns raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  cols: 5
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid dimensions: cols=5 is too small", str(ctx.exception))

    def test_invalid_dimensions_rows_too_small(self):
        """Test rows=2 below minimum 5 rows raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  rows: 2
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid dimensions: rows=2 is too small", str(ctx.exception))

    def test_invalid_resolution_negative_values(self):
        """Test negative resolution values raise ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  resolution: [-1280, 720]
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid resolution", str(ctx.exception))

    def test_invalid_resolution_wrong_element_count(self):
        """Test resolution with 1 element raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  resolution: [1280]
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Invalid resolution", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 5. Unrecognized Theme Names
    # ───────────────────────────────────────────────────────────

    def test_unrecognized_theme_name_raises_error(self):
        """Test unrecognized visual theme name raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
metadata:
  theme: "solarized-cyberpunk-fantasy"
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Unrecognized theme 'solarized-cyberpunk-fantasy'", str(ctx.exception))
        self.assertIn("catppuccin-mocha", str(ctx.exception))
        self.assertIn("tokyo-night", str(ctx.exception))
        self.assertIn("nord", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 6. Malformed Trigger Regexes
    # ───────────────────────────────────────────────────────────

    def test_malformed_trigger_regex_unclosed_parenthesis(self):
        """Test unclosed parenthesis in trigger pattern raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
triggers:
  - on_match: "([unclosed"
    action: "Enter"
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Malformed trigger regex pattern '([unclosed'", str(ctx.exception))

    def test_malformed_trigger_regex_invalid_repeat_quantifier(self):
        """Test invalid quantifier in trigger pattern raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
triggers:
  - pattern: "*invalid_quantifier"
    action: "Escape"
timeline:
  - launch:
      command: "bash"
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Malformed trigger regex pattern", str(ctx.exception))

    def test_trigger_config_direct_instantiation_validates_regex(self):
        """Test direct TriggerConfig construction validates regex pattern."""
        with self.assertRaises(ScenarioValidationError) as ctx:
            TriggerConfig(on_match="(?P<bad_group", action="Enter")
        self.assertIn("Malformed trigger regex pattern", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 7. Missing Command in launch or run_shell
    # ───────────────────────────────────────────────────────────

    def test_missing_command_in_launch_empty_dict(self):
        """Test launch step with empty parameter dict raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - launch: {}
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing command in 'launch' step", str(ctx.exception))
        self.assertIn("'command' must be a non-empty string", str(ctx.exception))

    def test_missing_command_in_launch_empty_string(self):
        """Test launch step with empty string command raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - launch:
      command: ""
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing command in 'launch' step", str(ctx.exception))

    def test_missing_command_in_run_shell_empty_dict(self):
        """Test run_shell step with empty parameter dict raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - run_shell: {}
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing command in 'run_shell' step", str(ctx.exception))

    def test_missing_command_in_run_shell_empty_string(self):
        """Test run_shell step with empty string command raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - run_shell:
      command: "   "
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing command in 'run_shell' step", str(ctx.exception))

    def test_missing_command_in_exec_empty_dict(self):
        """Test exec step alias with empty parameter dict raises ScenarioValidationError."""
        yaml_str = """
version: "1.0"
timeline:
  - exec: {}
"""
        with self.assertRaises(ScenarioValidationError) as ctx:
            ScenarioManifest.from_yaml_str(yaml_str)
        self.assertIn("Missing command in 'exec' step", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 8. Schema Validation Convenience Helper
    # ───────────────────────────────────────────────────────────

    def test_validate_manifest_helper_with_valid_dict(self):
        """Test ScenarioManifest.validate_manifest succeeds on valid dictionary."""
        valid_dict = {
            "version": "1.0",
            "metadata": {"title": "Valid Manifest", "theme": "catppuccin-mocha", "fps": 30},
            "timeline": [{"launch": {"command": "bash"}}],
        }
        # Should not raise
        ScenarioManifest.validate_manifest(valid_dict)

    def test_validate_manifest_helper_with_invalid_dict(self):
        """Test ScenarioManifest.validate_manifest raises on invalid dictionary."""
        invalid_dict = {
            "version": "1.0",
            "timeline": [{"unknown_action": {}}],
        }
        with self.assertRaises(ScenarioValidationError):
            ScenarioManifest.validate_manifest(invalid_dict)


if __name__ == "__main__":
    unittest.main()
