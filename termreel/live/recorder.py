"""
`termreel live` recorder: human-driven capture with pause, cut and crossfade.

Three rules come out of how a PTY and this renderer actually behave, and
everything here follows from them:

1. **One reader.** Only PtySupervisor reads the PTY master. The operator's
   view is a mirror fed by its ``on_output`` hook.
2. **One state.** The supervisor parses into the same TerminalState the
   renderer draws from, so no reconciliation is needed.
3. **Never write or blend frames on the input thread.** ``write_frame`` takes
   a lock and blocks on encoder backpressure; the keystroke path must not.
"""

import math
import os
import shutil
import struct
import sys
import termios
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import cairo

import fcntl

from termreel.emulator.parser import ANSIParser
from termreel.emulator.state import TerminalState
from termreel.exceptions import TermReelError
from termreel.live.config import PrefixBinding
from termreel.live.passthrough import (
    ACTION_HELP,
    ACTION_MARK,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_STOP,
    ACTION_TOGGLE_PAUSE,
    ACTION_UNKNOWN,
    DEFAULT_BINDINGS,
    PassthroughLoop,
    PrefixFSM,
    TerminalGuard,
)
from termreel.renderer.cairo_renderer import CairoTerminalRenderer, pixels_for_grid
from termreel.supervisor.pty_session import PtySupervisor
from termreel.telemetry.models import SessionMetadata
from termreel.telemetry.registry import SessionRegistry
from termreel.telemetry.server import TelemetryServer
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.utils.asciicast import AsciicastRecorder
from termreel.utils.redaction import Redactor


# Above this canvas area the producer side alone eats a large share of the
# frame budget (measured: 13.8 ms per draw_frame at 2546x1546), before the
# encoder has seen a byte.
LARGE_CANVAS_PIXELS = 1920 * 1080

# How often the frame thread re-reads the operator's terminal size. Fast enough
# that a human dragging a window corner does not notice, slow enough that the
# ioctl is free at any frame rate.
WINSIZE_POLL_SECONDS = 0.5


def blend_frames(base: bytes, overlay: bytes, alpha: float, width: int, height: int) -> bytes:
    """
    Composite ``overlay`` over ``base`` at the given alpha.

    Done with cairo rather than per-pixel Python: a 1080p frame is 8.5 MB and
    a pure-Python blend would take longer than the frame interval.
    """
    alpha = max(0.0, min(1.0, float(alpha)))
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, width)
    expected = stride * height
    if len(base) != expected or len(overlay) != expected:
        raise ValueError(
            f"Frame buffers must be {expected} bytes for {width}x{height}; "
            f"got base={len(base)} overlay={len(overlay)}"
        )

    base_buffer = bytearray(base)
    base_surface = cairo.ImageSurface.create_for_data(
        base_buffer, cairo.FORMAT_ARGB32, width, height, stride
    )
    overlay_surface = cairo.ImageSurface.create_for_data(
        bytearray(overlay), cairo.FORMAT_ARGB32, width, height, stride
    )
    ctx = cairo.Context(base_surface)
    ctx.set_source_surface(overlay_surface, 0, 0)
    ctx.paint_with_alpha(alpha)
    base_surface.flush()
    return bytes(base_surface.get_data())


@dataclass
class PendingTransition:
    """A crossfade queued by the input thread, consumed by the frame thread."""

    from_frame: bytes
    duration: float


@dataclass
class LiveReport:
    """Outcome of a live recording."""

    status: str
    output_file: str
    frames_written: int
    fps: int
    video_seconds: float
    wall_seconds: float
    paused_seconds: float
    file_size_bytes: int
    cols: int
    rows: int
    marks: List[float] = field(default_factory=list)
    cast_file: Optional[str] = None
    error_message: Optional[str] = None
    frame_errors: int = 0


class LiveRecorder:
    """Records the operator's own interactive shell session to video."""

    def __init__(
        self,
        command: Optional[str] = None,
        output: str = "output/live.mp4",
        fps: int = 15,
        theme: str = "catppuccin-mocha",
        title: str = "TermReel Live",
        subtitle: str = "Live Capture",
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        font: str = "DejaVu Sans Mono",
        font_size: float = 14.5,
        crossfade: float = 0.25,
        hard_cuts: bool = False,
        prefix: Optional[PrefixBinding] = None,
        cast: Optional[str] = None,
        cwd: Optional[str] = None,
        preset: str = "veryfast",
        crf: int = 20,
        redactions: Optional[List[str]] = None,
        verbose: bool = True,
        stdin_fd: Optional[int] = None,
        stdout_fd: Optional[int] = None,
        enable_telemetry: bool = True,
    ):
        self.command = command or os.environ.get("SHELL") or "bash"
        self.output_file = os.path.abspath(output)
        self.fps = max(1, int(fps))
        self.theme_name = theme
        self.title = title
        self.subtitle = subtitle
        self.crossfade = 0.0 if hard_cuts else max(0.0, float(crossfade))
        self.prefix = prefix
        self.cast_path = os.path.abspath(cast) if cast else None
        self.cwd = os.path.abspath(cwd) if cwd else os.getcwd()
        self.preset = preset
        self.crf = crf
        self.verbose = verbose
        self.enable_telemetry = enable_telemetry

        self.stdin_fd = stdin_fd if stdin_fd is not None else sys.stdin.fileno()
        self.stdout_fd = stdout_fd if stdout_fd is not None else sys.stdout.fileno()

        if width and height:
            canvas_w, canvas_h = int(width), int(height)
        elif cols or rows:
            canvas_w, canvas_h = pixels_for_grid(
                cols or 100, rows or 30, font_family=font, font_size=font_size
            )
        else:
            canvas_w, canvas_h = 1280, 720

        self.width, self.height = canvas_w, canvas_h

        self.renderer = CairoTerminalRenderer(
            width=self.width,
            height=self.height,
            title=self.title,
            subtitle=self.subtitle,
            theme=self.theme_name,
            font_family=font,
            font_size=font_size,
        )
        self.cols = self.renderer.cols
        self.rows = self.renderer.rows

        self.state = TerminalState(
            rows=self.rows,
            cols=self.cols,
            default_fg=self.renderer.theme.default_fg,
            default_bg=self.renderer.theme.terminal_bg,
            palette=self.renderer.theme.palette,
        )
        self.parser = ANSIParser(self.state)
        self.redactor = Redactor(custom_patterns=redactions)

        self.supervisor: Optional[PtySupervisor] = None
        self.pipe: Optional[FFmpegPipe] = None
        self.asciicast: Optional[AsciicastRecorder] = None
        self.telemetry: Optional[TelemetryServer] = None
        self.telemetry_registry: Optional[SessionRegistry] = None
        self.session_id = f"live{os.getpid():06d}"

        self._paused = False
        self._stop_event = threading.Event()
        self._frame_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()

        self._last_frame: Optional[bytes] = None
        self._pending_transition: Optional[PendingTransition] = None

        self.frames_written = 0
        self.frame_errors = 0
        self.frame_error_samples: List[str] = []
        self.paused_seconds = 0.0
        self._pause_started_at: Optional[float] = None
        self.marks: List[float] = []
        self._started_at = 0.0

        self._passthrough: Optional[PassthroughLoop] = None
        self._guard: Optional[TerminalGuard] = None
        self._title_set = False
        self._oversize_warned = False
        self._next_winsize_check = 0.0

    # ------------------------------------------------------------------ logs

    def _log(self, message: str, force: bool = False) -> None:
        """
        Report a status line to the operator.

        While the tty is in raw mode OPOST is cleared, so a bare "\\n" moves
        down a line without returning the carriage and every message
        stair-steps diagonally across the screen. In that state the line goes
        out through the same writer as the mirrored child output, with an
        explicit CRLF, which also keeps the two streams from interleaving
        halfway through an escape sequence.
        """
        if not (self.verbose or force):
            return
        guard = self._guard
        if guard is not None and guard.active:
            self._write_to_operator(f"\r\x1b[0m[termreel] {message}\r\n".encode("utf-8"))
            return
        sys.stderr.write(f"[termreel] {message}\n")
        sys.stderr.flush()

    # ------------------------------------------------------------ tty output

    def _write_to_operator(self, data: bytes) -> None:
        """
        Mirror child output to the operator's real terminal.

        Called on the supervisor's reader thread, outside its parser lock.
        Partial writes are real on a tty under flow control, so loop.
        """
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.stdout_fd, view)
            except BlockingIOError:
                time.sleep(0.002)
                continue
            except InterruptedError:
                continue
            except OSError:
                return
            if written <= 0:
                return
            view = view[written:]

    def _on_child_output(self, chunk: bytes) -> None:
        self._write_to_operator(chunk)
        recorder = self.asciicast
        if recorder is not None:
            try:
                recorder.record_output_bytes(chunk)
            except Exception:
                pass

    def _set_terminal_title(self, text: str) -> None:
        try:
            os.write(self.stdout_fd, f"\x1b]0;{text}\x07".encode("utf-8"))
            self._title_set = True
        except OSError:
            pass

    # --------------------------------------------------------------- geometry

    def _operator_grid(self) -> Optional[Tuple[int, int]]:
        """Current size of the operator's real terminal, if it has one."""
        try:
            packed = fcntl.ioctl(self.stdout_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        except (OSError, ValueError):
            return None
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        if rows <= 0 or cols <= 0:
            return None
        return rows, cols

    def _apply_window_size(self, warn: bool = True) -> None:
        """
        Clamp the child's grid to the locked canvas grid.

        The canvas is fixed for the whole encode, so the child can never be
        allowed to grow past it. Shrinking is fine: the renderer crops with
        ``min(term_state.rows, self.rows)`` and repaints the full background
        each frame, so a smaller grid simply letterboxes.

        Called once at startup and then polled, so that resizing the window
        mid-session still reaches the child. Without that the child keeps its
        stale winsize and every subsequent line wraps at the wrong column.
        """
        if self.supervisor is None:
            return
        measured = self._operator_grid()
        if measured is None:
            return
        rows, cols = measured

        if warn and (rows > self.rows or cols > self.cols) and not self._oversize_warned:
            # Ahead of the early return below: the supervisor starts at exactly
            # the locked grid, so a terminal that is *already* too big clamps to
            # a no-op and the operator would never be told their edges are
            # being cropped.
            self._oversize_warned = True
            self._log(
                f"Terminal is {cols}x{rows} but the recording grid is locked at "
                f"{self.cols}x{self.rows}; the extra area will not be captured."
            )

        clamped_rows = min(rows, self.rows)
        clamped_cols = min(cols, self.cols)
        if clamped_rows == self.supervisor.rows and clamped_cols == self.supervisor.cols:
            return
        self.supervisor.resize(clamped_rows, clamped_cols)

    # ---------------------------------------------------------------- actions

    def status_pill(self) -> str:
        """Recording badge drawn into the frame."""
        if self._paused:
            return "⏸ PAUSED"
        seconds = int(self.frames_written / self.fps)
        return f"● REC {seconds // 60:02d}:{seconds % 60:02d}"

    def status_color(self) -> Tuple[float, float, float]:
        theme = self.renderer.theme
        return theme.traffic_minimize if self._paused else theme.traffic_close

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        """Stop writing frames. The child keeps running."""
        with self._state_lock:
            if self._paused:
                return
            self._paused = True
            self._pause_started_at = time.monotonic()
        self._set_terminal_title("⏸ PAUSED – termreel")
        self._log("Paused. Nothing is being recorded.")

    def resume(self) -> None:
        """Resume writing frames, queueing a crossfade over the cut."""
        with self._state_lock:
            if not self._paused:
                return
            self._paused = False
            if self._pause_started_at is not None:
                self.paused_seconds += time.monotonic() - self._pause_started_at
                self._pause_started_at = None
            if self.crossfade > 0 and self._last_frame is not None:
                # Handed to the frame thread. Blending here would block the
                # keystroke path behind an 8 MB composite.
                self._pending_transition = PendingTransition(
                    from_frame=self._last_frame, duration=self.crossfade
                )
        self._set_terminal_title("● REC – termreel")
        self._log("Recording.")

    def toggle_pause(self) -> None:
        if self._paused:
            self.resume()
        else:
            self.pause()

    def mark(self) -> None:
        """Record a chapter timestamp on the video timeline."""
        position = self.frames_written / float(self.fps)
        self.marks.append(position)
        self._log(f"Marked {position:.2f}s")

    def stop(self) -> None:
        """Request shutdown of both the frame thread and the input loop."""
        self._stop_event.set()
        passthrough = self._passthrough
        if passthrough is not None:
            passthrough.request_stop()

    def _on_terminate(self, signum: int) -> None:  # pragma: no cover - signal path
        """
        SIGTERM/SIGHUP handler body, installed by TerminalGuard.

        Requests exactly the same shutdown the stop hotkey does rather than
        exiting from the handler. Exiting there would skip finalising the
        encoder, flushing the asciicast tail and killing the recorded child's
        process group -- a `sleep 600 &` started during the session would be
        left reparented to init.

        Waking the passthrough loop matters as well as setting the flag:
        PEP 475 retries an interrupted select(), so a handler that only sets a
        flag would leave the loop blocked forever. ``stop`` writes to the
        loop's wake pipe.
        """
        self.stop()

    def handle_action(self, action: str) -> None:
        if action == ACTION_TOGGLE_PAUSE:
            self.toggle_pause()
        elif action == ACTION_PAUSE:
            self.pause()
        elif action == ACTION_RESUME:
            self.resume()
        elif action == ACTION_MARK:
            self.mark()
        elif action == ACTION_STOP:
            self._log("Stopping.")
            self.stop()
        elif action == ACTION_HELP:
            # An explicit request from the operator, so it is not status noise
            # that --quiet should swallow.
            self._log(self.help_line(), force=True)
        elif action == ACTION_UNKNOWN:
            self._log(f"Unbound key. {self.help_line()}", force=True)

    def help_line(self) -> str:
        label = self.prefix.label if self.prefix is not None else "^T"
        return (
            f"{label} p pause/resume   {label} m mark   "
            f"{label} q stop   {label} {label} literal"
        )

    # ----------------------------------------------------------- frame thread

    def _render_frame(self) -> bytes:
        with self.state._lock:
            self.redactor.apply_to_terminal_state(self.state)
        return self.renderer.draw_frame(
            term_state=self.state,
            status_pill=self.status_pill(),
            status_color=self.status_color(),
            status_left=f"{self.title} | {self.cols}x{self.rows} | UTF-8",
            status_right=f"TermReel Live {self.fps}fps",
        )

    def _emit(self, frame: bytes) -> None:
        if self.pipe is None or not self.pipe.is_open:
            return
        self.pipe.write_frame(frame)
        self.frames_written = self.pipe.frame_count
        if self.telemetry is not None:
            self.telemetry.update_rendered_frame(
                rendered_frames=self.frames_written,
                elapsed_seconds=self.frames_written / float(self.fps),
            )

    def _emit_transition(self, transition: PendingTransition, target: bytes) -> None:
        """
        Alpha-blend the last pre-pause frame into the first post-resume frame.

        Streams straight into the existing pipe: no temp files, no second
        encode pass. A paused-overlay caption can never show up in the output
        because paused frames are never written, so this fade *is* the
        in-video indication that time was cut.
        """
        steps = int(round(transition.duration * self.fps))
        if steps <= 0:
            return
        for index in range(1, steps + 1):
            # Fade the old frame out: alpha 1 -> 0 across the transition.
            alpha = 1.0 - (index / float(steps + 1))
            self._emit(blend_frames(target, transition.from_frame, alpha, self.width, self.height))

    def _frame_loop(self) -> None:
        """
        Deadline-paced encoder feed.

        Pacing matters: ``time.sleep`` never returns early and the naive
        "sleep(interval - elapsed)" loop floors its wait, so its period is
        always >= the interval and the resulting video is systematically
        shorter than the session. Advancing a fixed deadline removes the drift.
        """
        interval = 1.0 / float(self.fps)
        next_deadline = time.monotonic() + interval

        while not self._stop_event.is_set():
            delay = next_deadline - time.monotonic()
            if delay > 0:
                if self._stop_event.wait(delay):
                    break
            elif delay < -interval * 10:
                # Fell far behind (encoder backpressure or a long stall).
                # Resynchronise rather than burning through a backlog of
                # deadlines at full speed.
                next_deadline = time.monotonic()
            next_deadline += interval

            if self.supervisor is not None and not self.supervisor.is_alive():
                # The recorded shell exited on its own (the operator typed
                # `exit`). Bring the input loop down with us.
                self.stop()
                break

            now = time.monotonic()
            if now >= self._next_winsize_check:
                # Deliberately polled rather than driven by a SIGWINCH handler:
                # signal.signal() is only legal on the main thread, and the
                # recorder is run off-thread by the tests and by anything
                # embedding it. Runs while paused too, so a resize during a
                # pause is not lost. A human resizing a window does not notice
                # half a second.
                self._next_winsize_check = now + WINSIZE_POLL_SECONDS
                try:
                    self._apply_window_size()
                except Exception as exc:
                    self._log(f"Resize failed: {type(exc).__name__}: {exc}")

            with self._state_lock:
                if self._paused:
                    continue
                transition = self._pending_transition
                self._pending_transition = None

            try:
                frame = self._render_frame()
                if transition is not None:
                    self._emit_transition(transition, frame)
                self._emit(frame)
                self._last_frame = frame
            except Exception as exc:
                # The scenario capture loop swallows these with a bare pass,
                # which is how a silently truncated recording happens. Count
                # and sample them instead.
                self.frame_errors += 1
                if len(self.frame_error_samples) < 5:
                    self.frame_error_samples.append(f"{type(exc).__name__}: {exc}")
                    self._log(f"Frame error: {type(exc).__name__}: {exc}")

    # -------------------------------------------------------------- lifecycle

    def _start_telemetry(self) -> None:
        if not self.enable_telemetry:
            return
        try:
            self.telemetry_registry = SessionRegistry()
            session_dir = os.path.join(self.telemetry_registry.directory, self.session_id)
            socket_path = os.path.join(session_dir, "telemetry.sock")
            if len(socket_path) > 100:
                socket_path = f"/tmp/tr_{self.session_id}.sock"
            metadata = SessionMetadata(
                session_id=self.session_id,
                pid=os.getpid(),
                scenario_title=self.title,
                output_video=self.output_file,
                started_at=time.time(),
                fps=self.fps,
                socket_path=socket_path,
                status="running",
            )
            # Its own renderer: CairoTerminalRenderer reuses one ImageSurface,
            # so sharing would let `peek --image` tear a video frame.
            telemetry_renderer = CairoTerminalRenderer(
                width=self.width,
                height=self.height,
                title=self.title,
                subtitle=self.subtitle,
                theme=self.theme_name,
            )
            self.telemetry = TelemetryServer(
                session_id=self.session_id,
                state=self.state,
                renderer=telemetry_renderer,
                metadata=metadata,
                registry=self.telemetry_registry,
                controller=self,
            )
            self.telemetry_registry.register(metadata)
            self.telemetry.start()
        except Exception as exc:
            self._log(f"Telemetry unavailable ({exc}); continuing without it.")
            self.telemetry = None

    def preflight(self) -> None:
        """Validate the environment before touching the terminal."""
        if not os.isatty(self.stdin_fd):
            raise TermReelError(
                "termreel live needs an interactive terminal on stdin. "
                "Run it directly in a terminal, not through a pipe or a CI job."
            )
        if shutil.which("ffmpeg") is None:
            raise TermReelError("ffmpeg binary not found in PATH.")

        extension = os.path.splitext(self.output_file)[1].lower()
        if extension == ".gif":
            self._log(
                "Warning: .gif output buffers the whole stream through a palettegen "
                "filtergraph, so nothing is written until you stop. Prefer .mp4."
            )
        elif extension not in (".mp4", ".webm", ".mov", ".mkv"):
            self._log(f"Unrecognised output extension '{extension}'; encoding as H.264 anyway.")

        if self.width * self.height > LARGE_CANVAS_PIXELS:
            self._log(
                f"Canvas is {self.width}x{self.height} ({self.cols}x{self.rows}). "
                f"At this size frame production alone uses a large share of the frame "
                f"budget and the encoder may apply backpressure. Consider --cols/--rows."
            )

    def run(self) -> LiveReport:
        """Record until the operator stops or the child exits."""
        self.preflight()
        error_message: Optional[str] = None
        self._started_at = time.monotonic()

        try:
            self.pipe = FFmpegPipe(
                output_file=self.output_file,
                width=self.width,
                height=self.height,
                fps=self.fps,
                crf=self.crf,
                preset=self.preset,
            )
            self.pipe.open()

            if self.cast_path:
                self.asciicast = AsciicastRecorder(
                    filepath=self.cast_path,
                    width=self.cols,
                    height=self.rows,
                    title=self.title,
                    redactor=self.redactor,
                    # Video time, not wall time: paused segments are cut from
                    # the video, so a wall clock would desynchronise the cast
                    # from the footage it is supposed to accompany.
                    clock=lambda: self.frames_written / float(self.fps),
                )
                self.asciicast.start()

            self.supervisor = PtySupervisor(
                command=self.command,
                cwd=self.cwd,
                rows=self.rows,
                cols=self.cols,
                state=self.state,
                parser=self.parser,
                on_output=self._on_child_output,
            )
            self.supervisor.start()
            # Silent here: the screen is cleared a few lines below, so any
            # warning printed now would be wiped before it could be read. The
            # frame thread's first poll re-checks and reports it after the
            # banner is up.
            self._apply_window_size(warn=False)

            self._start_telemetry()

            prefix_byte = self.prefix.byte if self.prefix is not None else b"\x14"
            fsm = PrefixFSM(prefix_byte, dict(DEFAULT_BINDINGS))

            # Raw mode goes on before anything is painted: with ONLCR still
            # enabled the banner's CRLFs reach the terminal as "\r\r\n", and
            # the status logger needs to know whether it must emit CRLF.
            self._guard = TerminalGuard(self.stdin_fd, on_terminate=self._on_terminate)
            with self._guard:
                # Fresh screen so the operator's view matches the empty grid
                # being recorded. Deliberately NOT the alternate screen buffer:
                # unlike `peek`, live forwards the child's own bytes, so a child
                # entering the alt buffer (vim, htop) would desynchronise the
                # outer terminal and could destroy the operator's scrollback.
                self._write_to_operator(b"\x1b[2J\x1b[3J\x1b[H")
                self._set_terminal_title("● REC – termreel")
                self._banner()

                self._frame_thread = threading.Thread(
                    target=self._frame_loop, name="termreel-live-frames", daemon=True
                )
                self._frame_thread.start()

                self._passthrough = PassthroughLoop(
                    stdin_fd=self.stdin_fd,
                    write_to_child=self.supervisor.send_raw,
                    fsm=fsm,
                    on_action=self.handle_action,
                )
                self._passthrough.run()
                if self._passthrough.error is not None:
                    raise self._passthrough.error

        except BaseException as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, Exception):
                raise
        finally:
            self._shutdown()

        wall = time.monotonic() - self._started_at
        size = os.path.getsize(self.output_file) if os.path.exists(self.output_file) else 0

        return LiveReport(
            status="error" if error_message else "pass",
            output_file=self.output_file,
            frames_written=self.frames_written,
            fps=self.fps,
            video_seconds=self.frames_written / float(self.fps),
            wall_seconds=wall,
            paused_seconds=self.paused_seconds,
            file_size_bytes=size,
            cols=self.cols,
            rows=self.rows,
            marks=list(self.marks),
            cast_file=self.cast_path,
            error_message=error_message,
            frame_errors=self.frame_errors,
        )

    def _banner(self) -> None:
        label = self.prefix.label if self.prefix is not None else "^T"
        spec = self.prefix.spec if self.prefix is not None else "C-t"
        source = self.prefix.source if self.prefix is not None else "default"
        lines = [
            f"\x1b[1m● Recording\x1b[0m — drive your terminal normally.\r\n",
            f"  {label} p  pause/resume     {label} m  mark     {label} q  stop\r\n",
            f"  prefix {spec} (from {source}); press {label} {label} to type a literal {label}.\r\n",
            f"  {self.cols}x{self.rows} @ {self.fps}fps -> {self.output_file}\r\n\r\n",
        ]
        for line in lines:
            self._write_to_operator(line.encode("utf-8"))

    def _shutdown(self) -> None:
        self._stop_event.set()

        # Close an open pause interval. Stopping while paused is ordinary --
        # pause, decide the take is done, stop -- and without this the final
        # interval never lands in paused_seconds, so the report understates
        # (or, for a single trailing pause, entirely omits) the time cut out.
        with self._state_lock:
            if self._paused and self._pause_started_at is not None:
                self.paused_seconds += time.monotonic() - self._pause_started_at
                self._pause_started_at = None

        if self._frame_thread is not None and self._frame_thread.is_alive():
            self._frame_thread.join(timeout=3.0)

        if self._passthrough is not None:
            self._passthrough.stop(join_timeout=0.5)

        if self.supervisor is not None:
            try:
                self.supervisor.terminate()
            except Exception:
                pass

        if self.telemetry is not None:
            try:
                self.telemetry.stop(status="completed")
            except Exception:
                pass
        if self.telemetry_registry is not None:
            try:
                self.telemetry_registry.unregister(self.session_id)
            except Exception:
                pass

        if self.asciicast is not None:
            try:
                self.asciicast.close()
            except Exception:
                pass

        if self.pipe is not None:
            try:
                self.pipe.close()
            except Exception as exc:
                self._log(f"Encoder shutdown problem: {exc}")

        if self._guard is not None:
            self._guard.restore()

        # Restore cursor visibility and scrolling region regardless of what
        # the child left behind. In raw mode termreel has no Ctrl-C of its
        # own, so this is the only path back to a usable terminal.
        try:
            os.write(self.stdout_fd, b"\x1b[?25h\x1b[r")
            if self._title_set:
                os.write(self.stdout_fd, b"\x1b]0;\x07")
            os.write(self.stdout_fd, b"\r\n")
        except OSError:
            pass
