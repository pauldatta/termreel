"""
Factory for instantiating terminal supervisors based on backend preference and platform.
"""

import shutil
from typing import Optional, Dict
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
) -> BaseSupervisor:
    """
    Create a terminal supervisor.
    - backend='tmux': Use tmux session (recommended for full TUI capture)
    - backend='pty': Use native POSIX openpty
    - backend='auto': Use tmux if available, otherwise fall back to pty
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
        )
    else:
        raise ValueError(f"Unknown terminal supervisor backend: '{backend}'. Choose 'auto', 'tmux', or 'pty'.")
