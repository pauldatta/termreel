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

    def send_input(self, text: str, delay_per_char: float = 0.0) -> None:
        """Type characters into terminal session (alias for send_text)."""
        self.send_text(text, delay_per_char=delay_per_char)

    def get_screen(self) -> str:
        """Capture rendered plain screen text (alias for capture_plain)."""
        return self.capture_plain()

    def wait_for_output(self, pattern: object, timeout: float = 5.0, interval: float = 0.05) -> bool:
        """Wait until pattern appears in rendered screen text or timeout expires."""
        import time
        start = time.time()
        while time.time() - start < timeout:
            screen = self.get_screen()
            if isinstance(pattern, str):
                if pattern in screen:
                    return True
            elif hasattr(pattern, "search"):
                if pattern.search(screen):
                    return True
            time.sleep(interval)
        return False

    def __enter__(self) -> "BaseSupervisor":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
