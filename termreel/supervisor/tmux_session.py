"""
Tmux-backed supervisor for managing isolated CLI sessions.

Every supervisor runs its own tmux *server* (``tmux -L termreel-<id>``) with a
generated config file, so:

* the user's ``~/.tmux.conf`` (prefix keys, status bars, base-index 1,
  hooks, plugins) cannot change what gets recorded;
* running TermReel from inside tmux (``$TMUX`` set) does not attach the
  recording to, or resize, the user's own server;
* ``terminate()`` kills exactly the recording server and nothing else.

Panes are addressed by their stable ``%id`` rather than ``session:0.N``,
because the numeric index depends on ``pane-base-index`` and shifts when panes
close.

``capture_frame()`` returns geometry, cursor and contents for every pane of
the window. The runner composites them into one screen (with borders), which
is what makes multi-pane recordings show all panes rather than only the active
one.
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from termreel.supervisor.base import BaseSupervisor
from termreel.utils.keystrokes import KeyMap


TMUX_CONF = """\
set -g default-terminal "xterm-256color"
set -g status off
set -g history-limit 5000
set -sg escape-time 10
set -g base-index 0
setw -g pane-base-index 0
set -g remain-on-exit off
set -g destroy-unattached off
set -g exit-empty on
"""

_PANE_FORMAT = (
    "#{pane_id} #{pane_index} #{pane_left} #{pane_top} #{pane_width} "
    "#{pane_height} #{pane_active} #{cursor_x} #{cursor_y} #{cursor_flag} "
    "#{alternate_on}"
)


@dataclass
class PaneCapture:
    """One pane's geometry, cursor, and captured contents."""

    pane_id: str
    index: int
    left: int
    top: int
    width: int
    height: int
    active: bool
    cursor_x: int
    cursor_y: int
    cursor_visible: bool
    alternate_on: bool
    content: str = ""


class TmuxSupervisor(BaseSupervisor):
    """
    Supervisor driving CLI sessions inside a private tmux server.
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
        suffix = uuid.uuid4().hex[:8]
        self.session_name = session_name or f"termreel_{suffix}"
        self.socket_name = f"termreel-{suffix}"
        self.env = env or {}
        self._started = False
        self._conf_dir: Optional[str] = None
        self._conf_path: Optional[str] = None
        self._tmux_bin = shutil.which("tmux") or "tmux"

    # ------------------------------------------------------------ helpers

    def _tmux_cmd(self, *args: str) -> List[str]:
        cmd = [self._tmux_bin, "-L", self.socket_name]
        if self._conf_path:
            cmd += ["-f", self._conf_path]
        return cmd + list(args)

    def _run(self, *args: str, check: bool = False, **kw) -> subprocess.CompletedProcess:
        env = kw.pop("env", None)
        if env is None:
            env = os.environ.copy()
            env.update(self.env)
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
        res = subprocess.run(
            self._tmux_cmd(*args), capture_output=True, text=True, env=env, **kw
        )
        if check and res.returncode != 0:
            raise RuntimeError(
                f"tmux {' '.join(args[:2])} failed: {res.stderr.strip()}"
            )
        return res

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Tmux supervisor is not running.")

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start a private tmux server with one session running ``command``."""
        self._conf_dir = tempfile.mkdtemp(prefix="termreel-tmux-")
        self._conf_path = os.path.join(self._conf_dir, "tmux.conf")
        with open(self._conf_path, "w", encoding="utf-8") as fh:
            fh.write(TMUX_CONF)

        merged_env = os.environ.copy()
        merged_env.update(self.env)
        merged_env.pop("TMUX", None)
        merged_env.pop("TMUX_PANE", None)
        merged_env["TERM"] = "xterm-256color"
        merged_env["COLORTERM"] = "truecolor"
        merged_env["COLUMNS"] = str(self.cols)
        merged_env["LINES"] = str(self.rows)

        res = self._run(
            "new-session", "-d", "-s", self.session_name,
            "-x", str(self.cols), "-y", str(self.rows),
            self.command,
            cwd=self.cwd, env=merged_env,
        )
        if res.returncode != 0:
            self._remove_conf()
            raise RuntimeError(
                f"Failed to start tmux session '{self.session_name}': {res.stderr}"
            )
        # Keep the window at exactly the requested size even though no
        # client is attached.
        self._run("set-option", "-w", "-t", self.session_name, "window-size", "manual")
        self._run("resize-window", "-t", self.session_name,
                  "-x", str(self.cols), "-y", str(self.rows))
        self._started = True

    def _remove_conf(self) -> None:
        if self._conf_dir:
            shutil.rmtree(self._conf_dir, ignore_errors=True)
        self._conf_dir = None
        self._conf_path = None

    def is_alive(self) -> bool:
        """True while the recording session still exists."""
        if not self._started:
            return False
        return self._run("has-session", "-t", self.session_name).returncode == 0

    def terminate(self) -> None:
        """Kill the private tmux server (and only it)."""
        if self._started:
            self._run("kill-server")
            self._started = False
        self._remove_conf()

    # ------------------------------------------------------------ input

    def _active_target(self) -> str:
        return self.session_name

    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        """Type characters into the active pane with optional per-char delay."""
        self._require_started()
        if delay_per_char > 0:
            for ch in text:
                self._run("send-keys", "-t", self._active_target(), "-l", "--", ch)
                time.sleep(delay_per_char)
        else:
            self._run("send-keys", "-t", self._active_target(), "-l", "--", text)

    def send_key(self, key_name: str) -> None:
        """Send a special key (e.g. Enter, Escape, C-c, F5, S-Tab, M-x)."""
        self._require_started()
        # Raises KeySpecError (a ValueError) for keys we cannot express.
        args = KeyMap.tmux_args(key_name)
        res = self._run("send-keys", "-t", self._active_target(), *args)
        if res.returncode != 0:
            raise ValueError(f"tmux rejected key {key_name!r} ({args}): {res.stderr.strip()}")

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes to the active pane, byte-exact (``send-keys -H``)."""
        self._require_started()
        if not data:
            return
        # Chunk to keep argv small.
        for i in range(0, len(data), 512):
            chunk = data[i:i + 512]
            self._run("send-keys", "-t", self._active_target(), "-H",
                      *[f"{b:02x}" for b in chunk])

    def paste_text(self, text: str) -> None:
        """
        Paste a block of text via a tmux buffer. ``paste-buffer -p`` wraps it
        in bracketed-paste markers only if the application enabled ?2004.
        """
        self._require_started()
        buf_name = f"buf_{uuid.uuid4().hex[:6]}"
        self._run("set-buffer", "-b", buf_name, "--", text)
        self._run("paste-buffer", "-p", "-b", buf_name, "-t", self._active_target(), "-d")

    # ------------------------------------------------------------ panes

    def list_panes(self) -> List[PaneCapture]:
        """Return geometry and cursor state for every pane, sorted by index."""
        if not self._started:
            return []
        res = self._run("list-panes", "-t", self.session_name, "-F", _PANE_FORMAT)
        if res.returncode != 0:
            return []
        panes: List[PaneCapture] = []
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) != 11:
                continue
            try:
                panes.append(PaneCapture(
                    pane_id=parts[0],
                    index=int(parts[1]),
                    left=int(parts[2]),
                    top=int(parts[3]),
                    width=int(parts[4]),
                    height=int(parts[5]),
                    active=parts[6] == "1",
                    cursor_x=int(parts[7]),
                    cursor_y=int(parts[8]),
                    cursor_visible=parts[9] == "1",
                    alternate_on=parts[10] == "1",
                ))
            except ValueError:
                continue
        panes.sort(key=lambda p: p.index)
        return panes

    def _pane_id_for(self, pane_index: int) -> str:
        """Resolve the Nth pane (0-based, in index order) to its ``%id``."""
        panes = self.list_panes()
        if not 0 <= pane_index < len(panes):
            raise RuntimeError(
                f"No tmux pane at index {pane_index} (window has {len(panes)} pane(s))"
            )
        return panes[pane_index].pane_id

    def split_pane(self, direction: str = "horizontal", percent: int = 50,
                   command: Optional[str] = None) -> None:
        """Split the active pane. horizontal = side by side, vertical = stacked."""
        self._require_started()
        flag = "-h" if str(direction).lower() in ("horizontal", "h", "right") else "-v"
        args = ["split-window", "-t", self.session_name, flag, "-p", str(percent)]
        if command:
            args.append(command)
        res = self._run(*args, cwd=self.cwd)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to split tmux pane: {res.stderr}")

    def select_pane(self, pane_index: int = 0) -> None:
        """Focus the Nth pane (0-based, in tmux index order)."""
        self._require_started()
        res = self._run("select-pane", "-t", self._pane_id_for(pane_index))
        if res.returncode != 0:
            raise RuntimeError(f"Failed to select tmux pane {pane_index}: {res.stderr}")

    def close_pane(self, pane_index: Optional[int] = None) -> None:
        """Close the Nth pane, or the active pane when no index is given."""
        self._require_started()
        if pane_index is None:
            active = [p for p in self.list_panes() if p.active]
            if not active:
                raise RuntimeError("No active tmux pane to close")
            target = active[0].pane_id
        else:
            target = self._pane_id_for(pane_index)
        res = self._run("kill-pane", "-t", target)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to close tmux pane {pane_index}: {res.stderr}")

    # ------------------------------------------------------------ capture

    def capture_frame(self) -> List[PaneCapture]:
        """
        Capture every pane's geometry, cursor, and ANSI contents.

        The list-panes query and the captures are two tmux invocations, so a
        pane can change in between; the caller composites whatever it gets
        and the next frame corrects it.
        """
        panes = self.list_panes()
        if not panes:
            return []
        # Must not start with '-' (tmux would parse it as a flag).
        nonce = f"TERMREEL-PANE-END-{uuid.uuid4().hex}"
        args: List[str] = []
        for i, pane in enumerate(panes):
            if i:
                args.append(";")
            args += ["capture-pane", "-p", "-e", "-J", "-t", pane.pane_id,
                     ";", "display-message", "-p", "-t", pane.pane_id, nonce]
        res = self._run(*args)
        if res.returncode != 0 and not res.stdout:
            return []
        chunks = res.stdout.split(nonce + "\n")
        for pane, chunk in zip(panes, chunks):
            pane.content = chunk
        return panes

    def capture_ansi(self) -> str:
        """Capture the active pane with ANSI escapes (single-pane view)."""
        if not self._started:
            return ""
        res = self._run("capture-pane", "-t", self.session_name, "-p", "-e", "-N")
        return res.stdout if res.returncode == 0 else ""

    def capture_plain(self, include_scrollback: bool = False, history_lines: int = 500) -> str:
        """Capture the active pane as plain text, optionally with scrollback."""
        if not self._started:
            return ""
        args = ["capture-pane", "-t", self.session_name, "-p"]
        if include_scrollback:
            args += ["-S", f"-{history_lines}"]
        res = self._run(*args)
        return res.stdout if res.returncode == 0 else ""

    def capture_prompt_view(self):
        """
        Active pane text, cursor row and history size from one tmux call.

        ``history_size`` grows by one per row scrolled off the pane until it
        reaches ``history-limit``; after that it stops growing, and edge
        triggers fall back to noticing that the prompt line changed.
        """
        if not self._started:
            return "", None, 0
        res = self._run("capture-pane", "-t", self.session_name, "-p", ";",
                        "display-message", "-p", "-t", self.session_name,
                        "TERMREEL-VIEW #{cursor_y} #{history_size}")
        if res.returncode != 0:
            return self.capture_plain(), None, 0
        text, sep, tail = res.stdout.rpartition("TERMREEL-VIEW ")
        if not sep:
            return res.stdout, None, 0
        try:
            cy, hist = (int(x) for x in tail.split()[:2])
        except ValueError:
            return text, None, 0
        if text.endswith("\n"):
            text = text[:-1]
        return text, cy, hist

    def resize(self, rows: int, cols: int) -> None:
        """Resize the window."""
        self.rows = rows
        self.cols = cols
        if self._started:
            self._run("resize-window", "-t", self.session_name,
                      "-x", str(cols), "-y", str(rows))
