"""
Bi-directional event bridge connecting Antigravity (agy) hooks with TermReel.

Provides thread-safe event ingestion, JSONL tailing, event queries, and
deterministic state synchronization (wait_for_event, assertions).
"""

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from termreel.hooks.models import HookEvent, HookEventType


class AgyHookBridge:
    """
    Manages live event stream from Antigravity hooks to TermReel scenario runners.
    """

    def __init__(self, events_file: Optional[str] = None):
        self.events_file = events_file
        self.events: List[HookEvent] = []
        self._listeners: List[Callable[[HookEvent], None]] = []
        self._lock = threading.Lock()
        self._is_running = False
        self._tail_thread: Optional[threading.Thread] = None
        self._file_offset = 0

    def start(self):
        """Start the background file tailing thread if an events file is specified."""
        if self._is_running:
            return

        self._is_running = True
        self._file_offset = 0

        # Pre-create empty events file if needed
        if self.events_file:
            os.makedirs(os.path.dirname(os.path.abspath(self.events_file)), exist_ok=True)
            if not os.path.exists(self.events_file):
                with open(self.events_file, "w", encoding="utf-8"):
                    pass

            self._tail_thread = threading.Thread(target=self._tail_loop, daemon=True)
            self._tail_thread.start()

    def stop(self):
        """Stop background event tailing and flush remaining events."""
        self._is_running = False
        if self._tail_thread and self._tail_thread.is_alive():
            self._tail_thread.join(timeout=1.0)
        self.read_new_events()

    def add_listener(self, listener: Callable[[HookEvent], None]):
        """Register a callback for newly received hook events."""
        with self._lock:
            self._listeners.append(listener)

    def record_event(self, event: HookEvent):
        """Record an event in-memory and write to JSONL log file."""
        with self._lock:
            self.events.append(event)
            listeners = list(self._listeners)

            if self.events_file:
                try:
                    with open(self.events_file, "a", encoding="utf-8") as f:
                        f.write(event.to_json() + "\n")
                        f.flush()
                        self._file_offset = f.tell()
                except Exception:
                    pass

        for cb in listeners:
            try:
                cb(event)
            except Exception:
                pass

    def read_new_events(self) -> List[HookEvent]:
        """Read any unread JSONL lines from the events file and dispatch them."""
        if not self.events_file or not os.path.exists(self.events_file):
            return []

        new_events = []
        with self._lock:
            try:
                with open(self.events_file, "r", encoding="utf-8") as f:
                    f.seek(self._file_offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = HookEvent.from_json(line)
                            new_events.append(event)
                        except Exception:
                            pass
                    self._file_offset = f.tell()
            except Exception:
                pass

            if new_events:
                self.events.extend(new_events)
                listeners = list(self._listeners)
            else:
                listeners = []

        for ev in new_events:
            for cb in listeners:
                try:
                    cb(ev)
                except Exception:
                    pass

        return new_events

    def _tail_loop(self):
        """Continuous background poll for new lines written to the events file."""
        while self._is_running:
            self.read_new_events()
            time.sleep(0.05)

    def get_events(
        self,
        event_type: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> List[HookEvent]:
        """Query captured hook events by event type and/or tool name."""
        self.read_new_events()
        with self._lock:
            evs = list(self.events)

        norm_type = HookEventType.from_string(event_type).value if event_type else None

        filtered = []
        for ev in evs:
            if norm_type:
                try:
                    ev_norm = HookEventType.from_string(ev.event_type).value
                    if ev_norm != norm_type:
                        continue
                except Exception:
                    if ev.event_type.lower() != event_type.lower():
                        continue

            if tool_name and ev.tool_name != tool_name:
                continue

            filtered.append(ev)

        return filtered

    def wait_for_event(
        self,
        event_type: str,
        tool_name: Optional[str] = None,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
    ) -> Optional[HookEvent]:
        """
        Poll until a matching hook event occurs or timeout expires.
        Returns the first matching HookEvent, or None if timed out.
        """
        start_t = time.time()
        while time.time() - start_t < timeout:
            matches = self.get_events(event_type=event_type, tool_name=tool_name)
            if matches:
                return matches[-1]
            time.sleep(poll_interval)
        return None

    def assert_event_present(
        self,
        event_type: str,
        tool_name: Optional[str] = None,
        timeout: float = 5.0,
    ):
        """Assert that a hook event has been observed within the timeout window."""
        ev = self.wait_for_event(event_type=event_type, tool_name=tool_name, timeout=timeout)
        if not ev:
            all_evs = [f"{e.event_type}({e.tool_name or ''})" for e in self.events]
            raise AssertionError(
                f"Expected hook event '{event_type}' (tool={tool_name}) not observed within {timeout}s.\n"
                f"Recorded hook events: {all_evs}"
            )

    def assert_event_absent(
        self,
        event_type: str,
        tool_name: Optional[str] = None,
        timeout: float = 2.0,
    ):
        """Assert that a specific hook event did NOT occur."""
        time.sleep(timeout)
        matches = self.get_events(event_type=event_type, tool_name=tool_name)
        if matches:
            raise AssertionError(
                f"Forbidden hook event '{event_type}' (tool={tool_name}) was observed: {matches}"
            )

    def clear(self):
        """Reset internal event buffer and file offset."""
        with self._lock:
            self.events.clear()
            self._file_offset = 0
