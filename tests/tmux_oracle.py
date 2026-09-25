"""
tmux as a reference terminal for differential tests.

Feeds exactly the same bytes to a real tmux pane and to TermReel's
TerminalState/ANSIParser, then reads back what each one thinks is on screen.
Tests compare the two instead of comparing TermReel against expectations
written by hand, which is how the emulator previously passed its whole suite
while treating LF as CRLF and ignoring REP.

How the bytes reach tmux unmodified:

* Each oracle runs its own tmux server (``-L <unique> -f /dev/null``), so the
  developer's ~/.tmux.conf (base-index, status line, default-terminal) cannot
  change the result and no user session is touched.
* The pane runs ``stty -opost -echo`` before ``cat``-ing the byte file, so the
  line discipline does not turn LF into CRLF on the way to tmux.
* ``wait-for`` channels sequence "start writing" and "finished writing", and
  the capture is polled until it stops changing, because tmux reads the pane
  asynchronously from the wait-for signal.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

# Bound at import time on purpose: some legacy tests patch subprocess.run on
# the module object, and the test runner executes tests on threads. Holding
# our own reference keeps the oracle talking to the real tmux regardless.
_run = subprocess.run

TMUX = shutil.which("tmux")


def tmux_available() -> bool:
    return TMUX is not None


_CAPTURE_ESC = re.compile(r"\x1b\[[0-9;:?<=>]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def normalise_capture_line(line: str, cols: int, wrap: bool = False,
                           tab_stops: Optional[Sequence[int]] = None,
                           carry: Optional[dict] = None,
                           padded: bool = False) -> str:
    """
    Turn one line of ``capture-pane -p -e`` output into the text a viewer sees.

    tmux marks DEC special graphics cells with SO/SI instead of storing the
    glyph, and prints runs of never-written cells that a TAB skipped as a
    literal ``\\t``. Both are capture artefacts: the screen shows box glyphs
    and blanks. Tabs are expanded with the pane's real tab stops
    (``#{pane_tabs}``). SGR/OSC escapes from ``-e`` are dropped; colour is not
    part of the text comparison. C1 code points (U+0080..U+009F) have no
    glyph and are dropped too.

    tmux emits SO/SI only when the attribute changes between consecutive
    cells, including across a line break, so the graphics state must be
    carried from one captured row to the next; pass the same ``carry`` dict
    for every row of one capture.
    """
    from termreel.emulator.state import DEC_SPECIAL_GRAPHICS, char_width

    stops = sorted(tab_stops) if tab_stops is not None else list(range(8, cols, 8))
    line = _CAPTURE_ESC.sub("", line)
    # ``capture-pane -N`` pads each row to the full pane width, so when a row
    # holds exactly one TAB its real width is whatever the rest leaves over.
    # That stays correct even if tab stops changed after the TAB was printed,
    # which expansion from the current ``#{pane_tabs}`` cannot handle.
    exact_tab: Optional[int] = None
    if padded and not wrap and line.count("\t") == 1:
        visible = sum(max(char_width(c), 0) for c in line
                      if c not in "\t\x0e\x0f" and not ("\x80" <= c <= "\x9f"))
        if visible < cols:
            exact_tab = cols - visible
    out: List[str] = []
    col = 0
    graphics = bool(carry.get("graphics")) if carry is not None else False
    for ch in line:
        if ch == "\x0e":
            graphics = True
            continue
        if ch == "\x0f":
            graphics = False
            continue
        if "\x80" <= ch <= "\x9f":
            continue
        if ch == "\t":
            pos = col % cols if wrap else col
            target = next((s for s in stops if s > pos), cols - 1)
            target = min(target, cols - 1)
            pad = max(target - pos, 0) if exact_tab is None else exact_tab
            out.append(" " * pad)
            col += pad
            continue
        if graphics:
            ch = DEC_SPECIAL_GRAPHICS.get(ch, ch)
        out.append(ch)
        col += max(char_width(ch), 0)
    if carry is not None:
        carry["graphics"] = graphics
    return "".join(out).rstrip(" ")


@dataclass
class ScreenResult:
    """What a terminal reports after consuming a byte stream."""

    lines: List[str]
    cursor_x: int
    cursor_y: int
    alternate_on: bool = False
    joined: Optional[List[str]] = None  # logical lines (soft wraps joined)
    cursor_visible: Optional[bool] = None

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class TmuxOracle:
    """A private tmux server with one pane of a fixed size."""

    cols: int = 80
    rows: int = 24
    socket: str = field(default_factory=lambda: f"trtest-{uuid.uuid4().hex[:10]}")
    _tmpdir: Optional[str] = None
    _started: bool = False

    def _tmux(self, *args: str, check: bool = True, timeout: float = 10.0) -> subprocess.CompletedProcess:
        cmd = [TMUX, "-L", self.socket, "-f", "/dev/null", *args]
        res = _run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and res.returncode != 0:
            raise RuntimeError(f"tmux {' '.join(args)} failed: {res.stderr.strip()}")
        return res

    def close(self) -> None:
        if self._started:
            try:
                self._tmux("kill-server", check=False, timeout=5.0)
            except Exception:
                pass
            self._started = False
        if self._tmpdir and os.path.isdir(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def __enter__(self) -> "TmuxOracle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def render(self, data: bytes, settle: float = 0.15, timeout: float = 10.0) -> ScreenResult:
        """Write ``data`` into a fresh pane and return tmux's view of it."""
        if TMUX is None:
            raise RuntimeError("tmux is not installed")
        if self._started:
            self._tmux("kill-server", check=False)
            self._started = False
        # A fresh server per render. Reusing the socket right after
        # kill-server occasionally raced with the dying server.
        self.socket = f"trtest-{uuid.uuid4().hex[:10]}"
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="trtmux_")
        payload = os.path.join(self._tmpdir, f"in_{uuid.uuid4().hex[:6]}.bin")
        with open(payload, "wb") as fh:
            fh.write(data)
        self._tabs_changed = bool(re.search(rb"\x1bH|\x1b\[[0-9;]*g|\x1bc", data))

        go = f"go{uuid.uuid4().hex[:6]}"
        done = f"done{uuid.uuid4().hex[:6]}"
        tmux_q = shlex.quote(TMUX)
        sock_q = shlex.quote(self.socket)
        script = (
            f"stty -opost -echo -icanon; "
            f"{tmux_q} -L {sock_q} wait-for {go}; "
            f"cat {shlex.quote(payload)}; "
            f"{tmux_q} -L {sock_q} wait-for -S {done}; "
            f"exec sleep 3600"
        )
        env = dict(os.environ)
        env.pop("TMUX", None)
        _run(
            [TMUX, "-L", self.socket, "-f", "/dev/null", "new-session", "-d",
             "-x", str(self.cols), "-y", str(self.rows), "sh", "-c", script],
            capture_output=True, text=True, env=env, timeout=timeout, check=True,
        )
        self._started = True
        self._tmux("set-option", "-g", "status", "off")
        self._tmux("resize-window", "-x", str(self.cols), "-y", str(self.rows), check=False)
        # Wait for stty to have run before releasing cat.
        time.sleep(0.05)
        self._tmux("wait-for", "-S", go)
        self._tmux("wait-for", done, timeout=timeout)

        deadline = time.time() + timeout
        previous = None
        result = None
        while time.time() < deadline:
            time.sleep(settle)
            result = self._snapshot()
            key = (tuple(result.lines), result.cursor_x, result.cursor_y, result.alternate_on)
            if key == previous:
                return result
            previous = key
        assert result is not None
        return result

    def _snapshot(self) -> ScreenResult:
        # Tab stops as they are now. A TAB printed before a stop was cleared
        # expands differently; for that case only, fall back to inferring the
        # TAB width from ``-N`` padding. The padding reaches the row's
        # allocated size, which is not always the full width (a row holding
        # a TAB and wide characters was padded to 20 of 40 columns), so it is
        # not used when the stops never changed.
        tabs_raw = self._tmux("display-message", "-p", "#{pane_tabs}").stdout.strip()
        stops = [int(v) for v in tabs_raw.split(",") if v.strip().isdigit()]
        pane = self._tmux("capture-pane", "-p", "-e", "-N").stdout
        lines = pane.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        carry: dict = {}
        padded = bool(getattr(self, "_tabs_changed", False))
        lines = [normalise_capture_line(ln, self.cols, tab_stops=stops, carry=carry, padded=padded)
                 for ln in lines[: self.rows]]
        while len(lines) < self.rows:
            lines.append("")
        joined_raw = self._tmux("capture-pane", "-p", "-e", "-J").stdout.split("\n")
        if joined_raw and joined_raw[-1] == "":
            joined_raw.pop()
        carry = {}
        joined = [normalise_capture_line(ln, self.cols, wrap=True, tab_stops=stops, carry=carry)
                  for ln in joined_raw]
        while joined and joined[-1] == "":
            joined.pop()
        info = self._tmux(
            "display-message", "-p",
            "#{cursor_x} #{cursor_y} #{alternate_on} #{cursor_flag} #{pane_width} #{pane_height}",
        ).stdout.split()
        cx, cy, alt, cflag, pw, ph = (int(v) for v in info)
        if (pw, ph) != (self.cols, self.rows):
            raise RuntimeError(f"tmux pane is {pw}x{ph}, expected {self.cols}x{self.rows}")
        return ScreenResult(
            lines=lines, cursor_x=cx, cursor_y=cy, alternate_on=bool(alt),
            joined=joined, cursor_visible=bool(cflag),
        )


def termreel_render(data: bytes, cols: int = 80, rows: int = 24,
                    chunks: Optional[Sequence[int]] = None) -> ScreenResult:
    """Feed ``data`` to TermReel's emulator, optionally split at ``chunks`` offsets."""
    from termreel.emulator.parser import ANSIParser
    from termreel.emulator.state import TerminalState

    state = TerminalState(rows=rows, cols=cols)
    parser = ANSIParser(state)
    if chunks:
        cuts = sorted(set(c for c in chunks if 0 < c < len(data)))
        start = 0
        for cut in cuts + [len(data)]:
            parser.feed(data[start:cut])
            start = cut
    else:
        parser.feed(data)
    lines = [state.get_line_text(r) for r in range(rows)]
    joined = state.get_logical_lines()
    while joined and joined[-1] == "":
        joined.pop()
    return ScreenResult(
        lines=lines,
        cursor_x=state.display_cursor_col(),
        cursor_y=state.cursor.row,
        alternate_on=state.in_alt_buffer,
        joined=joined,
        cursor_visible=state.cursor.visible,
    )


def describe_diff(expected: ScreenResult, actual: ScreenResult) -> str:
    """Readable side-by-side of the rows that differ."""
    out = []
    for idx, (a, b) in enumerate(zip(expected.lines, actual.lines)):
        if a != b:
            out.append(f"row {idx:2d} tmux    : {a!r}\nrow {idx:2d} termreel: {b!r}")
    if (expected.cursor_x, expected.cursor_y) != (actual.cursor_x, actual.cursor_y):
        out.append(
            f"cursor tmux=({expected.cursor_x},{expected.cursor_y}) "
            f"termreel=({actual.cursor_x},{actual.cursor_y})"
        )
    if expected.alternate_on != actual.alternate_on:
        out.append(f"alternate_on tmux={expected.alternate_on} termreel={actual.alternate_on}")
    return "\n".join(out) or "(no difference)"


def record_pty_session(argv: Sequence[str], inputs: Iterable[tuple], cols: int = 80, rows: int = 24,
                       env: Optional[dict] = None, cwd: Optional[str] = None,
                       settle: float = 0.6, timeout: float = 20.0) -> bytes:
    """
    Run a real program on a PTY of the given size and return every byte it wrote.

    ``inputs`` is a sequence of ``(delay_seconds, bytes)`` pairs typed into the
    program. The bytes are what a real application emits for TERM=xterm-256color,
    so replaying them into tmux and into TermReel exercises the sequences apps
    actually use (REP, scroll regions, DECSTBM, alt screen) rather than the ones
    a test author thought of.
    """
    import fcntl
    import pty
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    full_env = dict(os.environ)
    full_env.pop("TMUX", None)
    full_env.update({"TERM": "xterm-256color", "LINES": str(rows), "COLUMNS": str(cols),
                     "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    if env:
        full_env.update(env)
    proc = subprocess.Popen(list(argv), stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                            env=full_env, start_new_session=True, close_fds=True)
    os.close(slave)
    out = bytearray()

    def pump(duration: float) -> None:
        end = time.time() + duration
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                return
            r, _, _ = select.select([master], [], [], min(remaining, 0.05))
            if master in r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                out.extend(chunk)

    try:
        pump(settle)
        for delay, data in inputs:
            pump(delay)
            os.write(master, data)
        pump(settle)
        deadline = time.time() + timeout
        while proc.poll() is None and time.time() < deadline:
            pump(0.1)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        pump(0.1)
        os.close(master)
    return bytes(out)
