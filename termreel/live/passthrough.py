"""
Raw-mode stdin passthrough with a hotkey prefix state machine.

The operator drives their own shell. Everything they type is forwarded
verbatim to the PTY master except a prefix keystroke and the key that follows
it, which are intercepted as recording controls.

This module deliberately does **not** read the PTY master. A pty master is a
single-consumer stream: whichever reader calls os.read() first gets the bytes
and the other never sees them. PtySupervisor owns that read; the operator's
view is fed by its on_output hook.
"""

import atexit
import errno
import os
import select
import signal
import sys
import termios
import threading
import tty
from typing import Callable, Dict, List, Optional, Tuple

# Bracketed paste delimiters. A paste arrives as one burst and can legitimately
# contain the prefix byte, so the FSM must be suppressed between these.
PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"

_MAX_MARKER = max(len(PASTE_START), len(PASTE_END))

# Action names emitted by the FSM.
ACTION_TOGGLE_PAUSE = "toggle_pause"
ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_STOP = "stop"
ACTION_MARK = "mark"
ACTION_HELP = "help"
ACTION_UNKNOWN = "unknown"

DEFAULT_BINDINGS: Dict[bytes, str] = {
    b"p": ACTION_TOGGLE_PAUSE,
    b" ": ACTION_TOGGLE_PAUSE,
    b"r": ACTION_RESUME,
    b"m": ACTION_MARK,
    b"q": ACTION_STOP,
    b"?": ACTION_HELP,
}


class PrefixFSM:
    """
    Byte-level state machine splitting operator input into forwarded bytes and
    control actions.

    Pure and synchronous so it can be tested against real byte sequences
    without a terminal. ``feed`` returns (bytes_to_forward, actions).
    """

    def __init__(
        self,
        prefix: bytes,
        bindings: Optional[Dict[bytes, str]] = None,
    ):
        if len(prefix) != 1:
            raise ValueError(f"Prefix must be exactly one byte, got {prefix!r}")
        self.prefix = prefix
        self.bindings = dict(bindings) if bindings is not None else dict(DEFAULT_BINDINGS)

        self.pending = False
        self.in_paste = False
        self._tail = bytearray()

    def _remember(self, byte: int) -> None:
        self._tail.append(byte)
        if len(self._tail) > _MAX_MARKER:
            del self._tail[: len(self._tail) - _MAX_MARKER]

    def feed(self, data: bytes) -> Tuple[bytes, List[str]]:
        """Consume a read() chunk from the operator's terminal."""
        forward = bytearray()
        actions: List[str] = []

        for byte in data:
            single = bytes([byte])

            if self.in_paste:
                forward.append(byte)
                self._remember(byte)
                if bytes(self._tail).endswith(PASTE_END):
                    self.in_paste = False
                continue

            if self.pending:
                self.pending = False
                if single == self.prefix:
                    # Double prefix types a literal prefix byte. Without this,
                    # rebinding would only relocate the collision instead of
                    # resolving it.
                    forward += self.prefix
                    self._remember(byte)
                    continue
                lowered = single.lower() if b"A" <= single <= b"Z" else single
                actions.append(self.bindings.get(lowered, ACTION_UNKNOWN))
                continue

            if single == self.prefix:
                self.pending = True
                continue

            forward.append(byte)
            self._remember(byte)
            if bytes(self._tail).endswith(PASTE_START):
                self.in_paste = True

        return bytes(forward), actions

    def reset(self) -> None:
        """Drop any half-consumed prefix or paste state."""
        self.pending = False
        self.in_paste = False
        self._tail.clear()


class TerminalGuard:
    """
    Put a tty into raw mode and guarantee it is restored.

    Raw mode, not cbreak: cbreak leaves ISIG enabled, so Ctrl-C would kill
    termreel instead of reaching the recorded shell. The flip side is that
    termreel loses its own Ctrl-C, which makes restoration mandatory rather
    than merely tidy -- hence the signal handlers and the atexit hook as well
    as the context manager.

    ``on_terminate`` turns SIGTERM/SIGHUP into a *request* to shut down rather
    than an immediate exit. That distinction matters: exiting from the handler
    skips the owner's teardown, which is where the encoder is finalised, the
    asciicast buffer is flushed and the recorded child's process group is
    killed. A second signal, or no callback at all, still hard-exits.
    """

    def __init__(
        self,
        fd: int,
        install_signal_handlers: bool = True,
        on_terminate: Optional[Callable[[int], None]] = None,
    ):
        self.fd = fd
        self.install_signal_handlers = install_signal_handlers
        self.on_terminate = on_terminate
        self._saved: Optional[list] = None
        self._previous_handlers: Dict[int, object] = {}
        self._restored = threading.Event()
        self._entered = False
        self._terminating = False

    @property
    def active(self) -> bool:
        """True while the tty is in raw mode, so output needs CRLF not LF."""
        return self._entered and not self._restored.is_set()

    @property
    def restored(self) -> bool:
        return self._restored.is_set()

    def __enter__(self) -> "TerminalGuard":
        self._saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        self._entered = True
        # Last-ditch net for exit paths that bypass the finally block.
        atexit.register(self.restore)
        if self.install_signal_handlers:
            for sig in (signal.SIGTERM, signal.SIGHUP):
                try:
                    self._previous_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, self._on_signal)
                except (ValueError, OSError):
                    # Not the main thread, or the platform will not allow it.
                    pass
        return self

    def _on_signal(self, signum, frame):  # pragma: no cover - signal path
        if self.on_terminate is not None and not self._terminating:
            # First signal: ask the owner to stop cleanly and let the ordinary
            # teardown run. That path restores the terminal in its finally
            # block *and* finalises the recording.
            self._terminating = True
            try:
                self.on_terminate(int(signum))
            except Exception:
                pass
            return

        self.restore()
        previous = self._previous_handlers.get(signum)
        if callable(previous):
            previous(signum, frame)
        else:
            os._exit(128 + int(signum))

    def restore(self) -> None:
        """Restore the saved terminal attributes. Safe to call repeatedly."""
        if self._restored.is_set():
            return
        self._restored.set()
        if self._saved is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
            except (termios.error, OSError, ValueError):
                pass
        for sig, previous in self._previous_handlers.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError, TypeError):
                pass
        try:
            atexit.unregister(self.restore)
        except Exception:
            pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.restore()


class PassthroughLoop:
    """
    Forward operator keystrokes to the PTY master, intercepting hotkeys.

    Watches only [stdin, wake_pipe]. The PTY master is deliberately absent:
    see the module docstring.
    """

    def __init__(
        self,
        stdin_fd: int,
        write_to_child: Callable[[bytes], None],
        fsm: PrefixFSM,
        on_action: Callable[[str], None],
        read_size: int = 4096,
    ):
        self.stdin_fd = stdin_fd
        self.write_to_child = write_to_child
        self.fsm = fsm
        self.on_action = on_action
        self.read_size = read_size

        self._wake_r, self._wake_w = os.pipe()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        """Run the loop until stop() is called or stdin reaches EOF."""
        try:
            while not self._stop.is_set():
                try:
                    readable, _, _ = select.select([self.stdin_fd, self._wake_r], [], [])
                except OSError as exc:
                    if exc.errno == errno.EINTR:
                        continue
                    raise

                if self._wake_r in readable:
                    try:
                        os.read(self._wake_r, 64)
                    except OSError:
                        pass
                    if self._stop.is_set():
                        break

                if self.stdin_fd in readable:
                    try:
                        chunk = os.read(self.stdin_fd, self.read_size)
                    except OSError as exc:
                        if exc.errno in (errno.EINTR, errno.EAGAIN):
                            continue
                        break
                    if not chunk:
                        break

                    forward, actions = self.fsm.feed(chunk)
                    if forward:
                        try:
                            self.write_to_child(forward)
                        except OSError:
                            break
                    for action in actions:
                        self.on_action(action)
        except BaseException as exc:  # surfaced to the caller, never swallowed
            self.error = exc
        finally:
            self._stop.set()

    def start(self) -> None:
        """Run the loop on a background thread."""
        self._thread = threading.Thread(target=self.run, name="termreel-passthrough", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """
        Ask the loop to exit, without joining or closing anything.

        Safe to call from the loop's own thread, which is where it normally
        happens: the stop hotkey is dispatched from inside run().
        """
        self._stop.set()
        wake_fd = self._wake_w
        if wake_fd is not None:
            try:
                os.write(wake_fd, b"\x00")
            except OSError:
                pass

    def stop(self, join_timeout: float = 1.0) -> None:
        """Wake the loop out of select(), shut it down and release its fds."""
        self.request_stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)
        self.close()

    def close(self) -> None:
        for fd_attr in ("_wake_r", "_wake_w"):
            fd = getattr(self, fd_attr, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, fd_attr, None)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()
