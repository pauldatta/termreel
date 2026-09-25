"""
Masking, cast redaction and telemetry privacy against real programs.

Everything here runs a real child in a real PTY (or a real UNIX socket and
real files) and checks what actually ends up in the video grid, the .cast
file, the telemetry socket and the fallback files.
"""

import json
import os
import re
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest

from termreel.emulator.parser import ANSIParser
from termreel.emulator.state import TerminalState
from termreel.exceptions import MaskConfigError, ScenarioValidationError
from termreel.mask.engine import MaskEngine
from termreel.scenario.schema import parse_manifest_dict
from termreel.supervisor.pty_session import PtySupervisor
from termreel.telemetry.registry import SessionRegistry, _ensure_private_dir
from termreel.telemetry.server import TelemetryServer
from termreel.utils.asciicast import AsciicastRecorder, AsciicastPlayer

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Za-z]|\x1b[78=>]")


def _run_child(script: str, rows: int = 8, cols: int = 40, wait_for: str = "END_OF_OUTPUT",
               on_output=None, timeout: float = 10.0) -> PtySupervisor:
    sup = PtySupervisor(command=f"bash --norc --noprofile -c {_sh_quote(script)}",
                        rows=rows, cols=cols, on_output=on_output)
    sup.start()
    if not sup.wait_for_output(wait_for, timeout=timeout):
        text = sup.capture_plain()
        sup.terminate()
        raise AssertionError(f"child never printed {wait_for!r}; screen:\n{text}")
    return sup


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _row_texts(state: TerminalState):
    return [state.get_line_text(r) for r in range(state.rows)]


class TestGridMasking(unittest.TestCase):

    def test_value_mask_does_not_grow_across_frames(self):
        # Real bug: masking the live grid in place re-matched "paul" inside
        # its own replacement every frame: paul_demo -> paul_demo_demo -> ...
        sup = _run_child("printf 'user=paul\\n'; echo END_OF_OUTPUT; sleep 30")
        try:
            engine = MaskEngine(values={"paul": "paul_demo"})
            frames = [engine.redacted_snapshot(sup.state) for _ in range(5)]
            for snap in frames:
                self.assertIn("user=paul_demo", _row_texts(snap))
                self.assertNotIn("paul_demo_demo", snap.get_rendered_text())
            # The live grid (what the next frame is masked from) stays real.
            self.assertIn("user=paul", _row_texts(sup.state))
            self.assertNotIn("paul_demo", sup.state.get_rendered_text())
        finally:
            sup.terminate()

    def test_secret_wrapped_across_rows_is_masked_on_every_row(self):
        secret = "ghp_" + "Q7x9Lm2Pz4Kd8Rt6Vb3Nw5Yc1Hs0Jf7Ga2E"
        self.assertGreater(len(secret), 30)
        # 20 columns: the terminal itself wraps the token over two rows.
        sup = _run_child(f"printf 'tok {secret}\\n'; echo END_OF_OUTPUT; sleep 30", cols=20)
        try:
            live_rows = _row_texts(sup.state)
            self.assertTrue(any(secret[4:14] in r for r in live_rows),
                            f"test setup: token not on screen: {live_rows}")
            snap = MaskEngine().redacted_snapshot(sup.state)
            rows = _row_texts(snap)
            for row in rows:
                for i in range(len(secret) - 5):
                    self.assertNotIn(secret[i:i + 6], row,
                                     f"secret fragment {secret[i:i+6]!r} survived masking: {rows}")
        finally:
            sup.terminate()

    def test_invalid_mask_regex_fails_closed(self):
        with self.assertRaises(MaskConfigError):
            MaskEngine(patterns=["token=(unclosed"])
        with self.assertRaises(MaskConfigError):
            MaskEngine.create(load_global=False, mask={"anchors": [{"after": "k=", "match": "[bad"}]})
        # And at validate time, before anything records.
        with self.assertRaises(ScenarioValidationError):
            parse_manifest_dict({
                "version": "1.0",
                "mask": {"patterns": ["(unclosed"]},
                "timeline": [{"pause": 0.1}],
            })

    def test_broken_global_config_fails_closed(self):
        tmp = tempfile.mkdtemp(prefix="termreel_maskcfg_")
        try:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w") as fh:
                fh.write("mask: [unterminated\n")
            with self.assertRaises(MaskConfigError):
                MaskEngine(load_global_config=True, global_config_path=path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_snapshot_is_isolated_from_later_output_in_both_buffers(self):
        # The per-frame snapshot must not change when the child keeps
        # writing, whether the visible buffer is the primary or the alt one.
        script = (
            "printf 'primary token=abc\\n'; echo PRIMARY_READY; sleep 1.0; "
            "printf '\\033[?1049h\\033[Halt screen user=paul'; echo; echo ALT_READY; sleep 1.0; "
            "printf '\\033[HOVERWRITTEN_ALT'; echo; echo END_OF_OUTPUT; sleep 30"
        )
        sup = _run_child(script, wait_for="PRIMARY_READY")
        try:
            engine = MaskEngine(values={"paul": "pd"}, use_default_patterns=False)
            snap_primary = engine.redacted_snapshot(sup.state)
            primary_before = _row_texts(snap_primary)
            self.assertTrue(sup.wait_for_output("ALT_READY", timeout=5))
            snap_alt = engine.redacted_snapshot(sup.state)
            alt_before = _row_texts(snap_alt)
            self.assertIn("alt screen user=pd", alt_before)
            self.assertTrue(sup.wait_for_output("END_OF_OUTPUT", timeout=5))
            self.assertEqual(_row_texts(snap_primary), primary_before)
            self.assertEqual(_row_texts(snap_alt), alt_before)
            self.assertIn("OVERWRITTEN_ALT", sup.state.get_rendered_text())
        finally:
            sup.terminate()

    def test_per_frame_masked_snapshot_fits_well_inside_a_1080p30_frame(self):
        # The renderer masks a snapshot of the grid every frame while holding
        # the lock the PTY reader needs. A full 1080p grid took ~30 ms per
        # snapshot (the whole 33 ms frame budget at 30 fps) before snapshots
        # skipped the hidden buffer and throwaway grids. Best-of-N timing so
        # a loaded machine does not make this flaky.
        from termreel.renderer.cairo_renderer import CairoTerminalRenderer

        renderer = CairoTerminalRenderer(width=1920, height=1080)
        cols, rows = renderer.cols, renderer.rows
        width = max(10, cols - 1)
        # A real program fills the grid with coloured text.
        script = (
            f"for i in $(seq {rows + 5}); do "
            f"printf '\\033[3%dm%s\\033[0m\\n' $((i % 7)) \"$(printf '%*s' {width} '' | tr ' ' x)\"; "
            "done; echo END_OF_OUTPUT; sleep 30"
        )
        sup = _run_child(script, rows=rows, cols=cols, timeout=15)
        try:
            engine = MaskEngine(values={"paul": "pd"})
            best = float("inf")
            for _ in range(12):
                t0 = time.perf_counter()
                engine.redacted_snapshot(sup.state)
                best = min(best, time.perf_counter() - t0)
            budget = 1.0 / 30 / 2
            self.assertLess(best, budget,
                            f"masked snapshot of a {cols}x{rows} grid took {best * 1000:.1f} ms; "
                            f"budget {budget * 1000:.1f} ms (half a 30 fps frame)")
        finally:
            sup.terminate()


class TestCastStreamRedaction(unittest.TestCase):

    def test_secret_split_across_reads_and_sgr_is_masked_in_cast(self):
        # The child prints one OpenAI-style key in four writes with pauses
        # and a colour change in the middle, so the PTY delivers it in
        # separate chunks and the raw bytes never contain the key
        # contiguously. Per-chunk redaction leaked all of it.
        parts = ["sk-", "AAAAAAAAAA", "BBBBBBBBBB", "CCCCC"]
        secret = "".join(parts)
        script = (
            "printf 'key='; sleep 0.05; printf 'sk-'; sleep 0.05; printf 'AAAAAAAAAA'; sleep 0.05; "
            "printf '\\033[31mBBBBBBBBBB\\033[0m'; sleep 0.05; printf 'CCCCC done\\n'; "
            "echo END_OF_OUTPUT; sleep 30"
        )
        tmp = tempfile.mkdtemp(prefix="termreel_cast_")
        cast_path = os.path.join(tmp, "out.cast")
        rec = AsciicastRecorder(cast_path, width=60, height=8, redactor=MaskEngine())
        rec.start()
        chunks = []

        def on_output(data: bytes):
            chunks.append(data)
            rec.record_output_bytes(data)

        stop = threading.Event()

        def ticker():  # like the runner's frame loop
            while not stop.is_set():
                rec.tick()
                time.sleep(1 / 30)

        t = threading.Thread(target=ticker, daemon=True)
        t.start()
        sup = _run_child(script, cols=60, on_output=on_output)
        try:
            self.assertGreater(len(chunks), 1, "test setup: output arrived in one chunk")
            raw = b"".join(chunks).decode()
            self.assertNotIn(secret, raw, "test setup: key must not be contiguous in raw bytes")
            self.assertIn(secret, sup.state.get_rendered_text(), "test setup: key on screen")
        finally:
            stop.set()
            t.join()
            sup.terminate()
            rec.close()
        try:
            events = [e for e in AsciicastPlayer(cast_path).iter_events() if e[1] == "o"]
            stream = "".join(e[2] for e in events)
            visible = ANSI_RE.sub("", stream)
            self.assertIn("done", visible)
            for part in parts[1:]:
                self.assertNotIn(part, visible, f"{part!r} leaked into the cast")
            # Replaying the cast shows a masked screen.
            state = TerminalState(rows=8, cols=60)
            parser = ANSIParser(state)
            parser.feed(stream.encode())
            self.assertNotIn(secret, state.get_rendered_text())
            self.assertIn("done", state.get_rendered_text())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTelemetryPrivacy(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="termreel_telem_")
        self.secret = "AKIA" + "ABCDEFGHIJKLMNOP"
        self.sup = _run_child(f"printf 'aws {self.secret}\\n'; echo END_OF_OUTPUT; sleep 30")

    def tearDown(self):
        self.sup.terminate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, session_dir):
        return TelemetryServer(
            session_id="privtest",
            state=self.sup.state,
            session_dir=session_dir,
            registry=SessionRegistry(os.path.join(self.tmp, "registry")),
            redactor=MaskEngine(),
        )

    def _rpc(self, path, method):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(path)
        try:
            s.sendall((json.dumps({"method": method, "id": 1}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return buf.decode()
        finally:
            s.close()

    def test_socket_and_fallback_files_are_masked_and_private(self):
        session_dir = os.path.join(self.tmp, "sess")
        server = self._server(session_dir)
        server.start()
        try:
            self.assertIn(self.secret, self.sup.state.get_rendered_text())
            for method in ("GET_SCREEN", "GET_RAW"):
                reply = self._rpc(server.socket_path, method)
                self.assertIn("aws ", reply)
                self.assertNotIn(self.secret, reply, f"{method} served the secret")
            self.assertEqual(stat.S_IMODE(os.stat(session_dir).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(server.socket_path).st_mode), 0o600)
            for name in ("screen.ansi", "status.json"):
                p = os.path.join(session_dir, name)
                self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, name)
            with open(os.path.join(session_dir, "screen.ansi")) as fh:
                screen = fh.read()
            self.assertIn("aws ", screen)
            self.assertNotIn(self.secret, screen)
        finally:
            server.stop()

    def test_long_session_dir_moves_only_the_socket(self):
        # AF_UNIX paths are limited (~108 bytes); the socket then goes to
        # /tmp, but the screen/status files must stay in the private dir.
        session_dir = os.path.join(self.tmp, "d" * 120)
        server = self._server(session_dir)
        server.start()
        try:
            self.assertTrue(server.socket_path.startswith("/tmp/"))
            self.assertTrue(os.path.exists(os.path.join(session_dir, "screen.ansi")))
            sock_dir = os.path.dirname(server.socket_path)
            self.assertNotEqual(os.path.abspath(sock_dir), os.path.abspath(session_dir))
            reply = self._rpc(server.socket_path, "GET_SCREEN")
            self.assertNotIn(self.secret, reply)
        finally:
            server.stop()
        self.assertFalse(os.path.exists(server.socket_path))


class TestRegistryDirectoryChecks(unittest.TestCase):

    def test_symlinked_fallback_dir_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="termreel_reg_")
        try:
            target = os.path.join(tmp, "target")
            os.mkdir(target, 0o700)
            link = os.path.join(tmp, "link")
            os.symlink(target, link)
            with self.assertRaises(PermissionError):
                _ensure_private_dir(link, strict=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipIf(os.getuid() == 0, "root owns everything")
    def test_directory_owned_by_someone_else_is_refused(self):
        # /tmp itself is owned by root: a real foreign-owned directory.
        with self.assertRaises(PermissionError):
            _ensure_private_dir("/tmp", strict=True)

    def test_own_world_readable_dir_is_tightened(self):
        tmp = tempfile.mkdtemp(prefix="termreel_reg_")
        try:
            os.chmod(tmp, 0o755)
            _ensure_private_dir(tmp, strict=True)
            self.assertEqual(stat.S_IMODE(os.stat(tmp).st_mode), 0o700)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
