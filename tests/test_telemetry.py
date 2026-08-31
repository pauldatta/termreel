"""
Unit and integration tests for TermReel Telemetry Subsystem.
Tests SessionMetadata, ScreenSnapshot, SessionRegistry, TelemetryServer IPC,
fallback atomic persistence, clean shutdown, and ScenarioRunner integration.
"""

import json
import os
import shutil
import socket
import tempfile
import time
import unittest

from termreel.emulator.state import TerminalState, CharCell
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.scenario.schema import ScenarioManifest
from termreel.scenario.runner import ScenarioRunner
from termreel.telemetry.models import SessionMetadata, ScreenSnapshot
from termreel.telemetry.registry import SessionRegistry
from termreel.telemetry.server import TelemetryServer


class TestTelemetryModels(unittest.TestCase):
    """Verify serialization, deserialization, and state snapshot conversion."""

    def test_session_metadata_serialization(self):
        meta = SessionMetadata(
            session_id="test_sess_01",
            pid=12345,
            scenario_title="Demo Scenario",
            scenario_path="/tmp/scenario.yaml",
            output_video="/tmp/out.mp4",
            started_at=1000.0,
            current_step_index=2,
            total_steps=5,
            current_step_type="type",
            current_step_desc="Typing bash command",
            fps=30,
            rendered_frames=60,
            elapsed_seconds=2.0,
            socket_path="/tmp/test.sock",
            status="running",
        )

        d = meta.to_dict()
        self.assertEqual(d["session_id"], "test_sess_01")
        self.assertEqual(d["pid"], 12345)
        self.assertEqual(d["fps"], 30)
        self.assertEqual(d["status"], "running")

        # JSON round-trip
        json_str = meta.to_json()
        restored = SessionMetadata.from_json(json_str)
        self.assertEqual(restored.session_id, meta.session_id)
        self.assertEqual(restored.pid, meta.pid)
        self.assertEqual(restored.current_step_desc, meta.current_step_desc)
        self.assertEqual(restored.status, meta.status)

    def test_screen_snapshot_serialization(self):
        snap = ScreenSnapshot(
            text="Hello World",
            ansi_text="\033[38;2;255;0;0mHello World\033[0m",
            cursor_row=1,
            cursor_col=5,
            cursor_visible=True,
            rows=24,
            cols=80,
            timestamp=1234.56,
        )

        d = snap.to_dict()
        self.assertEqual(d["text"], "Hello World")
        self.assertEqual(d["cursor_row"], 1)
        self.assertEqual(d["cols"], 80)

        json_str = snap.to_json()
        restored = ScreenSnapshot.from_json(json_str)
        self.assertEqual(restored.text, snap.text)
        self.assertEqual(restored.ansi_text, snap.ansi_text)
        self.assertEqual(restored.cursor_row, snap.cursor_row)
        self.assertEqual(restored.timestamp, snap.timestamp)

    def test_screen_snapshot_from_terminal_state(self):
        state = TerminalState(rows=10, cols=30)
        for ch in "TermReel Snapshot":
            state.write_char(ch)

        state.cursor.row = 2
        state.cursor.col = 15
        state.cursor.visible = True

        snap = ScreenSnapshot.from_terminal_state(state)
        self.assertEqual(snap.rows, 10)
        self.assertEqual(snap.cols, 30)
        self.assertEqual(snap.cursor_row, 2)
        self.assertEqual(snap.cursor_col, 15)
        self.assertTrue(snap.cursor_visible)
        self.assertIn("TermReel Snapshot", snap.text)
        self.assertIn("TermReel Snapshot", snap.ansi_text)


class TestSessionRegistry(unittest.TestCase):
    """Verify SessionRegistry registration, updates, liveness, and pruning."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_reg_test_")
        self.registry = SessionRegistry(directory=self.temp_dir)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_register_and_get_session(self):
        meta = SessionMetadata(
            session_id="reg_test_101",
            pid=os.getpid(),
            scenario_title="Registry Test",
            status="running",
        )
        sid = self.registry.register(meta)
        self.assertEqual(sid, "reg_test_101")

        sess_file = os.path.join(self.temp_dir, "session_reg_test_101.json")
        self.assertTrue(os.path.exists(sess_file))

        # Retrieve by session_id
        retrieved = self.registry.get_session("reg_test_101")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.session_id, "reg_test_101")
        self.assertEqual(retrieved.scenario_title, "Registry Test")

        # Retrieve by PID
        retrieved_by_pid = self.registry.get_session(str(os.getpid()))
        self.assertIsNotNone(retrieved_by_pid)
        self.assertEqual(retrieved_by_pid.session_id, "reg_test_101")

    def test_update_session(self):
        meta = SessionMetadata(
            session_id="upd_test_202",
            pid=os.getpid(),
            current_step_index=0,
            status="running",
        )
        self.registry.register(meta)

        self.registry.update("upd_test_202", current_step_index=3, current_step_desc="Active step")
        retrieved = self.registry.get_session("upd_test_202")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.current_step_index, 3)
        self.assertEqual(retrieved.current_step_desc, "Active step")

    def test_liveness_and_active_sessions(self):
        # Active session: alive pid and running
        active_meta = SessionMetadata(
            session_id="active_sess",
            pid=os.getpid(),
            started_at=time.time(),
            status="running",
        )
        self.registry.register(active_meta)

        # Inactive session: dead pid
        dead_meta = SessionMetadata(
            session_id="dead_sess",
            pid=99999999,
            started_at=time.time() - 100,
            status="running",
        )
        self.registry.register(dead_meta)

        # Completed session: alive pid but completed
        completed_meta = SessionMetadata(
            session_id="comp_sess",
            pid=os.getpid(),
            started_at=time.time() - 50,
            status="completed",
        )
        self.registry.register(completed_meta)

        active_list = self.registry.list_sessions(active_only=True)
        active_ids = [s.session_id for s in active_list]
        self.assertIn("active_sess", active_ids)
        self.assertNotIn("dead_sess", active_ids)
        self.assertNotIn("comp_sess", active_ids)

        all_list = self.registry.list_sessions(active_only=False)
        all_ids = [s.session_id for s in all_list]
        self.assertIn("active_sess", all_ids)
        self.assertIn("dead_sess", all_ids)
        self.assertIn("comp_sess", all_ids)

        latest = self.registry.get_latest_session()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.session_id, "active_sess")

    def test_unregister(self):
        meta = SessionMetadata(
            session_id="unreg_test",
            pid=os.getpid(),
            status="running",
        )
        self.registry.register(meta)

        # Unregister with remove_file=False -> sets status to completed
        self.registry.unregister("unreg_test", remove_file=False)
        sess = self.registry.get_session("unreg_test")
        self.assertIsNotNone(sess)
        self.assertEqual(sess.status, "completed")
        self.assertEqual(len(self.registry.list_sessions(active_only=True)), 0)

        # Unregister with remove_file=True -> deletes file
        self.registry.unregister("unreg_test", remove_file=True)
        self.assertIsNone(self.registry.get_session("unreg_test"))

    def test_prune_stale(self):
        dead_meta_1 = SessionMetadata(session_id="stale_1", pid=99999991, status="running")
        dead_meta_2 = SessionMetadata(session_id="stale_2", pid=99999992, status="running")
        alive_meta = SessionMetadata(session_id="alive_1", pid=os.getpid(), status="running")

        self.registry.register(dead_meta_1)
        self.registry.register(dead_meta_2)
        self.registry.register(alive_meta)

        pruned = self.registry.prune_stale()
        self.assertEqual(pruned, 2)
        self.assertIsNone(self.registry.get_session("stale_1"))
        self.assertIsNone(self.registry.get_session("stale_2"))
        self.assertIsNotNone(self.registry.get_session("alive_1"))


class TestTelemetryServer(unittest.TestCase):
    """Verify TelemetryServer socket IPC, JSON-RPC handling, and atomic fallback files."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="termreel_srv_test_")
        self.registry = SessionRegistry(directory=self.temp_dir)
        self.state = TerminalState(rows=12, cols=40)
        self.renderer = CairoTerminalRenderer(width=640, height=360)
        self.session_id = "test_server_sess"
        self.socket_path = os.path.join(self.temp_dir, "test_srv.sock")

        self.metadata = SessionMetadata(
            session_id=self.session_id,
            pid=os.getpid(),
            scenario_title="Server IPC Test",
            socket_path=self.socket_path,
            status="running",
            total_steps=3,
        )

        self.server = TelemetryServer(
            session_id=self.session_id,
            state=self.state,
            renderer=self.renderer,
            metadata=self.metadata,
            registry=self.registry,
            session_dir=self.temp_dir,
        )

    def tearDown(self):
        if self.server._running:
            self.server.stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _send_cmd(self, client: socket.socket, cmd_obj: dict) -> dict:
        client.sendall((json.dumps(cmd_obj) + "\n").encode("utf-8"))
        buf = ""
        while "\n" not in buf:
            chunk = client.recv(4096).decode("utf-8")
            if not chunk:
                break
            buf += chunk
        line, _ = buf.split("\n", 1)
        return json.loads(line)

    def test_server_startup_fallback_files_and_shutdown(self):
        # Write some text into state
        for ch in "Terminal Active":
            self.state.write_char(ch)

        self.server.start()
        self.assertTrue(self.server._running)
        self.assertTrue(os.path.exists(self.socket_path))

        # Check fallback files
        status_file = os.path.join(self.temp_dir, "status.json")
        screen_file = os.path.join(self.temp_dir, "screen.ansi")
        self.assertTrue(os.path.exists(status_file))
        self.assertTrue(os.path.exists(screen_file))

        with open(status_file, "r") as f:
            status_data = json.load(f)
        self.assertEqual(status_data["session_id"], self.session_id)
        self.assertEqual(status_data["status"], "running")

        with open(screen_file, "r") as f:
            ansi_content = f.read()
        self.assertIn("Terminal Active", ansi_content)

        # Stop server
        self.server.stop(status="completed")
        self.assertFalse(self.server._running)
        self.assertFalse(os.path.exists(self.socket_path))

        with open(status_file, "r") as f:
            status_data = json.load(f)
        self.assertEqual(status_data["status"], "completed")

    def test_server_socket_ipc_commands(self):
        for ch in "Echo 42":
            self.state.write_char(ch)

        self.server.start()

        # Connect client socket
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.socket_path)

        try:
            # 1. GET_STATUS
            resp = self._send_cmd(client, {"jsonrpc": "2.0", "method": "GET_STATUS", "id": 1})
            self.assertEqual(resp["id"], 1)
            self.assertEqual(resp["result"]["session_id"], self.session_id)
            self.assertEqual(resp["result"]["status"], "running")

            # 2. GET_SCREEN
            resp = self._send_cmd(client, {"jsonrpc": "2.0", "method": "GET_SCREEN", "id": 2})
            self.assertEqual(resp["id"], 2)
            self.assertIn("Echo 42", resp["result"]["text"])
            self.assertIn("Echo 42", resp["result"]["ansi_text"])

            # 3. GET_RAW
            resp = self._send_cmd(client, {"jsonrpc": "2.0", "method": "GET_RAW", "id": 3})
            self.assertEqual(resp["id"], 3)
            self.assertIn("Echo 42", resp["result"])

            # 4. CAPTURE_IMAGE (in-memory base64)
            resp = self._send_cmd(client, {"jsonrpc": "2.0", "method": "CAPTURE_IMAGE", "id": 4})
            self.assertEqual(resp["id"], 4)
            self.assertEqual(resp["result"]["status"], "ok")
            self.assertEqual(resp["result"]["format"], "png")
            self.assertGreater(len(resp["result"]["data"]), 100)

            # 5. CAPTURE_IMAGE (to file)
            out_png = os.path.join(self.temp_dir, "capture_out.png")
            resp = self._send_cmd(client, {
                "jsonrpc": "2.0",
                "method": "CAPTURE_IMAGE",
                "params": {"path": out_png},
                "id": 5,
            })
            self.assertEqual(resp["id"], 5)
            self.assertEqual(resp["result"]["status"], "ok")
            self.assertTrue(os.path.exists(out_png))
            self.assertGreater(os.path.getsize(out_png), 500)

            # 6. Unknown method error handling
            resp = self._send_cmd(client, {"jsonrpc": "2.0", "method": "UNKNOWN_ACTION", "id": 6})
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32601)

        finally:
            client.close()

    def test_server_subscribe(self):
        for ch in "Stream Test":
            self.state.write_char(ch)

        self.server.start()

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.socket_path)

        try:
            # Subscribe at 20 FPS
            client.sendall((json.dumps({
                "jsonrpc": "2.0",
                "method": "SUBSCRIBE",
                "params": {"fps": 20},
                "id": 10,
            }) + "\n").encode("utf-8"))

            client_file = client.makefile("r", encoding="utf-8")
            init_line = client_file.readline()
            init_msg = json.loads(init_line)
            self.assertEqual(init_msg.get("id"), 10)
            self.assertEqual(init_msg["result"]["status"], "subscribed")

            # Read next streamed snapshot
            stream_line = client_file.readline()
            stream_msg = json.loads(stream_line)
            self.assertEqual(stream_msg["method"], "screen_snapshot")
            self.assertIn("Stream Test", stream_msg["params"]["text"])
        finally:
            client.close()

    def test_server_step_and_frame_updates(self):
        self.server.start()
        self.server.update_step(1, 4, "type", "Running unit test")
        self.assertEqual(self.server.metadata.current_step_index, 1)
        self.assertEqual(self.server.metadata.current_step_type, "type")

        self.server.update_rendered_frame(150, 5.0)
        self.assertEqual(self.server.metadata.rendered_frames, 150)
        self.assertEqual(self.server.metadata.elapsed_seconds, 5.0)


class TestScenarioRunnerTelemetryIntegration(unittest.TestCase):
    """Verify TelemetrySubsystem integration into ScenarioRunner lifecycle."""

    def test_runner_telemetry_lifecycle(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            out_mp4 = f.name

        manifest_dict = {
            "version": "1.0",
            "metadata": {
                "title": "Telemetry Integration Test",
                "output": out_mp4,
                "resolution": [640, 360],
                "fps": 15,
                "theme": "catppuccin-mocha",
            },
            "environment": {
                "create_temp_workspace": True,
            },
            "timeline": [
                {"show_card": {"tag": "Init", "title": "Starting Telemetry Test", "duration": 0.2}},
                {"launch": {"command": "bash"}},
                {"type": {"text": "echo 999", "speed": 0.01, "send_key": "Enter"}},
                {"pause": {"seconds": 0.3}},
            ],
        }

        manifest = ScenarioManifest.from_dict(manifest_dict)
        runner = ScenarioRunner(manifest=manifest, verbose=False)

        # Verify telemetry metadata was initialized
        self.assertIsNotNone(runner.telemetry_metadata)
        self.assertEqual(runner.telemetry_metadata.scenario_title, "Telemetry Integration Test")
        self.assertEqual(runner.telemetry_metadata.total_steps, 4)
        self.assertEqual(runner.telemetry_metadata.fps, 15)

        # Run scenario
        report = runner.run()

        # Verify execution and report
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.session_id, runner.session_id)
        self.assertIsNotNone(report.session_id)

        # Verify telemetry server was cleanly stopped
        if runner.telemetry_server:
            self.assertFalse(runner.telemetry_server._running)
            self.assertFalse(os.path.exists(runner.telemetry_server.socket_path))

        # Verify session status in registry was unregistered (status="completed")
        sess_record = runner.telemetry_registry.get_session(runner.session_id)
        self.assertIsNotNone(sess_record)
        self.assertEqual(sess_record.status, "completed")

        if os.path.exists(out_mp4):
            os.unlink(out_mp4)


if __name__ == "__main__":
    unittest.main()
