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
from typing import Optional, Dict, Union, Any, Pattern
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
    ):
        self.command = command
        self.cwd = os.path.abspath(cwd) if cwd else os.getcwd()
        self.rows = rows
        self.cols = cols
        self.env = env or {}

        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None

        self.state = TerminalState(rows=rows, cols=cols)
        self.parser = ANSIParser(self.state)

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
                preexec_fn=os.setsid,
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
        """Asynchronously reads data from master PTY fd and feeds the ANSI parser."""
        while self._running and self.master_fd is not None:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.05)
                if self.master_fd in r:
                    chunk = os.read(self.master_fd, 8192)
                    if not chunk:
                        break
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
        """Clean up child process and close PTY descriptors."""
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
