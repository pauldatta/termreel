"""
Factory for instantiating terminal supervisors based on backend preference and platform.
"""

import shutil
from typing import Any, Callable, Optional, Dict
from termreel.supervisor.base import BaseSupervisor
from termreel.supervisor.tmux_session import TmuxSupervisor
from termreel.supervisor.pty_session import PtySupervisor


def is_tmux_available() -> bool:
    """Check if tmux binary is installed and executable."""
    return shutil.which("tmux") is not None


def create_supervisor(
    backend: str = "auto",
    command: str = "bash",
    cwd: Optional[str] = None,
    rows: int = 30,
    cols: int = 100,
    session_name: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    state: Optional[Any] = None,
    parser: Optional[Any] = None,
    on_output: Optional[Callable[[bytes], None]] = None,
) -> BaseSupervisor:
    """
    Create a terminal supervisor.
    - backend='tmux': Use tmux session (recommended for full TUI capture)
    - backend='pty': Use native POSIX openpty
    - backend='auto': Use tmux if available, otherwise fall back to pty

    ``state``, ``parser`` and ``on_output`` apply to the PTY backend only. They
    let the caller have the supervisor's single reader parse straight into the
    TerminalState the renderer draws from, and mirror the raw child bytes
    somewhere else. The tmux backend is polled via capture-pane instead and
    has no equivalent, so these are ignored there.
    """
    selected = backend.lower().strip()
    if selected == "auto":
        selected = "tmux" if is_tmux_available() else "pty"

    if selected == "tmux":
        return TmuxSupervisor(
            command=command,
            cwd=cwd,
            rows=rows,
            cols=cols,
            session_name=session_name,
            env=env,
        )
    elif selected == "pty":
        return PtySupervisor(
            command=command,
            cwd=cwd,
            rows=rows,
            cols=cols,
            env=env,
            state=state,
            parser=parser,
            on_output=on_output,
        )
    else:
        raise ValueError(f"Unknown terminal supervisor backend: '{backend}'. Choose 'auto', 'tmux', or 'pty'.")
