"""
Native POSIX pseudo-terminal (PTY) supervisor.
"""

import os
import pty
import fcntl
import termios
import struct
import select
import shutil
import signal
import subprocess
import threading
import time
from typing import Optional, Dict, List, Union, Any, Pattern, Callable
from termreel.supervisor.base import BaseSupervisor
from termreel.emulator.state import TerminalState
from termreel.emulator.parser import ANSIParser
from termreel.utils.keystrokes import KeyMap


KEY_SEQUENCES = {
    "Enter": "\r",
    "Return": "\r",
    "Escape": "\x1b",
    "Esc": "\x1b",
    "Tab": "\t",
    "BSpace": "\x7f",
    "Backspace": "\x7f",
    "Space": " ",
    "Up": "\x1b[A",
    "Down": "\x1b[B",
    "Right": "\x1b[C",
    "Left": "\x1b[D",
    "Home": "\x1b[H",
    "End": "\x1b[F",
    "PageUp": "\x1b[5~",
    "PageDown": "\x1b[6~",
    "C-c": "\x03",
    "C-d": "\x04",
    "C-z": "\x1a",
    "C-l": "\x0c",
    "C-o": "\x0f",
    "C-j": "\n",
    "C-u": "\x15",
    "C-w": "\x17",
    "C-a": "\x01",
    "C-e": "\x05",
}


def _pty_child_preexec() -> None:
    """
    Post-fork, pre-exec setup for the PTY child.

    Runs after subprocess has already dup2'd the slave PTY onto fds 0/1/2, so
    fd 0 is the slave terminal here.

    1. ``setsid()`` detaches from the parent's session and makes the child a
       session leader with no controlling terminal.
    2. ``TIOCSCTTY`` then explicitly claims the slave PTY as the controlling
       terminal. Linux hands a ctty to a session leader that opens a tty, so
       this looked unnecessary there; BSD and macOS do not, and without it the
       child reports "no job control in this shell" and Ctrl-C / Ctrl-Z / fg /
       bg all misbehave.
    """
    os.setsid()
    tiocsctty = getattr(termios, "TIOCSCTTY", None)
    if tiocsctty is None:
        return
    try:
        fcntl.ioctl(0, tiocsctty, 0)
    except OSError:
        # EPERM means some other session already owns this terminal; ENOTTY
        # means fd 0 is not a tty (a caller redirected stdin). Neither is
        # worth aborting the launch over -- the shell fallback that made this
        # work on Linux before is still in play.
        pass


def _session_members(sid: int) -> List[int]:
    """Pids whose session id is ``sid`` (excluding this process)."""
    me = os.getpid()
    pids: List[int] = []
    if os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat", "rb") as fh:
                    stat = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
            # comm (field 2) may contain spaces/parens: split after the last ')'.
            fields = stat[stat.rfind(")") + 2:].split()
            if len(fields) > 3 and fields[3] == str(sid):
                pid = int(name)
                if pid != me:
                    pids.append(pid)
        return pids
    pgrep = shutil.which("pgrep")
    if pgrep:
        try:
            res = subprocess.run([pgrep, "-s", str(sid)], capture_output=True, text=True, timeout=5)
            pids = [int(p) for p in res.stdout.split() if p.isdigit() and int(p) != me]
        except (OSError, subprocess.SubprocessError):
            pass
    return pids


def _kill_session(sid: int, grace: float = 1.0) -> None:
    """SIGTERM, then SIGKILL, every process still in session ``sid``."""
    if sid <= 1 or sid == os.getsid(0):
        return
    members = _session_members(sid)
    if not members:
        return
    for pid in members:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        members = _session_members(sid)
        if not members:
            return
        time.sleep(0.05)
    for pid in members:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class PtySupervisor(BaseSupervisor):
    """
    Direct POSIX PTY supervisor using openpty.
    Manages terminal dimensions, signal propagation, and real-time state emulation.
    """

    def __init__(
        self,
        command: str = "bash",
        cwd: Optional[str] = None,
        rows: int = 30,
        cols: int = 100,
        env: Optional[Dict[str, str]] = None,
        state: Optional[TerminalState] = None,
        parser: Optional[ANSIParser] = None,
        on_output: Optional[Callable[[bytes], None]] = None,
        on_parsed: Optional[Callable[[bytes], None]] = None,
        respond_to_queries: bool = True,
    ):
        self.command = command
        self.cwd = os.path.abspath(cwd) if cwd else os.getcwd()
        self.rows = rows
        self.cols = cols
        self.env = env or {}

        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None

        # A caller (e.g. ScenarioRunner or LiveRecorder) may inject the very
        # TerminalState the renderer draws from, so that the single PTY reader
        # feeds the frame pipeline directly instead of a private shadow grid.
        if parser is not None:
            self.parser = parser
            self.state = state if state is not None else parser.state
        else:
            self.state = state if state is not None else TerminalState(rows=rows, cols=cols)
            self.parser = ANSIParser(self.state)

        # Programs query the terminal (cursor position report ``CSI 6n``,
        # device attributes ``CSI c``) and some block until they get an
        # answer: crossterm/ratatui inline viewports, readline's cursor
        # probes. Nothing else is attached to this PTY, so the supervisor has
        # to answer. ``termreel live`` passes False because the operator's
        # real terminal sees the mirrored query and answers it itself; a
        # second answer would be typed into the program as garbage.
        if respond_to_queries and self.parser.responder is None:
            self.parser.responder = self._answer_query

        # Mirror hook for raw master-fd bytes. The PTY master is a
        # single-consumer stream, so anything that needs to see the child's
        # output (a live passthrough tty, an asciicast log) must hook here
        # rather than spawning a second os.read() loop. Runs before parsing,
        # outside the lock.
        self.on_output: Optional[Callable[[bytes], None]] = on_output
        # Runs under the parse lock right after a chunk is parsed, so the
        # callback's view of "what has been parsed" is exact. Used where a
        # recording must stay consistent with snapshots of the grid.
        self.on_parsed: Optional[Callable[[bytes], None]] = on_parsed

        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending_replies: List[bytes] = []

    def _answer_query(self, text: str) -> None:
        # Called by the parser under the parse lock: only queue here.
        self._pending_replies.append(text.encode("utf-8"))

    def _flush_replies(self) -> None:
        """Send queued query replies without blocking the reader thread."""
        if not self._pending_replies or self.master_fd is None:
            return
        if not self._write_lock.acquire(blocking=False):
            return  # a paste is in progress; try again after the next read
        try:
            while self._pending_replies:
                data = self._pending_replies[0]
                try:
                    written = os.write(self.master_fd, data)
                except (BlockingIOError, InterruptedError):
                    return
                except OSError:
                    self._pending_replies.clear()
                    return
                if written < len(data):
                    self._pending_replies[0] = data[written:]
                    return
                self._pending_replies.pop(0)
        finally:
            self._write_lock.release()

    def _write_all(self, data: bytes, timeout: float = 10.0) -> None:
        """
        Write every byte to the non-blocking master.

        A large paste fills the PTY input buffer (a few KB); the write then
        returns short or raises EAGAIN. Wait for the program to drain it
        instead of dropping the rest.
        """
        if self.master_fd is None:
            raise RuntimeError("PTY supervisor is not running.")
        view = memoryview(data)
        deadline = time.monotonic() + timeout
        with self._write_lock:
            while view:
                fd = self.master_fd
                if fd is None:
                    raise RuntimeError("PTY closed while writing.")
                try:
                    written = os.write(fd, view)
                except (BlockingIOError, InterruptedError):
                    written = 0
                if written:
                    view = view[written:]
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"PTY input stayed full for {timeout:.0f}s; "
                        f"{len(view)} of {len(data)} bytes not delivered"
                    )
                select.select([], [fd], [], min(remaining, 0.1))

    def _set_winsize(self, fd: int, rows: int, cols: int):
        """Set terminal geometry using ioctl TIOCSWINSZ."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    def start(self) -> None:
        """Allocate PTY, configure environment, and launch child process."""
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self._set_winsize(self.slave_fd, self.rows, self.cols)

            # Set master_fd to non-blocking mode
            fl = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

            merged_env = os.environ.copy()
            merged_env.update(self.env)
            merged_env["TERM"] = "xterm-256color"
            merged_env["COLORTERM"] = "truecolor"
            merged_env["COLUMNS"] = str(self.cols)
            merged_env["LINES"] = str(self.rows)

            # Launch child process attached to slave PTY
            self.process = subprocess.Popen(
                self.command,
                shell=isinstance(self.command, str),
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                cwd=self.cwd,
                env=merged_env,
                preexec_fn=_pty_child_preexec,
                close_fds=True,
            )
        except Exception:
            if self.slave_fd is not None:
                try:
                    os.close(self.slave_fd)
                except OSError:
                    pass
                self.slave_fd = None
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            raise

        # Close slave fd in parent process
        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except OSError:
                pass
            self.slave_fd = None

        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()


    def _reader_loop(self):
        """
        Sole consumer of the PTY master fd.

        A pty master is a single-consumer stream: whichever reader calls
        os.read() first gets the bytes and no one else ever sees them. So any
        other component that needs the child's output registers ``on_output``
        instead of opening a competing read loop.
        """
        while self._running and self.master_fd is not None:
            try:
                self._flush_replies()
                r, _, _ = select.select([self.master_fd], [], [], 0.05)
                if self.master_fd in r:
                    chunk = os.read(self.master_fd, 8192)
                    if not chunk:
                        break
                    # Mirror first and deliberately outside self._lock: the
                    # hook typically writes to the operator's real tty, which
                    # can block on flow control, and must not hold up parsing.
                    hook = self.on_output
                    if hook is not None:
                        try:
                            hook(chunk)
                        except Exception:
                            pass
                    with self._lock:
                        self.parser.feed(chunk)
                        parsed_hook = self.on_parsed
                        if parsed_hook is not None:
                            try:
                                parsed_hook(chunk)
                            except Exception:
                                pass
            except (OSError, ValueError):
                break

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        """Write characters to master PTY."""
        if not self._running or self.master_fd is None:
            raise RuntimeError("PTY supervisor is not running.")
        if delay_per_char > 0:
            for ch in text:
                self._write_all(ch.encode("utf-8"))
                time.sleep(delay_per_char)
        else:
            self._write_all(text.encode("utf-8"))

    def send_input(self, text: str, delay_per_char: float = 0.0) -> None:
        """Inject input characters (alias for send_text)."""
        self.send_text(text, delay_per_char=delay_per_char)

    def send_key(self, key_name: str) -> None:
        """Send mapped key code sequence."""
        seq = KeyMap.to_pty(key_name, app_cursor=bool(getattr(self.state, "app_cursor_keys", False)))
        self.send_text(seq)

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes directly."""
        if self._running and self.master_fd is not None:
            self._write_all(data)

    def paste_text(self, text: str) -> None:
        """
        Paste a block of text. Wrapped in bracketed-paste markers only when
        the application has enabled mode 2004, like a real terminal; an app
        that never asked for it would otherwise receive literal ``[200~``.
        Line breaks are sent as CR, as xterm and tmux ``paste-buffer`` do
        (the tty's ICRNL turns them back into LF for cooked-mode readers).
        """
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        if getattr(self.state, "bracketed_paste", False):
            self.send_text(f"\x1b[200~{text}\x1b[201~")
        else:
            self.send_text(text)

    def capture_ansi(self) -> str:
        """Capture screen as ANSI text."""
        return self.capture_plain()

    def capture_plain(self) -> str:
        """Capture rendered plain screen text."""
        with self._lock:
            return self.state.get_rendered_text()

    def capture_prompt_view(self):
        """Screen text, cursor row and scroll count, read atomically."""
        with self._lock:
            return (
                self.state.get_rendered_text(),
                self.state.cursor_row,
                self.state.lines_scrolled,
            )

    def get_screen(self) -> str:
        """Extract live plain screen text."""
        return self.capture_plain()

    def wait_for_output(self, pattern: Union[str, Any], timeout: float = 5.0, interval: float = 0.05) -> bool:
        """Wait until pattern appears in terminal screen or timeout expires."""
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if isinstance(pattern, str):
                    if self.state.contains(pattern):
                        return True
                elif hasattr(pattern, "search"):
                    if self.state.search_regex(pattern):
                        return True
            time.sleep(interval)
        return False

    def resize(self, rows: int, cols: int) -> None:
        """Resize terminal and notify child process via SIGWINCH."""
        self.rows = rows
        self.cols = cols
        with self._lock:
            self.state.resize(rows, cols)
        if self.master_fd is not None:
            try:
                self._set_winsize(self.master_fd, rows, cols)
                if self.process and self.process.poll() is None:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGWINCH)
            except (OSError, ProcessLookupError):
                pass

    def is_alive(self) -> bool:
        """Check if child process is running."""
        if not self.process:
            return False
        return self.process.poll() is None

    def terminate(self) -> None:
        """
        Clean up the child, everything else in its session, and the PTY.

        The child is a session leader (setsid in the pre-exec hook), so its
        pid is the session id. A background job started inside the recorded
        shell (``sleep 600 &``) lives in its own process group under job
        control, so killpg on the shell's group misses it; it is found by
        session id instead. A process that called setsid() itself (a real
        daemon) has left the session and is not touched.
        """
        self._running = False
        sid = self.process.pid if self.process else None
        if self.process and self.process.poll() is None:
            try:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                self.process.wait(timeout=1.5)
            except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    self.process.wait(timeout=1.0)
                except Exception:
                    pass
        if sid is not None:
            _kill_session(sid)


        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
