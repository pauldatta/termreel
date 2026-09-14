"""
Native POSIX pseudo-terminal (PTY) supervisor.
"""

import os
import pty
import fcntl
import termios
import struct
import select
import signal
import subprocess
import threading
import time
from typing import Optional, Dict, Union, Any, Pattern, Callable
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

        # Mirror hook for raw master-fd bytes. The PTY master is a
        # single-consumer stream, so anything that needs to see the child's
        # output (a live passthrough tty, an asciicast log) must hook here
        # rather than spawning a second os.read() loop.
        self.on_output: Optional[Callable[[bytes], None]] = on_output

        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._raw_output_buffer = bytearray()
        self._lock = threading.Lock()

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
                        self._raw_output_buffer.extend(chunk)
                        self.parser.feed(chunk)
            except (OSError, ValueError):
                break

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        """Write characters to master PTY."""
        if not self._running or self.master_fd is None:
            raise RuntimeError("PTY supervisor is not running.")
        if delay_per_char > 0:
            for ch in text:
                os.write(self.master_fd, ch.encode("utf-8"))
                time.sleep(delay_per_char)
        else:
            os.write(self.master_fd, text.encode("utf-8"))

    def send_input(self, text: str, delay_per_char: float = 0.0) -> None:
        """Inject input characters (alias for send_text)."""
        self.send_text(text, delay_per_char=delay_per_char)

    def send_key(self, key_name: str) -> None:
        """Send mapped key code sequence."""
        seq = KeyMap.to_pty(key_name)
        self.send_text(seq)

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes directly."""
        if self._running and self.master_fd is not None:
            os.write(self.master_fd, data)

    def paste_text(self, text: str) -> None:
        """Bracketed paste mode."""
        self.send_text(f"\x1b[200~{text}\x1b[201~")

    def capture_ansi(self) -> str:
        """Capture screen as ANSI text."""
        return self.capture_plain()

    def capture_plain(self) -> str:
        """Capture rendered plain screen text."""
        with self._lock:
            return self.state.get_rendered_text()

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
        Clean up child process and close PTY descriptors.

        Note: a background job started inside the recorded shell (``sleep 600 &``)
        is *not* reaped. Now that the child owns a controlling terminal it has
        job control, so each job lives in its own process group and killpg on
        the shell's group does not reach it. Measured against bash 5.3: SIGHUP
        to the session, closing the master first, and both together all leave
        the job running, so there is no cheap remedy here.
        """
        self._running = False
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


        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
