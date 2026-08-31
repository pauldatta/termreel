"""
Session registry for discovering, inspecting, and managing active and historical TermReel sessions.
"""

import json
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from termreel.telemetry.models import SessionMetadata


class SessionRegistry:
    """
    Manages session metadata files on the local filesystem.
    Provides registration, atomic updates, liveness probing, and stale session cleanup.
    """

    def __init__(self, directory: Optional[str] = None):
        if directory:
            self.directory = os.path.abspath(directory)
            os.makedirs(self.directory, exist_ok=True)
        else:
            default_dir = os.path.expanduser("~/.termreel/sessions")
            try:
                os.makedirs(default_dir, exist_ok=True)
                self.directory = default_dir
            except OSError:
                fallback_dir = "/tmp/termreel_sessions"
                os.makedirs(fallback_dir, exist_ok=True)
                self.directory = fallback_dir

    def _session_file(self, session_id: str) -> str:
        clean_id = session_id.strip()
        if clean_id.startswith("session_"):
            clean_id = clean_id[len("session_"):]
        if clean_id.endswith(".json"):
            clean_id = clean_id[:-len(".json")]
        return os.path.join(self.directory, f"session_{clean_id}.json")

    def _write_atomic(self, filepath: str, data: Dict[str, Any]):
        tmp_path = f"{filepath}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)

    def register(self, metadata: SessionMetadata) -> str:
        """
        Register a new session by writing session_<id>.json atomically.
        Returns the registered session_id.
        """
        filepath = self._session_file(metadata.session_id)
        self._write_atomic(filepath, metadata.to_dict())
        return metadata.session_id

    def update(self, session_id: str, **kwargs) -> None:
        """
        Update session metadata fields atomically.
        """
        filepath = self._session_file(session_id)
        if not os.path.exists(filepath):
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"session_id": session_id}

        data.update(kwargs)
        self._write_atomic(filepath, data)

    def unregister(self, session_id: str, remove_file: bool = False) -> None:
        """
        Unregister session: marks status as completed (or removes file if remove_file=True).
        """
        filepath = self._session_file(session_id)
        if remove_file:
            if os.path.exists(filepath):
                try:
                    os.unlink(filepath)
                except OSError:
                    pass
        else:
            if os.path.exists(filepath):
                self.update(session_id, status="completed")

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Probe whether a process PID is currently active."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Process exists but under different credentials
        except OSError:
            return False

    def list_sessions(self, active_only: bool = True) -> List[SessionMetadata]:
        """
        List all sessions. If active_only is True, filters for status='running' and alive PID.
        Returns sessions ordered by started_at descending.
        """
        sessions = []
        if not os.path.isdir(self.directory):
            return sessions

        for fname in os.listdir(self.directory):
            if fname.startswith("session_") and fname.endswith(".json"):
                fpath = os.path.join(self.directory, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    meta = SessionMetadata.from_dict(data)
                except Exception:
                    continue

                alive = self._is_pid_alive(meta.pid)
                if active_only:
                    if meta.status == "running" and alive:
                        sessions.append(meta)
                else:
                    sessions.append(meta)

        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions

    def get_latest_session(self) -> Optional[SessionMetadata]:
        """
        Retrieve the most recent active session, or None if no active sessions exist.
        """
        active = self.list_sessions(active_only=True)
        return active[0] if active else None

    def get_session(self, session_id_or_pid: str) -> Optional[SessionMetadata]:
        """
        Lookup a session by session ID or process PID.
        """
        target = str(session_id_or_pid).strip()

        # Check direct file lookup
        direct_path = self._session_file(target)
        if os.path.exists(direct_path):
            try:
                with open(direct_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return SessionMetadata.from_dict(data)
            except Exception:
                pass

        # Search all sessions (including completed/inactive)
        for s in self.list_sessions(active_only=False):
            if s.session_id == target or str(s.pid) == target:
                return s

        return None

    def prune_stale(self) -> int:
        """
        Clean up records where the associated process is no longer running.
        Returns count of pruned sessions.
        """
        pruned_count = 0
        if not os.path.isdir(self.directory):
            return 0

        for fname in os.listdir(self.directory):
            if fname.startswith("session_") and fname.endswith(".json"):
                fpath = os.path.join(self.directory, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pid = int(data.get("pid", 0))
                    session_id = str(data.get("session_id", ""))
                    sock_path = str(data.get("socket_path", ""))
                except Exception:
                    pid = 0
                    session_id = ""
                    sock_path = ""

                if not self._is_pid_alive(pid):
                    try:
                        os.unlink(fpath)
                        pruned_count += 1
                    except OSError:
                        pass

                    # Clean up associated session folder if present
                    if session_id:
                        s_dir = os.path.join(self.directory, session_id)
                        if os.path.isdir(s_dir):
                            try:
                                shutil.rmtree(s_dir)
                            except OSError:
                                pass

                    # Clean up associated socket if present
                    if sock_path and os.path.exists(sock_path):
                        try:
                            os.unlink(sock_path)
                        except OSError:
                            pass

        return pruned_count
