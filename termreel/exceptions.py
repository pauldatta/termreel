"""
Unified exception hierarchy for TermReel.
Provides structured error diagnostics and descriptive failure reports.
"""

from typing import Optional, Any, Dict


class TermReelError(Exception):
    """Base exception for all TermReel operations."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class ScenarioError(TermReelError):
    """Raised when a scenario fails validation or execution."""
    pass


class ScenarioValidationError(ScenarioError):
    """Raised when a scenario YAML manifest is invalid."""
    pass


class StepExecutionError(ScenarioError):
    """Raised when a timeline step encounters a fatal execution error."""
    pass


class StepTimeoutError(StepExecutionError):
    """Raised when a timeline step exceeds its timeout."""
    pass


class SupervisorError(TermReelError):
    """Raised when PTY or Tmux session supervision fails."""
    pass


class PtyAllocationError(SupervisorError):
    """Raised when allocating or configuring a pseudo-terminal fails."""
    pass


class ProcessExecutionError(SupervisorError):
    """Raised when launching or interacting with a child process fails."""
    pass


class TranscoderError(TermReelError):
    """Raised when FFmpeg video encoding or GIF transcoding fails."""
    pass


class FFmpegDeadlockError(TranscoderError):
    """Raised when FFmpeg process hangs or pipe deadlocks."""
    pass


class ScreenAssertionError(ScenarioError):
    """Raised when an expected text pattern is missing or unexpected text is present."""
    def __init__(self, pattern: str, actual_screen: str, timeout: float):
        msg = f"Screen assertion failed: Expected pattern '{pattern}' was not satisfied within {timeout:.1f}s."
        super().__init__(msg, {"pattern": pattern, "timeout": timeout, "screen_snippet": actual_screen[:200]})
        self.pattern = pattern
        self.actual_screen = actual_screen
        self.timeout = timeout


class HookBridgeError(TermReelError):
    """Raised when Antigravity lifecycle hook communication fails."""
    pass
