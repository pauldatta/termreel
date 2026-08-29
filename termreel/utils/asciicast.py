"""
Asciinema v2 (.cast) recording and playback support.
"""

import json
import os
import time
from typing import Optional, Dict, List, Tuple, Generator, Any


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
    ):
        self.filepath = filepath
        self.width = width
        self.height = height
        self.title = title
        self.env = env or {"TERM": "xterm-256color", "COLORTERM": "truecolor"}

        self.file = None
        self.start_time: Optional[float] = None
        self.event_count = 0

    def start(self):
        """Open file and write Asciinema v2 header."""
        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        self.file = open(self.filepath, "w", encoding="utf-8")
        self.start_time = time.time()

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

    def record_output(self, data: str):
        """Record an stdout event."""
        self.record_event("o", data)

    def record_input(self, data: str):
        """Record an stdin event."""
        self.record_event("i", data)

    def record_event(self, event_type: str, data: str):
        """Record a generic event line: [elapsed_sec, type, data]."""
        if not self.file or self.start_time is None:
            return
        elapsed = round(time.time() - self.start_time, 6)
        line = json.dumps([elapsed, event_type, data])
        self.file.write(line + "\n")
        self.file.flush()
        self.event_count += 1

    def close(self):
        """Close the asciicast file."""
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
