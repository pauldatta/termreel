"""
Streaming redaction for terminal output written to .cast files.

The PTY delivers output in arbitrary chunks, and applications interleave
text with escape sequences. Redacting each chunk on its own misses a secret
that is split across two reads (``sk-pro`` | ``j-...``), or broken up by SGR
codes (``gh\\x1b[1mp_...``), or typed one character at a time at a prompt.

``StreamRedactor`` keeps a small hold-back buffer:

* an escape sequence that has not finished yet;
* the trailing run of token characters (a secret could still be growing);
* a trailing fragment that could be the start of a literal value rule, an
  anchor landmark, or ``Bearer``.

Everything before the hold-back point is matched on its visible text (escape
sequences removed, positions mapped back) and emitted with the escapes kept.
The held text is released when more output arrives or when ``flush`` is
called after a short idle period, so the cast lags the screen by at most the
idle interval.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

# Characters that can appear inside a token/secret. A run of these at the end
# of the buffer is held back because the next chunk may extend it.
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_\-.=+/:~@%]")
_MAX_HOLD_VISIBLE = 512
_MAX_ESCAPE = 4096

_BEARER_TAIL = re.compile(r"(?i)b(?:e(?:a(?:r(?:e(?:r\s*)?)?)?)?)?$")


def _scan_escape(text: str, i: int) -> Optional[int]:
    """
    Return the index just past the escape sequence starting at ``text[i]``
    (an ESC), or None if the sequence is not complete yet.
    """
    n = len(text)
    if i + 1 >= n:
        return None
    nxt = text[i + 1]
    if nxt == "[":
        j = i + 2
        while j < n:
            c = ord(text[j])
            if 0x40 <= c <= 0x7E:
                return j + 1
            if c < 0x20 and text[j] != "\x1b":
                j += 1
                continue
            if text[j] == "\x1b":
                return j  # aborted by a new ESC
            j += 1
        return None
    if nxt in "]PX^_":
        j = i + 2
        while j < n:
            ch = text[j]
            if ch == "\x07" and nxt == "]":
                return j + 1
            if ch == "\x1b":
                if j + 1 >= n:
                    return None
                return j + 2 if text[j + 1] == "\\" else j
            j += 1
        return None
    j = i + 1
    while j < n and 0x20 <= ord(text[j]) <= 0x2F:
        j += 1
    if j >= n:
        return None
    return j + 1


def split_visible(text: str) -> Tuple[str, List[int], int]:
    """
    Separate visible characters from escape sequences.

    Returns (visible, raw_index_of_each_visible_char, complete_upto) where
    ``complete_upto`` is the raw length up to which escapes are complete.
    """
    visible: List[str] = []
    index: List[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            end = _scan_escape(text, i)
            if end is None:
                if n - i > _MAX_ESCAPE:
                    # Unterminated for too long: treat the ESC as a byte.
                    i += 1
                    continue
                return "".join(visible), index, i
            i = end
            continue
        visible.append(ch)
        index.append(i)
        i += 1
    return "".join(visible), index, n


class StreamRedactor:
    """Chunk-boundary-safe, escape-aware redaction of a text stream."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._buf = ""
        self._buf_time: Optional[float] = None
        self._literals: List[str] = []
        self._refresh_literals()

    def _refresh_literals(self) -> None:
        lits = []
        for v in getattr(self.engine, "values", []) or []:
            if v.match:
                lits.append(v.match)
        for a in getattr(self.engine, "anchors", []) or []:
            if a.after:
                lits.append(a.after)
        self._literals = lits

    @property
    def pending(self) -> str:
        return self._buf

    def _hold_index(self, visible: str) -> int:
        """Index into ``visible`` from which text must be held back."""
        n = len(visible)
        hold = n
        # Trailing token run.
        j = n
        while j > 0 and n - j < _MAX_HOLD_VISIBLE and _TOKEN_CHARS.match(visible[j - 1]):
            j -= 1
        hold = min(hold, j)
        # Tail that could be the start of a literal rule or anchor landmark,
        # or an anchor landmark still waiting for its value.
        for lit in self._literals:
            max_k = min(len(lit), n)
            for k in range(max_k, 0, -1):
                if visible.endswith(lit[:k]):
                    hold = min(hold, n - k)
                    break
            pos = visible.rfind(lit)
            if pos != -1 and not visible[pos + len(lit):].strip(" \t\"'"):
                hold = min(hold, pos)
        m = _BEARER_TAIL.search(visible)
        if m and m.start() < n:
            hold = min(hold, m.start())
        return hold

    def _redact_segment(self, raw: str) -> str:
        visible, index, _ = split_visible(raw)
        if not visible:
            return raw
        spans = self.engine._collect_matches(visible, count=True)
        if not spans:
            return raw
        out: List[str] = []
        prev_raw = 0
        for start, end, repl in spans:
            raw_start = index[start]
            raw_end = index[end - 1] + 1
            out.append(raw[prev_raw:raw_start])
            out.append(repl)
            # Keep escape sequences that were inside the span (colour
            # changes, cursor moves) so the stream stays well-formed.
            inner = raw[raw_start:raw_end]
            vis_positions = set(i - raw_start for i in index[start:end])
            k = 0
            while k < len(inner):
                if k in vis_positions:
                    k += 1
                    continue
                if inner[k] == "\x1b":
                    stop = _scan_escape(inner, k) or len(inner)
                    out.append(inner[k:stop])
                    k = stop
                else:
                    k += 1
            prev_raw = raw_end
        out.append(raw[prev_raw:])
        return "".join(out)

    def feed(self, text: str, now: float) -> List[Tuple[float, str]]:
        """Add output; return (timestamp, redacted_text) pieces safe to write."""
        if not text:
            return []
        if not self._buf:
            self._buf_time = now
        self._buf += text
        visible, index, complete = split_visible(self._buf)
        hold_vis = self._hold_index(visible)
        cut = index[hold_vis] if hold_vis < len(index) else complete
        cut = min(cut, complete)
        if cut <= 0:
            return []
        ready, self._buf = self._buf[:cut], self._buf[cut:]
        ts = self._buf_time if self._buf_time is not None else now
        self._buf_time = now if self._buf else None
        return [(ts, self._redact_segment(ready))]

    def flush(self, now: Optional[float] = None) -> List[Tuple[float, str]]:
        """Release everything held back (idle timeout or end of stream)."""
        if not self._buf:
            return []
        ready, self._buf = self._buf, ""
        ts = self._buf_time if self._buf_time is not None else (now or 0.0)
        self._buf_time = None
        return [(ts, self._redact_segment(ready))]

    def held_since(self) -> Optional[float]:
        return self._buf_time if self._buf else None
