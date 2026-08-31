"""
Telemetry data models: session metadata, screen snapshots, and state representations.
"""

from dataclasses import dataclass, field, asdict
import json
import threading
import time
from typing import Any, Dict, Optional

from termreel.emulator.state import TerminalState


@dataclass
class SessionMetadata:
    """
    Metadata describing an active or historical TermReel recording session.
    """
    session_id: str
    pid: int
    scenario_title: str = ""
    scenario_path: str = ""
    output_video: str = ""
    started_at: float = field(default_factory=time.time)
    current_step_index: int = 0
    total_steps: int = 0
    current_step_type: str = ""
    current_step_desc: str = ""
    fps: int = 30
    rendered_frames: int = 0
    elapsed_seconds: float = 0.0
    socket_path: str = ""
    status: str = "running"  # "running", "completed", "failed"

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMetadata":
        """Reconstruct SessionMetadata from a dictionary."""
        return cls(
            session_id=str(data.get("session_id", "")),
            pid=int(data.get("pid", 0)),
            scenario_title=str(data.get("scenario_title", "")),
            scenario_path=str(data.get("scenario_path", "")),
            output_video=str(data.get("output_video", "")),
            started_at=float(data.get("started_at", 0.0)),
            current_step_index=int(data.get("current_step_index", 0)),
            total_steps=int(data.get("total_steps", 0)),
            current_step_type=str(data.get("current_step_type", "")),
            current_step_desc=str(data.get("current_step_desc", "")),
            fps=int(data.get("fps", 30)),
            rendered_frames=int(data.get("rendered_frames", 0)),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
            socket_path=str(data.get("socket_path", "")),
            status=str(data.get("status", "running")),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize metadata to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "SessionMetadata":
        """Deserialize SessionMetadata from a JSON string."""
        return cls.from_dict(json.loads(json_str))


@dataclass
class ScreenSnapshot:
    """
    Point-in-time snapshot of the terminal screen state including text and ANSI styling.
    """
    text: str
    ansi_text: str
    cursor_row: int
    cursor_col: int
    cursor_visible: bool
    rows: int
    cols: int
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScreenSnapshot":
        """Reconstruct ScreenSnapshot from a dictionary."""
        return cls(
            text=str(data.get("text", "")),
            ansi_text=str(data.get("ansi_text", "")),
            cursor_row=int(data.get("cursor_row", 0)),
            cursor_col=int(data.get("cursor_col", 0)),
            cursor_visible=bool(data.get("cursor_visible", True)),
            rows=int(data.get("rows", 0)),
            cols=int(data.get("cols", 0)),
            timestamp=float(data.get("timestamp", 0.0)),
        )

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize snapshot to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "ScreenSnapshot":
        """Deserialize ScreenSnapshot from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_terminal_state(
        cls,
        state: TerminalState,
        timestamp: Optional[float] = None,
    ) -> "ScreenSnapshot":
        """
        Create a ScreenSnapshot directly from an active TerminalState instance.
        Extracts plain text and generates complete ANSI escape sequence rendering.
        """
        ts = timestamp if timestamp is not None else time.time()
        lock = getattr(state, "_lock", None)
        if lock is None:
            lock = threading.Lock()

        with lock:
            text = state.get_rendered_text(strip_trailing=True)
            cur_row = state.cursor.row
            cur_col = state.cursor.col
            cur_vis = state.cursor.visible
            rows = state.rows
            cols = state.cols

            ansi_lines = []
            for r in range(rows):
                line_parts = []
                last_fg = None
                last_bg = None
                last_attrs = None
                row_cells = state.grid[r]

                # Identify last non-empty/non-space cell to avoid trailing spaces
                end_col = cols
                while end_col > 0:
                    c_cell = row_cells[end_col - 1]
                    if c_cell.char != " " or c_cell.reverse:
                        break
                    end_col -= 1

                for c in range(end_col):
                    cell = row_cells[c]
                    fg = cell.effective_fg
                    bg = cell.effective_bg
                    attrs = (
                        cell.bold,
                        cell.dim,
                        cell.italic,
                        cell.underline,
                        cell.reverse,
                        cell.strikethrough,
                    )

                    if (fg != last_fg) or (bg != last_bg) or (attrs != last_attrs):
                        seq = "\033[0m"
                        if attrs[0]: seq += "\033[1m"
                        if attrs[1]: seq += "\033[2m"
                        if attrs[2]: seq += "\033[3m"
                        if attrs[3]: seq += "\033[4m"
                        if attrs[4]: seq += "\033[7m"
                        if attrs[5]: seq += "\033[9m"

                        r_fg = max(0, min(255, int(fg[0] * 255)))
                        g_fg = max(0, min(255, int(fg[1] * 255)))
                        b_fg = max(0, min(255, int(fg[2] * 255)))
                        r_bg = max(0, min(255, int(bg[0] * 255)))
                        g_bg = max(0, min(255, int(bg[1] * 255)))
                        b_bg = max(0, min(255, int(bg[2] * 255)))

                        seq += f"\033[38;2;{r_fg};{g_fg};{b_fg}m\033[48;2;{r_bg};{g_bg};{b_bg}m"
                        line_parts.append(seq)
                        last_fg, last_bg, last_attrs = fg, bg, attrs

                    line_parts.append(cell.char)

                if last_attrs is not None:
                    line_parts.append("\033[0m")
                ansi_lines.append("".join(line_parts))

            # Trim empty trailing lines
            while ansi_lines and not ansi_lines[-1]:
                ansi_lines.pop()

            ansi_text = "\n".join(ansi_lines)

        return cls(
            text=text,
            ansi_text=ansi_text,
            cursor_row=cur_row,
            cursor_col=cur_col,
            cursor_visible=cur_vis,
            rows=rows,
            cols=cols,
            timestamp=ts,
        )
