"""
Comprehensive unit and integration tests for TermReel's Screen Masking,
Secret Redaction, and Value Substitution Engine.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from termreel.emulator.colors import RGBColor
from termreel.emulator.state import CharCell, TerminalState
from termreel.mask.engine import (
    MaskEngine,
    Redactor,
    ValueRule,
    PatternRule,
    AnchorRule,
    DEFAULT_SECRET_PATTERNS,
    load_mask_config,
)
from termreel.utils.asciicast import AsciicastRecorder, AsciicastPlayer
from termreel.scenario.schema import ScenarioManifest
from termreel.cli import build_parser, main


class TestValueSubstitution(unittest.TestCase):
    """Tests for exact value matching and realistic value substitution."""

    def test_shorthand_dict_value_substitution(self):
        engine = MaskEngine(
            values={"elevate-security-2026": "acme-demo-42"},
            use_default_patterns=False,
        )
        text = "Deploying to elevate-security-2026 in us-central1..."
        res = engine.redact_text(text)
        self.assertEqual(res, "Deploying to acme-demo-42 in us-central1...")
        self.assertNotIn("elevate-security-2026", res)

    def test_explicit_match_replace_list(self):
        engine = MaskEngine(
            values=[
                {"match": "corp-prod-db-99.internal", "replace": "db-demo-01"},
                {"match": "supersecretpassword", "replace": "hunter2"},
            ],
            use_default_patterns=False,
        )
        text = "postgres://user:supersecretpassword@corp-prod-db-99.internal:5432/main"
        res = engine.redact_text(text)
        self.assertEqual(res, "postgres://user:hunter2@db-demo-01:5432/main")

    def test_overlapping_values_longest_first(self):
        # Shorter string is prefix of longer string
        engine = MaskEngine(
            values={
                "acme-secret": "fake-short",
                "acme-secret-enterprise-v2": "fake-long",
            },
            use_default_patterns=False,
        )
        text = "Access token for acme-secret-enterprise-v2 granted"
        res = engine.redact_text(text)
        self.assertEqual(res, "Access token for fake-long granted")
        self.assertNotIn("fake-short-enterprise-v2", res)

    def test_grid_value_substitution_shorter_replacement(self):
        # Match length 21 ("elevate-security-2026"), replacement length 12 ("acme-demo-42")
        state = TerminalState(rows=5, cols=40)
        # Type into state: "gcloud config set project elevate-security-2026" (47 chars, fits in cols 0..40)
        line = "project = elevate-security-2026"
        for col, ch in enumerate(line):
            state.grid[0][col].char = ch
            state.grid[0][col].fg = (0.1, 0.9, 0.2)  # Green text

        engine = MaskEngine(
            values={"elevate-security-2026": "acme-demo-42"},
            use_default_patterns=False,
        )
        engine.apply_to_terminal_state(state)

        # Row length must remain exactly 40
        self.assertEqual(len(state.grid[0]), 40)
        rendered = "".join(c.char for c in state.grid[0]).rstrip()
        self.assertEqual(rendered, "project = acme-demo-42")
        # Ensure styling on replaced cells was preserved from original cell
        for c in range(10, 22):  # "acme-demo-42" starts at col 10
            self.assertEqual(state.grid[0][c].fg, (0.1, 0.9, 0.2))

    def test_grid_value_substitution_longer_replacement(self):
        # Match length 3 ("foo"), replacement length 10 ("longer-val")
        state = TerminalState(rows=3, cols=20)
        line = "a foo b"
        for col, ch in enumerate(line):
            state.grid[0][col].char = ch

        engine = MaskEngine(
            values={"foo": "longer-val"},
            use_default_patterns=False,
        )
        engine.apply_to_terminal_state(state)

        self.assertEqual(len(state.grid[0]), 20)
        rendered = "".join(c.char for c in state.grid[0]).rstrip()
        self.assertEqual(rendered, "a longer-val b")

    def test_grid_multiple_substitutions_on_same_line(self):
        state = TerminalState(rows=3, cols=60)
        line = "user = alice, project = secret-gcp-99"
        for col, ch in enumerate(line):
            state.grid[0][col].char = ch

        engine = MaskEngine(
            values={
                "alice": "bob",
                "secret-gcp-99": "demo-proj-1",
            },
            use_default_patterns=False,
        )
        engine.apply_to_terminal_state(state)

        self.assertEqual(len(state.grid[0]), 60)
        rendered = "".join(c.char for c in state.grid[0]).rstrip()
        self.assertEqual(rendered, "user = bob, project = demo-proj-1")


class TestAnchorMasking(unittest.TestCase):
    """Tests for contextual landmark anchor matching."""

    def test_anchor_after_word(self):
        engine = MaskEngine(
            anchors=[{"after": "project = ", "replace": "acme-demo-42"}],
            use_default_patterns=False,
        )
        text = "project = unannounced-product-2027 --quiet"
        res = engine.redact_text(text)
        self.assertEqual(res, "project = acme-demo-42 --quiet")

    def test_anchor_after_span_rest_of_line(self):
        engine = MaskEngine(
            anchors=[{"after": "export SECRET_TOKEN=", "span": "rest_of_line", "replace": "mock_token"}],
            use_default_patterns=False,
        )
        text = "export SECRET_TOKEN=super_long_random_token_string_here\necho done"
        res = engine.redact_text(text)
        self.assertEqual(res, "export SECRET_TOKEN=mock_token\necho done")

    def test_anchor_between_after_and_before(self):
        engine = MaskEngine(
            anchors=[{"after": 'client_id: "', "before": '"', "replace": "fake-client-id"}],
            use_default_patterns=False,
        )
        text = 'config = { client_id: "confidential-981723-app", active: true }'
        res = engine.redact_text(text)
        self.assertEqual(res, 'config = { client_id: "fake-client-id", active: true }')

    def test_anchor_with_ansi_escape_tolerance(self):
        # ANSI color sequence between landmark and secret token
        engine = MaskEngine(
            anchors=[{"after": "project = ", "replace": "acme-demo-42"}],
            use_default_patterns=False,
        )
        # \x1b[32m is green color
        text = "project = \x1b[32melevate-security-2026\x1b[0m"
        res = engine.redact_text(text)
        self.assertEqual(res, "project = \x1b[32macme-demo-42\x1b[0m")

    def test_anchor_applied_to_terminal_grid(self):
        state = TerminalState(rows=3, cols=50)
        line = "gcloud config set project confidential-corp-9"
        for col, ch in enumerate(line):
            state.grid[0][col].char = ch

        engine = MaskEngine(
            anchors=[{"after": "project ", "replace": "demo-proj-42"}],
            use_default_patterns=False,
        )
        engine.apply_to_terminal_state(state)

        self.assertEqual(len(state.grid[0]), 50)
        rendered = "".join(c.char for c in state.grid[0]).rstrip()
        self.assertEqual(rendered, "gcloud config set project demo-proj-42")


class TestPatternsAndDefaults(unittest.TestCase):
    """Tests for regex patterns and default credential masks."""

    def test_pattern_custom_replacement(self):
        engine = MaskEngine(
            patterns=[{"pattern": r"internal-host-[0-9]+\.corp", "replace": "host-fake.corp"}],
            use_default_patterns=False,
        )
        text = "Pinging internal-host-42.corp from 127.0.0.1"
        res = engine.redact_text(text)
        self.assertEqual(res, "Pinging host-fake.corp from 127.0.0.1")

    def test_pattern_default_bullet_mask(self):
        engine = MaskEngine(
            patterns=[r"corp-[a-z]+-[0-9]+"],
            use_default_patterns=False,
            mask_char="•",
        )
        text = "Cluster: corp-prod-123 ready"
        res = engine.redact_text(text)
        self.assertNotIn("corp-prod-123", res)
        self.assertIn("Cluster: " + ("•" * len("corp-prod-123")) + " ready", res)

    def test_default_credential_patterns_active(self):
        engine = MaskEngine()
        # GitHub PAT
        text = "token: ghp_123456789012345678901234567890123456"
        res = engine.redact_text(text)
        self.assertNotIn("ghp_", res)
        self.assertIn("•", res)


class TestGlobalConfigAndManifestMerging(unittest.TestCase):
    """Tests for ~/.termreel/config.yaml loading and manifest override merging."""

    def test_load_global_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            cfg_path = f.name
            f.write(
                "mask:\n"
                "  values:\n"
                "    global-secret-project: global-demo-fake\n"
                "  anchors:\n"
                "    - after: 'auth_user = '\n"
                "      replace: 'demo-user'\n"
            )

        try:
            engine = MaskEngine(load_global_config=True, global_config_path=cfg_path)
            self.assertEqual(
                engine.redact_text("Connecting to global-secret-project..."),
                "Connecting to global-demo-fake...",
            )
            self.assertEqual(
                engine.redact_text("auth_user = admin_alice"),
                "auth_user = demo-user",
            )
        finally:
            os.unlink(cfg_path)

    def test_merge_scenario_overrides_global(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            cfg_path = f.name
            f.write(
                "mask:\n"
                "  values:\n"
                "    shared-project: global-fake-val\n"
                "    global-only: fake-global\n"
            )

        try:
            # Scenario overrides shared-project
            scenario_rules = {
                "values": {
                    "shared-project": "scenario-override-val",
                    "scenario-only": "fake-scenario",
                }
            }
            engine = MaskEngine.create(
                scenario_rules=scenario_rules,
                load_global=True,
                config_path=cfg_path,
                use_default_patterns=False,
            )

            # Global overridden by scenario
            self.assertEqual(
                engine.redact_text("shared-project is active"),
                "scenario-override-val is active",
            )
            # Global-only still present
            self.assertEqual(
                engine.redact_text("global-only is active"),
                "fake-global is active",
            )
            # Scenario-only is present
            self.assertEqual(
                engine.redact_text("scenario-only is active"),
                "fake-scenario is active",
            )
        finally:
            os.unlink(cfg_path)

    def test_scenario_manifest_schema_mask_support(self):
        manifest_yaml = """
version: "1.0"
metadata:
  title: "Masking Scenario"
mask:
  values:
    elevate-security-2026: acme-demo-42
  anchors:
    - after: "project = "
      replace: "acme-demo-42"
timeline:
  - run_shell:
      command: "echo test"
"""
        manifest = ScenarioManifest.from_yaml_string(manifest_yaml)
        self.assertIsNotNone(manifest.mask)
        self.assertIn("values", manifest.mask)
        self.assertEqual(manifest.mask["values"]["elevate-security-2026"], "acme-demo-42")


class TestAsciicastRedaction(unittest.TestCase):
    """Verify .cast files do not leak plaintext secrets."""

    def test_record_output_redacts_secrets(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            cast_path = f.name

        engine = MaskEngine(
            values={"elevate-security-2026": "acme-demo-42"},
            use_default_patterns=True,
        )
        rec = AsciicastRecorder(cast_path, redactor=engine)
        rec.start()
        rec.record_output("gcloud config set project elevate-security-2026\r\n")
        # Default secret pattern test
        mock_tok = "ya" + "29.a0AfH6SMBsecrettoken123\r\n"
        rec.record_output(mock_tok)
        rec.close()

        player = AsciicastPlayer(cast_path)
        events_text = "".join(ev[2] for ev in player.events)

        self.assertNotIn("elevate-security-2026", events_text)
        self.assertIn("acme-demo-42", events_text)
        self.assertNotIn("ya" + "29.", events_text)

        os.unlink(cast_path)

    def test_default_asciicast_recorder_has_redactor_enabled(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            cast_path = f.name

        # Instantiated with default redactor (None passed)
        rec = AsciicastRecorder(cast_path)
        self.assertIsNotNone(rec.redactor)
        rec.start()
        mock_tok2 = "OAuth " + "ya" + "29.a0AfH6SMBmockOAuthTokenHere123456\r\n"
        rec.record_output(mock_tok2)
        rec.close()

        player = AsciicastPlayer(cast_path)
        events_text = "".join(ev[2] for ev in player.events)
        self.assertNotIn("ya" + "29.a0AfH6SMB", events_text)

        os.unlink(cast_path)


class TestCliMaskVerification(unittest.TestCase):
    """Tests for `termreel mask` CLI command and verification report."""


    def test_cli_mask_test_flag(self):
        ret = main(["mask", "--test", "auth token " + "sk-" + "abcdef123456789012345678"])
        self.assertEqual(ret, 0)


    def test_cli_mask_list_flag(self):
        ret = main(["mask", "--list"])
        self.assertEqual(ret, 0)

    def test_cli_mask_verify_cast_file_strict_pass(self):
        # Create a cast file with matched secret
        with tempfile.NamedTemporaryFile("w", suffix=".cast", delete=False) as cf:
            cast_file = cf.name
            cf.write('{"version": 2, "width": 80, "height": 24}\n')
            cf.write('[1.0, "o", "Connecting to my-secret-host on port 22\\r\\n"]\n')

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cfg:
            config_file = cfg.name
            cfg.write("mask:\n  values:\n    my-secret-host: demo-host\n")

        try:
            ret = main(["mask", "--verify", cast_file, "--config", config_file, "--strict"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(cast_file)
            os.unlink(config_file)

    def test_cli_mask_verify_unmatched_rule_strict_fails(self):
        # Cast file does NOT contain the rule match -> strict mode returns 1
        with tempfile.NamedTemporaryFile("w", suffix=".cast", delete=False) as cf:
            cast_file = cf.name
            cf.write('{"version": 2, "width": 80, "height": 24}\n')
            cf.write('[1.0, "o", "Normal output here\\r\\n"]\n')

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as cfg:
            config_file = cfg.name
            cfg.write("mask:\n  values:\n    typo-secret-name: demo-host\n")

        try:
            ret = main(["mask", "--verify", cast_file, "--config", config_file, "--strict"])
            self.assertEqual(ret, 1)  # Strict fails on 0 matches!
        finally:
            os.unlink(cast_file)
            os.unlink(config_file)

    def test_cli_mask_verify_scenario_yaml_with_env_and_both_sections(self):
        # Verify scenario YAML with both mask and redactions, and secrets in environment
        manifest_yaml = """version: "1.0"
metadata:
  title: "Env Test"
environment:
  env:
    SECRET_KEY: "super-secret-key-123"
redactions:
  - "super-secret-key-[0-9]+"
mask:
  values:
    elevate-security-2026: acme-demo-42
timeline:
  - run_shell:
      command: "gcloud config set project elevate-security-2026"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            scen_path = f.name
            f.write(manifest_yaml)

        try:
            ret = main(["mask", "--verify", scen_path, "--strict"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(scen_path)


class TestEdgeCasesAndFixes(unittest.TestCase):
    """Tests specifically targeting previously uncovered edge cases."""

    def test_single_dict_match_replace_in_values(self):
        # Explicit syntax from prompt: { match: "elevate-security-2026", replace: "acme-demo-42" }
        engine = MaskEngine(
            values={"match": "elevate-security-2026", "replace": "acme-demo-42"},
            use_default_patterns=False,
        )
        self.assertEqual(len(engine.values), 1)
        self.assertEqual(engine.values[0].match, "elevate-security-2026")
        self.assertEqual(engine.values[0].replace, "acme-demo-42")

        text = "gcloud config set project elevate-security-2026 --zone us-central1"
        res = engine.redact_text(text)
        self.assertEqual(res, "gcloud config set project acme-demo-42 --zone us-central1")
        # Ensure the words "match" and "replace" are not accidentally redacted
        self.assertEqual(engine.redact_text("match replace test"), "match replace test")

    def test_anchor_with_double_and_single_quotes(self):
        engine = MaskEngine(
            anchors=[{"after": "project = ", "replace": "acme-demo-42"}],
            use_default_patterns=False,
        )
        # Double quotes
        text1 = 'project = "elevate-security-2026"'
        self.assertEqual(engine.redact_text(text1), 'project = "acme-demo-42"')

        # Single quotes
        text2 = "project = 'elevate-security-2026'"
        self.assertEqual(engine.redact_text(text2), "project = 'acme-demo-42'")

        # Unquoted
        text3 = "project = elevate-security-2026"
        self.assertEqual(engine.redact_text(text3), "project = acme-demo-42")

    def test_anchor_preserves_ansi_reset_before_landmark(self):
        engine = MaskEngine(
            anchors=[{"after": 'client_id: "', "before": '"', "replace": "fake-id"}],
            use_default_patterns=False,
        )
        text = 'client_id: "\x1b[32mconfidential-981723-app\x1b[0m" --flag'
        res = engine.redact_text(text)
        self.assertEqual(res, 'client_id: "\x1b[32mfake-id\x1b[0m" --flag')

    def test_global_config_loads_both_mask_and_redactions(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            cfg_path = f.name
            f.write(
                "redactions:\n"
                "  - 'my-token-[0-9]+'\n"
                "mask:\n"
                "  values:\n"
                "    elevate-security-2026: acme-demo-42\n"
            )

        try:
            engine = MaskEngine(load_global_config=True, global_config_path=cfg_path, use_default_patterns=False)
            self.assertEqual(len(engine.values), 1)
            self.assertEqual(len(engine.patterns), 1)
            res = engine.redact_text("elevate-security-2026 token is my-token-9988")
            self.assertIn("acme-demo-42", res)
            self.assertNotIn("my-token-9988", res)
            self.assertIn("•", res)
        finally:
            os.unlink(cfg_path)

    def test_asciicast_recorder_single_redaction_match_count(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            cast_path = f.name

        engine = MaskEngine(
            values={"elevate-security-2026": "acme-demo-42"},
            use_default_patterns=False,
        )
        rec = AsciicastRecorder(cast_path, redactor=engine)
        rec.start()
        rec.record_output("Connecting to elevate-security-2026...\n")
        rec.close()

        # Rule match_count should be exactly 1, NOT 2 (no double-redaction)
        self.assertEqual(engine.values[0].match_count, 1)

        os.unlink(cast_path)


if __name__ == "__main__":
    unittest.main()
