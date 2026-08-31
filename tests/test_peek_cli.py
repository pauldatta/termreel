"""
Unit and integration tests for TermReel Peek Client, Web Dashboard, and CLI integration.
Tests PeekClient session discovery, HUD rendering, live follow stream, image capture,
web server endpoints, and CLI subcommand invocations.
"""

import argparse
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch, MagicMock

from termreel.emulator.state import TerminalState
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.telemetry.models import SessionMetadata, ScreenSnapshot
from termreel.telemetry.registry import SessionRegistry
from termreel.telemetry.server import TelemetryServer
from termreel.peek import PeekClient
from termreel.cli import build_parser, main, cmd_peek


class TestPeekClientDiscovery(unittest.TestCase):
    """Verify session discovery by ID, prefix, PID, and latest active fallback."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_peek_disc_")
        self.registry = SessionRegistry(directory=self.temp_dir)
        self.client = PeekClient(registry=self.registry)

        self.s1 = SessionMetadata(
            session_id="session_alpha_1001",
            pid=os.getpid(),
            scenario_title="Alpha Scenario",
            output_video="/tmp/alpha.mp4",
            started_at=100.0,
            status="running",
        )
        self.s2 = SessionMetadata(
            session_id="session_beta_2002",
            pid=999999,  # Inactive PID
            scenario_title="Beta Scenario",
            output_video="/tmp/beta.mp4",
            started_at=200.0,
            status="running",
        )
        self.registry.register(self.s1)
        self.registry.register(self.s2)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_target_session_default_latest_active(self):
        """Default target (None) returns latest active running session."""
        target = self.client.find_target_session(None)
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, "session_alpha_1001")

    def test_find_target_session_by_exact_id(self):
        """Lookup by exact session ID."""
        target = self.client.find_target_session("session_beta_2002")
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, "session_beta_2002")

    def test_find_target_session_by_prefix(self):
        """Lookup by session ID prefix."""
        target = self.client.find_target_session("session_beta")
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, "session_beta_2002")

    def test_find_target_session_by_pid(self):
        """Lookup by process PID."""
        target = self.client.find_target_session(str(os.getpid()))
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, "session_alpha_1001")

    def test_find_target_session_nonexistent(self):
        """Nonexistent session returns None."""
        target = self.client.find_target_session("nonexistent_session_9999")
        self.assertIsNone(target)


class TestPeekClientSnapshotsAndQueries(unittest.TestCase):
    """Verify live status and screen queries via socket and fallback persistence."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_peek_snap_")
        self.registry = SessionRegistry(directory=self.temp_dir)
        self.client = PeekClient(registry=self.registry)

        self.state = TerminalState(rows=24, cols=80)
        self.session_dir = os.path.join(self.temp_dir, "sess_snap_test")
        os.makedirs(self.session_dir, exist_ok=True)
        self.socket_path = os.path.join(self.session_dir, "test.sock")

        self.metadata = SessionMetadata(
            session_id="sess_snap_test",
            pid=os.getpid(),
            scenario_title="Snapshot Verification",
            scenario_path="scenarios/test.yaml",
            output_video="/tmp/snap.mp4",
            current_step_index=2,
            total_steps=5,
            current_step_type="type",
            current_step_desc="Running test command",
            fps=30,
            rendered_frames=120,
            elapsed_seconds=4.0,
            socket_path=self.socket_path,
            status="running",
        )
        self.registry.register(self.metadata)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_query_status_and_screen_via_socket(self):
        """Query status and screen snapshot over live UNIX socket."""
        # Write some text into state
        from termreel.emulator.parser import ANSIParser
        parser = ANSIParser(self.state)
        parser.feed("\033[1;32mTermReel Test Prompt\033[0m > echo hello\nhello world\n")

        server = TelemetryServer(
            session_id="sess_snap_test",
            state=self.state,
            metadata=self.metadata,
            registry=self.registry,
            session_dir=self.session_dir,
        )
        server.start()

        try:
            # Query Status
            status = self.client.query_status(self.metadata)
            self.assertEqual(status.get("session_id"), "sess_snap_test")
            self.assertEqual(status.get("current_step_index"), 2)
            self.assertEqual(status.get("rendered_frames"), 120)

            # Query Screen
            screen_text, cursor = self.client.query_screen(self.metadata)
            self.assertIn("hello world", screen_text)
            self.assertIsInstance(cursor, dict)
            self.assertIn("row", cursor)
            self.assertIn("col", cursor)

            # Render Snapshot (formatted HUD)
            hud_snapshot = self.client.render_snapshot(self.metadata, raw=False)
            self.assertIn("TermReel Live Peek", hud_snapshot)
            self.assertIn("Snapshot Verification", hud_snapshot)
            self.assertIn("Step 2/5", hud_snapshot)
            self.assertIn("Frames:", hud_snapshot)
            self.assertIn("120", hud_snapshot)
            self.assertIn("hello world", hud_snapshot)

            # Render Snapshot (raw plain text)
            raw_snapshot = self.client.render_snapshot(self.metadata, raw=True)
            self.assertNotIn("TermReel Live Peek", raw_snapshot)
            self.assertIn("hello world", raw_snapshot)

        finally:
            server.stop()

    def test_query_status_and_screen_fallback_file(self):
        """Query status and screen using atomic fallback files when socket is unavailable."""
        # Intentionally do not start socket server, but write fallback files
        status_data = self.metadata.to_dict()
        status_file = os.path.join(self.session_dir, "status.json")
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f)

        ansi_file = os.path.join(self.session_dir, "screen.ansi")
        with open(ansi_file, "w", encoding="utf-8") as f:
            f.write("FALLBACK_TERMINAL_SCREEN_CONTENT\nLine 2")

        # Invalidate socket path to guarantee fallback path is taken
        self.metadata.socket_path = "/tmp/nonexistent_socket_path_xyz.sock"

        status = self.client.query_status(self.metadata)
        self.assertEqual(status.get("session_id"), "sess_snap_test")
        self.assertEqual(status.get("scenario_title"), "Snapshot Verification")

        screen_text, cursor = self.client.query_screen(self.metadata)
        self.assertIn("FALLBACK_TERMINAL_SCREEN_CONTENT", screen_text)

        snapshot = self.client.render_snapshot(self.metadata, raw=False)
        self.assertIn("FALLBACK_TERMINAL_SCREEN_CONTENT", snapshot)
        self.assertIn("TermReel Live Peek", snapshot)

    def test_ansi_to_html_styling(self):
        """Verify ANSI SGR converter correctly generates HTML styles."""
        ansi_sample = "\033[1;31mBold Red\033[0m Normal \033[4mUnderline\033[0m"
        html_out = PeekClient.ansi_to_html(ansi_sample)

        self.assertIn("<span", html_out)
        self.assertIn("font-weight:bold", html_out)
        self.assertIn("text-decoration:underline", html_out)
        self.assertIn("Bold Red", html_out)
        self.assertIn("Normal", html_out)

        # TrueColor RGB
        rgb_ansi = "\033[38;2;120;200;80mTrueColor Text\033[0m"
        html_rgb = PeekClient.ansi_to_html(rgb_ansi)
        self.assertIn("color:rgb(120,200,80)", html_rgb)
        self.assertIn("TrueColor Text", html_rgb)


class TestPeekClientListAndFollow(unittest.TestCase):
    """Verify list_sessions formatting, follow stream, and image capture."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_peek_list_")
        self.registry = SessionRegistry(directory=self.temp_dir)
        self.client = PeekClient(registry=self.registry)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_sessions_formatting(self):
        """Verify session listing table contains headers, rows, and aligned columns."""
        # Empty case
        self.assertIn("No active or recent", self.client.list_sessions())

        # Populate sessions
        s1 = SessionMetadata(
            session_id="tr_sess_001",
            pid=4100,
            scenario_title="E2E Workflow Test",
            current_step_index=3,
            total_steps=6,
            current_step_type="type",
            rendered_frames=180,
            elapsed_seconds=6.0,
            status="running",
        )
        s2 = SessionMetadata(
            session_id="tr_sess_002",
            pid=4101,
            scenario_title="Batch Child Beta",
            current_step_index=1,
            total_steps=4,
            current_step_type="launch",
            rendered_frames=30,
            elapsed_seconds=1.0,
            status="completed",
        )
        self.registry.register(s1)
        self.registry.register(s2)

        table = self.client.list_sessions()
        self.assertIn("SESSION ID", table)
        self.assertIn("PID", table)
        self.assertIn("STATUS", table)
        self.assertIn("SCENARIO", table)
        self.assertIn("CURRENT STEP", table)
        self.assertIn("FRAMES", table)
        self.assertIn("ELAPSED", table)

        self.assertIn("tr_sess_001", table)
        self.assertIn("4100", table)
        self.assertIn("RUNNING", table)
        self.assertIn("E2E Workflow Test", table)
        self.assertIn("3/6 (type)", table)
        self.assertIn("180", table)

        self.assertIn("tr_sess_002", table)
        self.assertIn("COMPLETED", table)

    def test_follow_mode_bounded_execution(self):
        """Verify follow() runs for bounded iterations without terminal crash."""
        session = SessionMetadata(
            session_id="tr_follow_test",
            pid=os.getpid(),
            scenario_title="Follow Test",
            status="running",
        )
        self.registry.register(session)

        # Run follow for 2 iterations in headless test environment
        output_catcher = io.StringIO()
        with patch("sys.stdout", output_catcher):
            self.client.follow(session, interval=0.01, max_iterations=2)

        out = output_catcher.getvalue()
        # Alternate screen buffer escape sequence
        self.assertIn("\x1b[?1049h", out)
        # Alternate screen exit escape sequence
        self.assertIn("\x1b[?1049l", out)
        self.assertIn("TermReel Live Peek", out)

    def test_capture_image_fallback_cairo(self):
        """Verify capture_image synthesizes valid PNG image via Cairo fallback."""
        session = SessionMetadata(
            session_id="tr_cap_test",
            pid=os.getpid(),
            scenario_title="Image Capture Test",
            status="running",
        )
        self.registry.register(session)

        target_png = os.path.join(self.temp_dir, "test_capture.png")
        success = self.client.capture_image(session, target_png)

        self.assertTrue(success)
        self.assertTrue(os.path.exists(target_png))
        self.assertGreater(os.path.getsize(target_png), 100)


class TestPeekWebDashboard(unittest.TestCase):
    """Verify local HTTP server dashboard endpoints and JSON API."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_peek_web_")
        self.registry = SessionRegistry(directory=self.temp_dir)
        self.client = PeekClient(registry=self.registry)

        self.session = SessionMetadata(
            session_id="tr_web_sess_8989",
            pid=os.getpid(),
            scenario_title="Web Dashboard Verification",
            output_video="/tmp/web_demo.mp4",
            current_step_index=1,
            total_steps=3,
            current_step_type="exec",
            current_step_desc="Dashboard probe",
            rendered_frames=45,
            elapsed_seconds=1.5,
            status="running",
        )
        self.registry.register(self.session)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_web_server_endpoints(self):
        """Test GET /, /api/snapshot, /api/status, and /api/raw."""
        # Find an available open port
        sock = socket.socket()
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = self.client.create_web_server(self.session, port=port)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()

        try:
            base_url = f"http://127.0.0.1:{port}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            # 1. GET / (HTML Dashboard)
            req_html = opener.open(f"{base_url}/", timeout=2.0)
            self.assertEqual(req_html.status, 200)
            html_body = req_html.read().decode("utf-8")
            self.assertIn("TermReel Live Peek", html_body)
            self.assertIn("Web Dashboard Verification", html_body)
            self.assertIn("status-badge", html_body)

            # 2. GET /api/snapshot (JSON)
            req_snap = opener.open(f"{base_url}/api/snapshot", timeout=2.0)
            self.assertEqual(req_snap.status, 200)
            snap_data = json.loads(req_snap.read().decode("utf-8"))
            self.assertIn("status", snap_data)
            self.assertIn("screen_html", snap_data)
            self.assertIn("screen_text", snap_data)
            self.assertEqual(snap_data["status"]["session_id"], "tr_web_sess_8989")

            # 3. GET /api/status (JSON)
            req_stat = opener.open(f"{base_url}/api/status", timeout=2.0)
            self.assertEqual(req_stat.status, 200)
            stat_data = json.loads(req_stat.read().decode("utf-8"))
            self.assertEqual(stat_data["current_step_type"], "exec")

            # 4. GET /api/raw (Plain text)
            req_raw = opener.open(f"{base_url}/api/raw", timeout=2.0)
            self.assertEqual(req_raw.status, 200)
            raw_text = req_raw.read().decode("utf-8")
            self.assertIsInstance(raw_text, str)

        finally:
            server.shutdown()
            server.server_close()


class TestPeekCLI(unittest.TestCase):
    """Verify termreel peek CLI argument parsing, execution flags, and error handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_peek_cli_")
        self.registry = SessionRegistry(directory=self.temp_dir)

        self.session = SessionMetadata(
            session_id="cli_test_session",
            pid=os.getpid(),
            scenario_title="CLI Integration Scenario",
            status="running",
        )
        self.registry.register(self.session)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parser_options(self):
        """Verify argparse options for peek subcommand."""
        parser = build_parser()

        # Positional session
        args = parser.parse_args(["peek", "my_session"])
        self.assertEqual(args.subcommand, "peek")
        self.assertEqual(args.session, "my_session")
        self.assertFalse(args.follow)
        self.assertFalse(args.list)
        self.assertIsNone(args.web)

        # Follow flag
        args_f = parser.parse_args(["peek", "-f"])
        self.assertTrue(args_f.follow)

        # Watch alias
        args_w = parser.parse_args(["peek", "--watch"])
        self.assertTrue(args_w.follow)

        # List flag
        args_l = parser.parse_args(["peek", "--list"])
        self.assertTrue(args_l.list)

        # Image flag
        args_img = parser.parse_args(["peek", "--image", "out.png"])
        self.assertEqual(args_img.image, "out.png")

        # Web flag default port 8989
        args_web_def = parser.parse_args(["peek", "--web"])
        self.assertEqual(args_web_def.web, 8989)

        # Web flag custom port 9090
        args_web_custom = parser.parse_args(["peek", "--web", "9090"])
        self.assertEqual(args_web_custom.web, 9090)

        # Raw flag
        args_raw = parser.parse_args(["peek", "--raw"])
        self.assertTrue(args_raw.raw)

        # Interval flag
        args_int = parser.parse_args(["peek", "--interval", "0.25"])
        self.assertEqual(args_int.interval, 0.25)

    def test_cmd_peek_list_invocation(self):
        """Verify cmd_peek handles --list successfully."""
        args = argparse.Namespace(
            list=True,
            session=None,
            follow=False,
            image=None,
            web=None,
            raw=False,
            interval=0.1,
        )
        with patch("termreel.peek.SessionRegistry", return_value=self.registry):
            ret = cmd_peek(args)
            self.assertEqual(ret, 0)

    def test_cmd_peek_nonexistent_session_returns_error(self):
        """Verify cmd_peek returns error code 1 when target session is not found."""
        args = argparse.Namespace(
            list=False,
            session="definitely_does_not_exist",
            follow=False,
            image=None,
            web=None,
            raw=False,
            interval=0.1,
        )
        with patch("termreel.peek.SessionRegistry", return_value=self.registry):
            ret = cmd_peek(args)
            self.assertEqual(ret, 1)

    def test_cmd_peek_snapshot_invocation(self):
        """Verify cmd_peek renders snapshot successfully."""
        args = argparse.Namespace(
            list=False,
            session="cli_test_session",
            follow=False,
            image=None,
            web=None,
            raw=False,
            interval=0.1,
        )
        with patch("termreel.peek.SessionRegistry", return_value=self.registry):
            ret = cmd_peek(args)
            self.assertEqual(ret, 0)

    def test_cmd_peek_image_capture_invocation(self):
        """Verify cmd_peek --image exports screenshot."""
        out_png = os.path.join(self.temp_dir, "cli_snap.png")
        args = argparse.Namespace(
            list=False,
            session="cli_test_session",
            follow=False,
            image=out_png,
            web=None,
            raw=False,
            interval=0.1,
        )
        with patch("termreel.peek.SessionRegistry", return_value=self.registry):
            ret = cmd_peek(args)
            self.assertEqual(ret, 0)
            self.assertTrue(os.path.exists(out_png))


if __name__ == "__main__":
    unittest.main()
