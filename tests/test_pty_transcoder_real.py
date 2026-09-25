"""
PTY input/query behaviour and the FFmpeg/renderer output path, against real
programs, a real tmux and a real ffmpeg/ffprobe.

Where a real terminal defines the expected behaviour (query replies, paste
bytes), the PTY backend is compared with what tmux delivers to the same
child instead of with hard-coded assumptions.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from termreel.emulator.state import TerminalState
from termreel.exceptions import TranscoderError
from termreel.renderer.cairo_renderer import (
    CairoTerminalRenderer,
    grid_for_pixels,
    pixels_for_grid,
)
from termreel.supervisor.pty_session import PtySupervisor
from termreel.supervisor.tmux_session import TmuxSupervisor
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe

HAVE_TMUX = shutil.which("tmux") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

# Raw-mode stdin recorder: optionally enables bracketed paste and/or sends a
# terminal query, then records every byte it receives until input is idle.
DUMPER = r"""
import os, sys, tty, termios, select, time
out, bracket, query = sys.argv[1], sys.argv[2] == "1", sys.argv[3].encode().decode("unicode_escape").encode("latin-1")
fd = 0
old = termios.tcgetattr(fd)
tty.setraw(fd)
if bracket:
    os.write(1, b"\x1b[?2004h")
os.write(1, b"READY\r\n")
if query:
    os.write(1, query)
buf = b""
deadline = time.time() + 6
last = None
while time.time() < deadline:
    r, _, _ = select.select([fd], [], [], 0.05)
    if r:
        buf += os.read(fd, 65536)
        last = time.time()
    elif last is not None and time.time() - last > 0.6:
        break
    elif last is None and query and time.time() > deadline - 4:
        break
termios.tcsetattr(fd, termios.TCSADRAIN, old)
with open(out, "wb") as fh:
    fh.write(buf)
os.write(1, b"\r\nDUMP_DONE\r\n")
time.sleep(60)
"""


class _Dumper:
    def __init__(self, backend, bracket=False, query="", respond_to_queries=True):
        self.dir = tempfile.mkdtemp(prefix=f"termreel_dump_{backend}_")
        script = os.path.join(self.dir, "dump.py")
        with open(script, "w") as fh:
            fh.write(DUMPER)
        self.out = os.path.join(self.dir, "out.bin")
        cmd = f"{sys.executable} {script} {self.out} {'1' if bracket else '0'} '{query}'"
        if backend == "pty":
            self.sup = PtySupervisor(command=cmd, rows=10, cols=60, cwd=self.dir,
                                     respond_to_queries=respond_to_queries)
        else:
            self.sup = TmuxSupervisor(command=cmd, rows=10, cols=60, cwd=self.dir)
        self.sup.start()

    def wait(self, text, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in self.sup.capture_plain():
                return True
            time.sleep(0.05)
        raise AssertionError(f"never saw {text!r}:\n{self.sup.capture_plain()}")

    def result(self):
        self.wait("DUMP_DONE")
        with open(self.out, "rb") as fh:
            return fh.read()

    def close(self):
        try:
            self.sup.terminate()
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


def _dump(backend, action=None, **kw):
    d = _Dumper(backend, **kw)
    try:
        d.wait("READY")
        if action:
            action(d.sup)
        return d.result()
    finally:
        d.close()


class TestTerminalQueries(unittest.TestCase):

    @unittest.skipUnless(HAVE_TMUX, "tmux is required")
    def test_cursor_position_report_matches_tmux(self):
        # Move to row 5 col 7, then ask where the cursor is.
        q = r"\x1b[5;7H\x1b[6n"
        pty_reply = _dump("pty", query=q)
        tmux_reply = _dump("tmux", query=q)
        self.assertEqual(tmux_reply, b"\x1b[5;7R", "test setup: tmux reply")
        self.assertEqual(pty_reply, tmux_reply)

    def test_device_attributes_are_answered(self):
        reply = _dump("pty", query=r"\x1b[c")
        self.assertTrue(reply.startswith(b"\x1b[?") and reply.endswith(b"c"), reply)

    def test_live_mode_supervisor_does_not_answer(self):
        # termreel live mirrors the query to the operator's terminal, which
        # answers; a second answer would be typed into the program.
        reply = _dump("pty", query=r"\x1b[6n", respond_to_queries=False)
        self.assertEqual(reply, b"")


class TestPaste(unittest.TestCase):
    TEXT = "first line\nsecond line\n"

    @unittest.skipUnless(HAVE_TMUX, "tmux is required")
    def test_paste_bytes_match_tmux_without_bracketed_mode(self):
        paste = lambda sup: sup.paste_text(self.TEXT)  # noqa: E731
        pty_bytes = _dump("pty", paste)
        tmux_bytes = _dump("tmux", paste)
        self.assertNotIn(b"\x1b[200~", tmux_bytes, "test setup")
        self.assertEqual(pty_bytes, tmux_bytes)

    @unittest.skipUnless(HAVE_TMUX, "tmux is required")
    def test_paste_bytes_match_tmux_with_bracketed_mode(self):
        paste = lambda sup: sup.paste_text(self.TEXT)  # noqa: E731
        pty_bytes = _dump("pty", paste, bracket=True)
        tmux_bytes = _dump("tmux", paste, bracket=True)
        self.assertTrue(tmux_bytes.startswith(b"\x1b[200~"), f"test setup: {tmux_bytes!r}")
        self.assertEqual(pty_bytes, tmux_bytes)

    def test_slow_large_paste_is_delivered_intact(self):
        # ~150 KB: far more than the PTY input buffer, so writes return
        # short / EAGAIN and must be retried until the reader drains them.
        lines = [f"{i:06d} " + ("abcdefghij" * 7) for i in range(2000)]
        text = "\n".join(lines) + "\n"
        self.assertGreater(len(text), 64 * 1024)
        tmp = tempfile.mkdtemp(prefix="termreel_paste_")
        out = os.path.join(tmp, "big.txt")
        sup = PtySupervisor(command=f"bash --norc --noprofile -c 'stty -echo; cat > {out}; echo PASTE_DONE; sleep 60'",
                            rows=10, cols=80, cwd=tmp)
        sup.start()
        try:
            time.sleep(0.3)
            sup.paste_text(text)
            sup.send_raw(b"\x04")  # EOF at the start of a line ends cat
            self.assertTrue(sup.wait_for_output("PASTE_DONE", timeout=20),
                            sup.capture_plain())
            with open(out) as fh:
                got = fh.read()
            self.assertEqual(len(got), len(text))
            self.assertEqual(got, text)
        finally:
            sup.terminate()
            shutil.rmtree(tmp, ignore_errors=True)


def _probe(path):
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(res.stdout)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(num) / float(den),
        "frames": int(stream.get("nb_read_frames", 0)),
        "duration": float(data.get("format", {}).get("duration", 0.0)),
    }


def _frames(width, height, n):
    """n distinct BGRA frames (a moving bar) so encoders cannot collapse them."""
    for i in range(n):
        row = bytearray(b"\x20\x20\x20\xff" * width)
        x = (i * 7) % width
        row[x * 4:(x + 4) * 4] = b"\xff\xff\xff\xff" * min(4, width - x)
        yield bytes(row) * height


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are required")
class TestTranscoder(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="termreel_ffmpeg_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_odd_frame_size_is_padded_even(self):
        out = os.path.join(self.tmp, "odd.mp4")
        pipe = FFmpegPipe(out, 827, 605, fps=10, preset="ultrafast")
        pipe.open()
        for f in _frames(827, 605, 12):
            pipe.write_frame(f)
        pipe.close()
        info = _probe(out)
        self.assertEqual((info["width"], info["height"]), (828, 606))
        self.assertFalse(os.path.exists(pipe.partial_file))

    def test_failed_encode_raises_and_leaves_no_file(self):
        out = os.path.join(self.tmp, "bad.mp4")
        pipe = FFmpegPipe(out, 160, 96, fps=10, preset="no-such-preset")
        pipe.open()
        try:
            for f in _frames(160, 96, 30):
                pipe.write_frame(f)
                time.sleep(0.01)
        except TranscoderError:
            pass  # ffmpeg may already have exited and closed the pipe
        with self.assertRaises(TranscoderError):
            pipe.close()
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(pipe.partial_file))

    def test_wide_gif_is_capped_at_960_and_15_fps(self):
        out = os.path.join(self.tmp, "wide.gif")
        pipe = FFmpegPipe(out, 1280, 200, fps=30)
        pipe.open()
        for f in _frames(1280, 200, 30):
            pipe.write_frame(f)
        pipe.close()
        info = _probe(out)
        self.assertEqual(info["width"], 960)
        self.assertLessEqual(info["fps"], 15.0)

    def test_slow_gif_longer_than_a_minute_encodes(self):
        # The old single-pass palettegen graph buffered every frame and timed
        # out on GIFs longer than about a minute.
        out = os.path.join(self.tmp, "long.gif")
        n = 1900  # 63 s at 30 fps
        pipe = FFmpegPipe(out, 160, 96, fps=30)
        pipe.open()
        for f in _frames(160, 96, n):
            pipe.write_frame(f)
        pipe.close()
        info = _probe(out)
        self.assertLessEqual(info["fps"], 15.0)
        self.assertGreater(info["duration"], 60.0)
        self.assertEqual((info["width"], info["height"]), (160, 96))
        self.assertFalse(os.path.exists(pipe.partial_file))


class TestGridSizing(unittest.TestCase):

    def test_pixels_for_grid_round_trips_and_is_even(self):
        grids = [(81, 24), (80, 24), (100, 30), (132, 43), (40, 10), (201, 57)]
        for font_size in range(8, 25):
            for cols, rows in grids:
                w, h = pixels_for_grid(cols, rows, font_size=font_size)
                self.assertEqual((w % 2, h % 2), (0, 0), (cols, rows, font_size, w, h))
                self.assertEqual(grid_for_pixels(w, h, font_size=font_size), (cols, rows),
                                 (cols, rows, font_size, w, h))

    def test_renderer_built_from_pixels_for_grid_has_that_grid(self):
        w, h = pixels_for_grid(81, 24)
        r = CairoTerminalRenderer(width=w, height=h)
        self.assertEqual((r.cols, r.rows), (81, 24))
        frame = r.draw_frame(TerminalState(rows=24, cols=81))
        self.assertEqual(len(frame), w * h * 4)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc to inspect processes")
class TestTerminateReapsSession(unittest.TestCase):

    def test_background_jobs_die_with_the_session_but_setsid_daemons_do_not(self):
        tmp = tempfile.mkdtemp(prefix="termreel_reap_")
        sup = PtySupervisor(command="bash --norc --noprofile -i", rows=10, cols=80, cwd=tmp)
        sup.start()
        daemon_pid = None
        try:
            time.sleep(0.5)
            sup.send_text(f"sleep 600 & echo $! > {tmp}/job.pid\r")
            sup.send_text(f"(setsid sleep 601 & echo $! > {tmp}/daemon.pid)\r")
            deadline = time.time() + 5
            while time.time() < deadline and not (
                os.path.exists(f"{tmp}/job.pid") and os.path.exists(f"{tmp}/daemon.pid")
            ):
                time.sleep(0.05)
            time.sleep(0.3)
            with open(f"{tmp}/job.pid") as fh:
                job_pid = int(fh.read())
            with open(f"{tmp}/daemon.pid") as fh:
                daemon_pid = int(fh.read())
            self.assertTrue(_alive(job_pid) and _alive(daemon_pid), "test setup")
            # Under job control the job has its own process group, which is
            # exactly why killpg on the shell's group used to miss it.
            self.assertNotEqual(os.getpgid(job_pid), os.getpgid(sup.process.pid))

            sup.terminate()
            deadline = time.time() + 3
            while time.time() < deadline and _alive(job_pid):
                time.sleep(0.05)
            self.assertFalse(_alive(job_pid), "background job outlived the recording")
            self.assertTrue(_alive(daemon_pid), "a process outside the session was killed")
        finally:
            sup.terminate()
            if daemon_pid:
                try:
                    os.kill(daemon_pid, 9)
                except OSError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
