"""
Abstract base class for terminal supervisors driving CLI processes.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict


class BaseSupervisor(ABC):
    """
    Abstract interface for managing interactive CLI execution in a pseudo-terminal.
    """

    @abstractmethod
    def start(self) -> None:
        """Start the CLI process in a pseudo-terminal."""
        pass

    @abstractmethod
    def send_text(self, text: str, delay_per_char: float = 0.0) -> None:
        """Type characters into the terminal with optional per-character delay."""
        pass

    @abstractmethod
    def send_key(self, key_name: str) -> None:
        """Send a special key or control sequence (e.g. Enter, Escape, C-c)."""
        pass

    @abstractmethod
    def send_raw(self, data: bytes) -> None:
        """Send raw bytes directly to stdin."""
        pass

    @abstractmethod
    def paste_text(self, text: str) -> None:
        """Paste a block of text into the terminal."""
        pass

    @abstractmethod
    def capture_ansi(self) -> str:
        """Capture the current screen snapshot with full ANSI color escapes."""
        pass

    @abstractmethod
    def capture_plain(self) -> str:
        """Capture the current screen snapshot as plain text without ANSI codes."""
        pass

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal window geometry."""
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if the child process / session is still running."""
        pass

    @abstractmethod
    def terminate(self) -> None:
        """Gracefully terminate the CLI process and clean up PTY resources."""
        pass

    def __enter__(self) -> "BaseSupervisor":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
