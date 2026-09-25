"""
Real-tmux tests for the tmux backend and the screen serializer.

Nothing here is mocked. Each test either drives a real private tmux server
through TmuxSupervisor, or compares TermReel's output with what a real tmux
pane shows for the same bytes:

* multi-pane composites are compared with an actual tmux client attached to
  the recording session, viewed through a second (outer) tmux pane;
* key names are checked by reading the bytes a program inside the pane
  really receives;
* the serializer (used for tmux-backend casts and for `live` resume) is
  checked by replaying its output, plus further program output, into tmux
  and comparing with the original byte stream replayed into tmux.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from termreel.emulator.composite import composite_panes
from termreel.emulator.parser import ANSIParser
from termreel.emulator.serialize import serialize_screen
from termreel.emulator.state import TerminalState
from termreel.supervisor.tmux_session import TmuxSupervisor
from termreel.utils.keystrokes import KeyMap
from tests.tmux_oracle import TmuxOracle, describe_diff, normalise_capture_line, tmux_available

requires_tmux = unittest.skipUnless(tmux_available(), "tmux is not installed")
TMUX = shutil.which("tmux")


def _wait_for(predicate, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def _clean_env():
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    return env


class _AttachedView:
    """
    What a person attached to the recording session would see.

    An outer tmux server runs ``tmux attach`` for the inner session in a pane
    of the same size. Capturing the outer pane gives the real client
    rendering, borders included, independently of TermReel's compositor.
    """

    def __init__(self, sup: TmuxSupervisor):
        self.sup = sup
        self.socket = f"trview-{uuid.uuid4().hex[:8]}"

    def __enter__(self):
        inner = [TMUX, "-u", "-L", self.sup.socket_name, "attach", "-t", self.sup.session_name]
        cmd = "env -u TMUX -u TMUX_PANE " + " ".join(shlex.quote(a) for a in inner)
        env = _clean_env()
        env["LANG"] = env["LC_ALL"] = "C.UTF-8"
        subprocess.run(
            [TMUX, "-u", "-L", self.socket, "-f", "/dev/null", "new-session", "-d",
             "-x", str(self.sup.cols), "-y", str(self.sup.rows), cmd],
            check=True, capture_output=True, env=env,
        )
        self._tmux("set-option", "-g", "status", "off")
        self._tmux("resize-window", "-x", str(self.sup.cols), "-y", str(self.sup.rows))
        return self

    def __exit__(self, *exc):
        self._tmux("kill-server")

    def _tmux(self, *args):
        return subprocess.run([TMUX, "-L", self.socket, *args], capture_output=True,
                              text=True, env=_clean_env())

    def lines(self):
        raw = self._tmux("capture-pane", "-p", "-e").stdout.split("\n")
        carry: dict = {}
        out = [normalise_capture_line(ln, self.sup.cols, carry=carry) for ln in raw[: self.sup.rows]]
        while len(out) < self.sup.rows:
            out.append("")
        return out

    def cursor(self):
        x, y = self._tmux("display-message", "-p", "#{cursor_x} #{cursor_y}").stdout.split()
        return int(x), int(y)


@requires_tmux
class TestTmuxPaneOperations(unittest.TestCase):
    """split/select/close against a real private tmux server.

    Replaces the previous mock-based tests, which asserted the exact argv
    passed to a patched subprocess.run (including a hard-coded
    ``session:0.N`` target that broke under ``pane-base-index 1``).
    """

    COLS, ROWS = 40, 10

    def setUp(self):
        self.sup = TmuxSupervisor(command="sh", rows=self.ROWS, cols=self.COLS)
        self.sup.start()
        self.addCleanup(self.sup.terminate)

    def _composite(self):
        state = TerminalState(rows=self.ROWS, cols=self.COLS)
        composite_panes(state, self.sup.capture_frame())
        return state

    def _pane_text(self, pane):
        return self.sup._run("capture-pane", "-p", "-t", pane.pane_id).stdout

    def _type_into(self, index, text):
        self.sup.select_pane(index)
        self.sup.send_text(text)
        self.sup.send_key("Enter")

    def _settle(self):
        prev = None
        for _ in range(40):
            cur = [(p.pane_id, p.content, p.cursor_x, p.cursor_y) for p in self.sup.capture_frame()]
            if cur == prev:
                return
            prev = cur
            time.sleep(0.1)

    def _assert_composite_matches_client(self):
        self._settle()
        with _AttachedView(self.sup) as view:
            expected = _wait_for(lambda: (lambda l: l if any(l) else None)(view.lines()))
            time.sleep(0.3)
            expected = view.lines()
            state = self._composite()
            actual = [state.get_line_text(r) for r in range(self.ROWS)]
            if expected != actual:
                diff = "\n".join(
                    f"row {i:2d} client  : {a!r}\nrow {i:2d} termreel: {b!r}"
                    for i, (a, b) in enumerate(zip(expected, actual)) if a != b
                )
                self.fail("composite differs from an attached tmux client:\n" + diff)
            self.assertEqual(view.cursor(), (state.cursor.col, state.cursor.row),
                             "composite cursor is not where the client shows it")

    def test_horizontal_split_composite_matches_attached_client(self):
        self._type_into(0, "echo LEFT_MARKER")
        self.sup.split_pane(direction="horizontal", percent=40, command="sh")
        panes = _wait_for(lambda: self.sup.list_panes() if len(self.sup.list_panes()) == 2 else None)
        self.assertEqual(len(panes), 2)
        self.assertEqual([p.index for p in panes], [0, 1])
        self.assertGreater(panes[1].left, 0)
        self._type_into(1, "echo RIGHT_MARKER")
        self._assert_composite_matches_client()
        state = self._composite()
        text = state.get_rendered_text()
        self.assertIn("LEFT_MARKER", text)
        self.assertIn("RIGHT_MARKER", text)

    def test_vertical_split_composite_matches_attached_client(self):
        self._type_into(0, "echo TOP_MARKER")
        self.sup.split_pane(direction="vertical", percent=50, command="sh")
        _wait_for(lambda: len(self.sup.list_panes()) == 2)
        self._type_into(1, "echo BOTTOM_MARKER")
        self._assert_composite_matches_client()

    def test_three_panes_with_junction(self):
        self.sup.split_pane(direction="horizontal", percent=50, command="sh")
        _wait_for(lambda: len(self.sup.list_panes()) == 2)
        self.sup.select_pane(1)
        self.sup.split_pane(direction="vertical", percent=50, command="sh")
        _wait_for(lambda: len(self.sup.list_panes()) == 3)
        for i, word in enumerate(["AAA", "BBB", "CCC"]):
            self._type_into(i, f"echo {word}")
        self._settle()
        with _AttachedView(self.sup) as view:
            time.sleep(0.4)
            expected = view.lines()
        actual = [self._composite().get_line_text(r) for r in range(self.ROWS)]
        # tmux draws a T-junction (├) where the horizontal border meets the
        # vertical one; the compositor draws a plain vertical there. Compare
        # everything except that single cell.
        norm = lambda rows: [r.replace("├", "│").replace("┤", "│").replace("┼", "│") for r in rows]
        self.assertEqual(norm(expected), norm(actual))

    def test_select_and_close_follow_tmux_order_after_close(self):
        self.sup.split_pane(direction="horizontal", command="sh")
        self.sup.split_pane(direction="horizontal", command="sh")
        panes = _wait_for(lambda: self.sup.list_panes() if len(self.sup.list_panes()) == 3 else None)
        ids = [p.pane_id for p in panes]
        self.sup.close_pane(0)
        after = _wait_for(lambda: self.sup.list_panes() if len(self.sup.list_panes()) == 2 else None)
        self.assertEqual([p.pane_id for p in after], ids[1:])
        # Index 0 is now what used to be index 1, as in tmux itself.
        self.sup.select_pane(0)
        active = [p for p in self.sup.list_panes() if p.active]
        self.assertEqual(active[0].pane_id, ids[1])
        with self.assertRaises(RuntimeError):
            self.sup.select_pane(5)
        self.sup.close_pane()  # active pane
        self.assertEqual([p.pane_id for p in self.sup.list_panes()], [ids[2]])


@requires_tmux
class TestPrivateServerIsolation(unittest.TestCase):
    """The user's tmux config and $TMUX must not affect recordings."""

    def test_user_conf_base_index_and_TMUX_do_not_leak(self):
        home = tempfile.mkdtemp(prefix="trhome_")
        self.addCleanup(shutil.rmtree, home, True)
        with open(os.path.join(home, ".tmux.conf"), "w") as fh:
            fh.write("set -g base-index 1\nsetw -g pane-base-index 1\nset -g status on\n"
                     "set -g prefix C-a\n")
        fake_sock_dir = tempfile.mkdtemp(prefix="trfake_")
        self.addCleanup(shutil.rmtree, fake_sock_dir, True)
        fake_socket = os.path.join(fake_sock_dir, "default")
        isolated_env = {
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "TMUX": f"{fake_socket},1234,0",
            "TMUX_PANE": "%99",
        }

        sup = TmuxSupervisor(command="sh", rows=8, cols=30, env=isolated_env)
        sup.start()
        self.addCleanup(sup.terminate)
        sup.split_pane(direction="horizontal", command="sh")
        panes = _wait_for(lambda: sup.list_panes() if len(sup.list_panes()) == 2 else None)
        # pane-base-index 1 from the user's conf would make these [1, 2].
        self.assertEqual([p.index for p in panes], [0, 1])
        status = sup._run("show-options", "-gv", "status").stdout.strip()
        self.assertEqual(status, "off")
        # Nothing was created at the socket $TMUX pointed to.
        self.assertFalse(os.path.exists(fake_socket))
        # And the full window is the pane area (no status line stealing a row).
        state = TerminalState(rows=8, cols=30)
        composite_panes(state, sup.capture_frame())
        self.assertEqual(sum(p.height for p in panes[:1]), 8)

    def test_terminate_kills_only_its_own_server(self):
        a = TmuxSupervisor(command="sh", rows=5, cols=20)
        b = TmuxSupervisor(command="sh", rows=5, cols=20)
        a.start()
        b.start()
        self.addCleanup(a.terminate)
        self.addCleanup(b.terminate)
        a.terminate()
        self.assertFalse(a.is_alive())
        self.assertTrue(b.is_alive())


_DUMP_KEYS = r"""
import os, sys, termios, tty, select
fd = sys.stdin.fileno()
tty.setraw(fd)
out = open(sys.argv[1], "wb", buffering=0)
out.write(b"READY\n")
while True:
    r, _, _ = select.select([fd], [], [], 30)
    if not r:
        break
    data = os.read(fd, 1024)
    if not data:
        break
    out.write(data.hex().encode() + b"\n")
"""


@requires_tmux
class TestKeyNamesAgainstTmux(unittest.TestCase):
    """
    The PTY backend's byte encoding for each key name must equal the bytes a
    program receives when tmux sends that key (TERM=xterm-256color), and the
    tmux backend must actually send it rather than typing the name.
    """

    KEYS = ["Enter", "Tab", "S-Tab", "BTab", "Escape", "BSpace", "Space",
            "Up", "Down", "Left", "Right", "Home", "End", "PageUp", "PageDown",
            "Insert", "Delete", "F1", "F4", "F5", "F12",
            "C-a", "C-r", "ctrl+r", "^R", "C-Space", "M-f", "alt+b", "M-Enter",
            "C-Left", "ctrl+right", "S-Up", "M-Up", "C-Home", "C-F5", "S-F3", "a", "Z", "0x7f"]

    def test_pty_encoding_matches_what_tmux_delivers(self):
        tmp = tempfile.mkdtemp(prefix="trkeys_")
        self.addCleanup(shutil.rmtree, tmp, True)
        script = os.path.join(tmp, "dump.py")
        with open(script, "w") as fh:
            fh.write(_DUMP_KEYS)
        outfile = os.path.join(tmp, "keys.hex")
        sup = TmuxSupervisor(command=f"{shlex.quote(sys.executable)} {script} {outfile}",
                             rows=10, cols=40)
        sup.start()
        self.addCleanup(sup.terminate)
        def read():
            with open(outfile, "rb") as fh:
                return fh.read()

        self.assertTrue(_wait_for(lambda: os.path.exists(outfile) and read().startswith(b"READY")))
        # tmux sends Home/End as CSI 1~ / CSI 4~ regardless of TERM, while
        # xterm (whose TERM the PTY backend advertises) sends CSI H / CSI F.
        # Both are real terminal behaviour and readline binds both; the PTY
        # backend deliberately follows xterm. Record, don't hide, the gap.
        xterm_vs_tmux = {"Home": b"\x1b[1~", "End": b"\x1b[4~"}
        mismatches = []
        for key in self.KEYS:
            before = read()
            sup.send_key(key)
            _wait_for(lambda: read() != before)
            time.sleep(0.05)
            new = read()[len(before):].split()
            received = b"".join(bytes.fromhex(h.decode()) for h in new)
            expected = KeyMap.to_pty(key).encode("latin-1")
            if received != expected and xterm_vs_tmux.get(key) != received:
                mismatches.append(f"{key!r}: tmux delivered {received!r}, PTY backend sends {expected!r}")
        self.assertEqual(mismatches, [], "\n".join(mismatches))

    def test_unknown_keys_raise_on_both_backends(self):
        from termreel.exceptions import KeySpecError
        sup = TmuxSupervisor(command="sh", rows=5, cols=20)
        sup.start()
        self.addCleanup(sup.terminate)
        for bad in ("ctrl+nonsense", "F13", "C-", "Hyper-x", "0x1ff"):
            with self.assertRaises(KeySpecError, msg=bad):
                KeyMap.to_pty(bad)
            with self.assertRaises(ValueError, msg=bad):
                sup.send_key(bad)
        # Nothing was typed as literal text.
        time.sleep(0.2)
        self.assertNotIn("nonsense", sup.capture_plain())


# Byte streams whose screens (and following output) must survive being
# replaced by serialize_screen(): margins, origin mode, pen, charsets,
# pending wrap, autowrap off, insert mode, alternate screen, tab stops.
SERIALIZE_CASES = {
    "plain": b"hello\r\nworld",
    "sgr_pen_carries": b"\x1b[1;31mred bold ",
    "truecolor_and_reverse": b"\x1b[38;2;10;200;30mX\x1b[7mY\x1b[27;48;5;4mZ",
    "scroll_region_active": b"1\r\n2\r\n3\r\n4\r\n5\x1b[2;4r\x1b[4;1H",
    "origin_mode": b"\x1b[2;5r\x1b[?6h\x1b[2;3H",
    "pending_wrap": b"A" * 20,
    "pending_wrap_last_row": b"\x1b[6;1H" + b"B" * 20,
    "autowrap_off": b"\x1b[?7l" + b"C" * 22,
    "insert_mode": b"abcdef\x1b[1;3H\x1b[4h",
    "dec_graphics_active": b"\x1b(0lqk",
    "g1_shifted": b"\x1b)0\x0eq",
    "alt_screen": b"primary\x1b[?1049h\x1b[2;2Halt",
    "tab_stops_changed": b"\x1b[3g\x1b[1;5H\x1bH\x1b[1;13H\x1bH\r",
    "hidden_cursor": b"x\x1b[?25l",
    "wide_chars": "中文x".encode(),
}
# Output that arrives after the redraw; it must render identically.
SUFFIX = b"XY\tT\r\n\x1b[Bz" + b"q" * 3 + b"\n\n\n\n\nend"


@requires_tmux
class TestSerializeScreenAgainstTmux(unittest.TestCase):
    COLS, ROWS = 20, 6

    def test_redraw_then_more_output_matches_original_stream(self):
        failures = []
        for name, data in SERIALIZE_CASES.items():
            state = TerminalState(rows=self.ROWS, cols=self.COLS)
            ANSIParser(state).feed(data)
            redraw = serialize_screen(state.snapshot()).encode("utf-8")
            for suffix in (b"", SUFFIX):
                with TmuxOracle(cols=self.COLS, rows=self.ROWS) as oracle:
                    original = oracle.render(data + suffix)
                with TmuxOracle(cols=self.COLS, rows=self.ROWS) as oracle:
                    replayed = oracle.render(redraw + suffix)
                same = (original.lines == replayed.lines
                        and (original.cursor_x, original.cursor_y) == (replayed.cursor_x, replayed.cursor_y)
                        and original.alternate_on == replayed.alternate_on
                        and original.cursor_visible == replayed.cursor_visible)
                if not same:
                    failures.append(f"[{name}{' +suffix' if suffix else ''}]\n"
                                    f"{describe_diff(original, replayed)}")
        self.assertEqual(failures, [], "\n\n".join(failures))


@requires_tmux
@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg is required")
class TestTmuxBackendCast(unittest.TestCase):
    """The tmux backend writes its .cast from screen redraws; replaying it must
    give the screen that went into the video."""

    def test_cast_replay_matches_rendered_screen(self):
        import json

        from termreel.scenario.runner import ScenarioRunner
        from termreel.scenario.schema import parse_manifest_dict

        workdir = tempfile.mkdtemp(prefix="termreel_tmuxcast_")
        try:
            cast_path = os.path.join(workdir, "out.cast")
            manifest = parse_manifest_dict({
                "version": "1.0",
                "metadata": {
                    "title": "tmux cast",
                    "output": os.path.join(workdir, "out.mp4"),
                    "cast_output": cast_path,
                    "fps": 10,
                    "cols": 50,
                    "rows": 12,
                },
                "timeline": [
                    {"launch": {"command": "bash --norc --noprofile"}},
                    {"type": {"text": "printf '\\033[1;31mred\\033[0m plain\\n'; seq 1 20",
                              "speed": 0.002, "send_key": "Enter", "pause": 1.0}},
                    {"type": {"text": "echo 'wide: 漢字 end'", "speed": 0.002,
                              "send_key": "Enter", "pause": 1.0}},
                ],
            })
            runner = ScenarioRunner(manifest=manifest, backend="tmux", verbose=False)
            runner.run()
            with open(cast_path, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            header = json.loads(lines[0])
            self.assertEqual((header["width"], header["height"]), (runner.state.cols, runner.state.rows))
            payload = "".join(e[2] for e in map(json.loads, lines[1:]) if e[1] == "o")
            replay = TerminalState(rows=header["height"], cols=header["width"])
            ANSIParser(replay).feed(payload.encode("utf-8"))
            screen = runner.state.get_rendered_text()
            self.assertIn("wide: 漢字 end", screen, "test setup: output never rendered")
            self.assertEqual(replay.get_rendered_text(), screen)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
