"""
Asciinema v2 (.cast) recording and playback support.
"""

import codecs
import json
import os
import threading
import time
from typing import Optional, Dict, List, Tuple, Generator, Any, Callable


class AsciicastRecorder:
    """
    Records terminal session I/O into standard Asciinema v2 (.cast) format.
    """

    def __init__(
        self,
        filepath: str,
        width: int = 100,
        height: int = 30,
        title: str = "TermReel Session",
        env: Optional[Dict[str, str]] = None,
        redactor: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
        flush_interval: float = 0.5,
    ):
        self.filepath = filepath
        self.width = width
        self.height = height
        self.title = title
        self.env = env or {"TERM": "xterm-256color", "COLORTERM": "truecolor"}

        # Redaction is applied to the rendered grid elsewhere; without this the
        # .cast stream is a verbatim, unmasked copy of everything the child
        # printed, secrets included.
        if redactor is None:
            from termreel.utils.redaction import Redactor
            self.redactor = Redactor(load_global_config=True)
        elif redactor is False:
            self.redactor = None
        else:
            self.redactor = redactor

        # Elapsed-time source. Defaults to wall clock since start(). A live
        # recording that cuts paused segments out of the video passes its own
        # clock so cast timestamps stay in sync with the trimmed footage.
        self.clock = clock

        self.flush_interval = max(0.0, flush_interval)

        self.file = None
        self.start_time: Optional[float] = None
        self.event_count = 0

        # Incremental decoder: a UTF-8 character can straddle two reads of the
        # PTY master, and a plain bytes.decode() per chunk mangles it.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._last_flush = 0.0
        self._last_ts = 0.0
        self._lock = threading.RLock()
        self._stream = None
        if self.redactor is not None and hasattr(self.redactor, "_collect_matches"):
            from termreel.mask.stream import StreamRedactor
            self._stream = StreamRedactor(self.redactor)

    def start(self):
        """Open file and write Asciinema v2 header."""
        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        self.file = open(self.filepath, "w", encoding="utf-8")
        self.start_time = time.time()
        self._last_flush = self.start_time

        header = {
            "version": 2,
            "width": self.width,
            "height": self.height,
            "timestamp": int(self.start_time),
            "title": self.title,
            "env": self.env,
        }
        self.file.write(json.dumps(header) + "\n")
        self.file.flush()

    def elapsed(self) -> float:
        """Timestamp for the next event, in seconds since recording started."""
        if self.clock is not None:
            return max(0.0, float(self.clock()))
        if self.start_time is None:
            return 0.0
        return max(0.0, time.time() - self.start_time)

    def record_output(self, data: str, already_redacted: bool = False):
        """
        Record an stdout event.

        ``already_redacted`` is for text built from a masked screen snapshot
        (tmux-backend redraws). Running value rules over it again would
        re-mask replacements that contain the original text.
        """
        if already_redacted:
            self._flush_stream()
            self._write_event("o", data)
            return
        self._record_stream(data)

    def record_output_bytes(self, data: bytes):
        """
        Record an stdout event from raw PTY bytes.

        Decoding is stateful across calls, so a character split across chunk
        boundaries is emitted once, whole, with the chunk that completes it.
        """
        if not data:
            return
        with self._lock:
            text = self._decoder.decode(data)
        if text:
            self._record_stream(text)

    def record_input(self, data: str):
        """Record an stdin event."""
        self.record_event("i", data)

    def _record_stream(self, text: str) -> None:
        if not self.file or self.start_time is None:
            return
        if self._stream is None:
            if self.redactor is not None:
                text = self.redactor.redact_text(text)
            self._write_event("o", text)
            return
        with self._lock:
            pieces = self._stream.feed(text, self.elapsed())
        for ts, piece in pieces:
            self._write_event("o", piece, ts=ts)

    def _flush_stream(self) -> None:
        if self._stream is None:
            return
        with self._lock:
            pieces = self._stream.flush(self.elapsed())
        for ts, piece in pieces:
            self._write_event("o", piece, ts=ts)

    def tick(self, idle: float = 0.25) -> None:
        """
        Release output held back for redaction once it has been idle for
        ``idle`` seconds. Call this from the frame loop.
        """
        if self._stream is None:
            return
        with self._lock:
            since = self._stream.held_since()
        if since is not None and self.elapsed() - since >= idle:
            self._flush_stream()

    def flush(self) -> None:
        """Release everything held back for redaction now."""
        self._flush_stream()

    def resync(self, screen: str) -> None:
        """
        Continue the stream from a known screen after a gap.

        Used when output was deliberately not recorded (``live`` pause):
        any partial UTF-8 sequence from before the gap is discarded, held
        text is flushed, and ``screen`` (a full redraw built from a masked
        snapshot) is written so a player shows exactly what is on screen.
        """
        with self._lock:
            self._decoder.reset()
        self._flush_stream()
        self.record_output(screen, already_redacted=True)

    def record_event(self, event_type: str, data: str):
        """Record a generic event line: [elapsed_sec, type, data]."""
        if not self.file or self.start_time is None:
            return
        if event_type == "o":
            self._record_stream(data)
            return
        if self.redactor is not None:
            data = self.redactor.redact_text(data)
        self._write_event(event_type, data)

    def _write_event(self, event_type: str, data: str, ts: Optional[float] = None) -> None:
        if not data:
            return
        elapsed = round(self.elapsed() if ts is None else ts, 6)
        with self._lock:
            if not self.file:
                return
            # Keep timestamps monotonic even when held text is released late.
            elapsed = max(elapsed, self._last_ts)
            self._last_ts = elapsed
            self.file.write(json.dumps([elapsed, event_type, data]) + "\n")
            self.event_count += 1
            now = time.time()
            if self.flush_interval <= 0.0 or (now - self._last_flush) >= self.flush_interval:
                self.file.flush()
                self._last_flush = now

    def close(self):
        """Flush the decoder and the redaction hold-back, then close the file."""
        with self._lock:
            if not self.file:
                return
            # Drain any bytes the decoder is still holding for a partial
            # character so a truncated tail is not silently dropped.
            try:
                tail = self._decoder.decode(b"", final=True)
            except Exception:
                tail = ""
        if tail:
            self._record_stream(tail)
        self._flush_stream()
        with self._lock:
            if self.file:
                self.file.close()
                self.file = None


class AsciicastPlayer:
    """
    Reads and replays an Asciinema v2 (.cast) file.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.header: Dict[str, Any] = {}
        self.events: List[Tuple[float, str, str]] = []
        self._load()

    def _load(self):
        with open(self.filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            raise ValueError(f"Empty asciicast file: {self.filepath}")

        self.header = json.loads(lines[0])
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, list) and len(item) >= 3:
                self.events.append((float(item[0]), str(item[1]), str(item[2])))

    @property
    def width(self) -> int:
        return self.header.get("width", 100)

    @property
    def height(self) -> int:
        return self.header.get("height", 30)

    @property
    def duration(self) -> float:
        if not self.events:
            return 0.0
        return self.events[-1][0]

    def iter_events(self) -> Generator[Tuple[float, str, str], None, None]:
        for ev in self.events:
            yield ev
