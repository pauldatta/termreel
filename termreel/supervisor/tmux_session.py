"""
Tmux-backed PTY supervisor for managing isolated CLI sessions.
"""

import os
import subprocess
import time
import uuid
from typing import Optional, Dict
from termreel.supervisor.base import BaseSupervisor


class TmuxSupervisor(BaseSupervisor):
    """
    Supervisor driving CLI sessions inside an isolated tmux session.
    Provides rock-solid terminal emulation, window sizing, and snapshot capture.
    """

    def __init__(
        self,
        command: str = "bash",
        cwd: Optional[str] = None,
        rows: int = 30,
        cols: int = 100,
        session_name: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        self.command = command
        self.cwd = os.path.abspath(cwd) if cwd else os.getcwd()
        self.rows = rows
        self.cols = cols
        self.session_name = session_name or f"termreel_{uuid.uuid4().hex[:8]}"
        self.env = env or {}
        self._started = False

    def start(self) -> None:
        """Start the isolated tmux session."""
        # Ensure any leftover session with this name is killed
        subprocess.run(
            ["tmux", "kill-session", "-t", self.session_name],
            capture_output=True,
        )

        merged_env = os.environ.copy()
        merged_env.update(self.env)
        merged_env["TERM"] = "xterm-256color"
        merged_env["COLORTERM"] = "truecolor"
        merged_env["COLUMNS"] = str(self.cols)
        merged_env["LINES"] = str(self.rows)

        cmd = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.session_name,
            "-x",
            str(self.cols),
            "-y",
            str(self.rows),
            self.command,
        ]

        res = subprocess.run(cmd, cwd=self.cwd, env=merged_env, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to start tmux session '{self.session_name}': {res.stderr}")

        # Configure tmux session options for clean recording
        subprocess.run(["tmux", "set-option", "-t", self.session_name, "status", "off"], capture_output=True)
        subprocess.run(["tmux", "set-option", "-t", self.session_name, "history-limit", "5000"], capture_output=True)
        self._started = True

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        """Type characters into tmux session with optional per-char delay."""
        if not self._started:
            raise RuntimeError("Tmux supervisor is not running.")
        if delay_per_char > 0:
            for ch in text:
                subprocess.run(
                    ["tmux", "send-keys", "-t", self.session_name, "-l", ch],
                    capture_output=True,
                )
                time.sleep(delay_per_char)
        else:
            subprocess.run(
                ["tmux", "send-keys", "-t", self.session_name, "-l", text],
                capture_output=True,
            )

    def send_key(self, key_name: str) -> None:
        """Send a special key (e.g. Enter, Escape, C-c, C-d, Up, Down)."""
        if not self._started:
            raise RuntimeError("Tmux supervisor is not running.")
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session_name, key_name],
            capture_output=True,
        )

    def send_raw(self, data: bytes) -> None:
        """Send raw string data to tmux session."""
        self.send_text(data.decode("utf-8", errors="replace"))

    def paste_text(self, text: str) -> None:
        """Paste block of text via tmux buffer."""
        if not self._started:
            raise RuntimeError("Tmux supervisor is not running.")
        buf_name = f"buf_{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "set-buffer", "-b", buf_name, "--", text],
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-b", buf_name, "-t", self.session_name, "-d"],
            capture_output=True,
        )

    def capture_ansi(self) -> str:
        """Capture current pane with full ANSI color escapes."""
        if not self._started:
            return ""
        res = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session_name, "-p", "-e"],
            capture_output=True,
            text=True,
        )
        return res.stdout if res.returncode == 0 else ""

    def capture_plain(self) -> str:
        """Capture current pane as plain text."""
        if not self._started:
            return ""
        res = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session_name, "-p"],
            capture_output=True,
            text=True,
        )
        return res.stdout if res.returncode == 0 else ""

    def resize(self, rows: int, cols: int) -> None:
        """Resize tmux window geometry."""
        self.rows = rows
        self.cols = cols
        if self._started:
            subprocess.run(
                ["tmux", "resize-window", "-t", self.session_name, "-x", str(cols), "-y", str(rows)],
                capture_output=True,
            )

    def is_alive(self) -> bool:
        """Check if tmux session is still active."""
        if not self._started:
            return False
        res = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            capture_output=True,
        )
        return res.returncode == 0

    def terminate(self) -> None:
        """Kill the tmux session."""
        if self._started:
            subprocess.run(
                ["tmux", "kill-session", "-t", self.session_name],
                capture_output=True,
            )
            self._started = False
