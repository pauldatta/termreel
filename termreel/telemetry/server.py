"""
TelemetryServer: UNIX domain socket JSON-RPC IPC server and atomic fallback file writer.
"""

import base64
import io
import json
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from termreel.emulator.state import TerminalState
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.telemetry.models import SessionMetadata, ScreenSnapshot
from termreel.telemetry.registry import SessionRegistry


class TelemetryServer:
    """
    Telemetry IPC Server binding a UNIX domain socket and streaming live terminal metrics.
    Handles JSON-RPC requests (GET_STATUS, GET_SCREEN, GET_RAW, SUBSCRIBE, CAPTURE_IMAGE)
    and maintains atomic file fallbacks (screen.ansi and status.json).
    """

    def __init__(
        self,
        session_id: str,
        state: TerminalState,
        renderer: Optional[CairoTerminalRenderer] = None,
        metadata: Optional[SessionMetadata] = None,
        registry: Optional[SessionRegistry] = None,
        session_dir: Optional[str] = None,
    ):
        self.session_id = session_id
        self.state = state
        self.renderer = renderer
        self.registry = registry if registry is not None else SessionRegistry()

        if metadata is not None:
            self.metadata = metadata
        else:
            self.metadata = SessionMetadata(
                session_id=session_id,
                pid=os.getpid(),
            )

        # Resolve session directory
        if session_dir:
            self.session_dir = os.path.abspath(session_dir)
        elif self.metadata.socket_path:
            self.session_dir = os.path.dirname(os.path.abspath(self.metadata.socket_path))
        else:
            self.session_dir = os.path.join(self.registry.directory, self.session_id)

        os.makedirs(self.session_dir, exist_ok=True)

        # Resolve socket path (ensure within UNIX domain socket length limit)
        if not self.metadata.socket_path:
            sock_p = os.path.join(self.session_dir, "telemetry.sock")
            if len(sock_p) > 100:
                sock_p = f"/tmp/tr_{self.session_id}.sock"
            self.metadata.socket_path = sock_p

        self.socket_path = self.metadata.socket_path
        os.makedirs(os.path.dirname(os.path.abspath(self.socket_path)), exist_ok=True)

        self._running = False
        self._server_socket: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._flush_thread: Optional[threading.Thread] = None
        self._clients: List[socket.socket] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the UNIX domain socket server and background threads."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

        # Clean up existing socket file if present
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self.socket_path)
        self._server_socket.listen(16)
        self._server_socket.settimeout(0.5)

        self._server_thread = threading.Thread(
            target=self._listen_loop,
            name=f"telemetry-listen-{self.session_id}",
            daemon=True,
        )
        self._server_thread.start()

        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name=f"telemetry-flush-{self.session_id}",
            daemon=True,
        )
        self._flush_thread.start()

        # Write initial fallback files
        self.write_fallback_files()

    def write_fallback_files(self) -> None:
        """
        Atomically write screen.ansi and status.json fallback files in session dir.
        Also updates registry if configured.
        """
        try:
            with self._lock:
                status_dict = self.metadata.to_dict()

            snap = ScreenSnapshot.from_terminal_state(self.state)

            # 1. status.json
            status_path = os.path.join(self.session_dir, "status.json")
            tmp_status = f"{status_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_status, "w", encoding="utf-8") as f:
                json.dump(status_dict, f, indent=2)
            os.replace(tmp_status, status_path)

            # 2. screen.ansi
            ansi_path = os.path.join(self.session_dir, "screen.ansi")
            tmp_ansi = f"{ansi_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_ansi, "w", encoding="utf-8") as f:
                f.write(snap.ansi_text)
            os.replace(tmp_ansi, ansi_path)

            # 3. Update registry
            if self.registry:
                self.registry.update(self.session_id, **status_dict)
        except Exception:
            pass

    def _flush_loop(self) -> None:
        """Periodic flush loop updating fallback files."""
        while not self._stop_event.is_set():
            self._stop_event.wait(0.5)
            if self._stop_event.is_set():
                break
            self.write_fallback_files()

    def _listen_loop(self) -> None:
        """Socket listener accepting client connections."""
        while self._running:
            try:
                client_sock, _ = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with self._lock:
                if not self._running:
                    client_sock.close()
                    break
                self._clients.append(client_sock)

            client_thread = threading.Thread(
                target=self._handle_client,
                args=(client_sock,),
                name=f"telemetry-client-{len(self._clients)}",
                daemon=True,
            )
            client_thread.start()

    def _handle_client(self, client_sock: socket.socket) -> None:
        """Process commands from an individual client connection."""
        try:
            buffer = ""
            while self._running:
                try:
                    data = client_sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break

                buffer += data.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        req = json.loads(line)
                    except json.JSONDecodeError:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "error": {"code": -32700, "message": "Parse error"},
                            "id": None,
                        }
                        client_sock.sendall((json.dumps(err_resp) + "\n").encode("utf-8"))
                        continue

                    should_continue = self._dispatch_command(req, client_sock)
                    if not should_continue:
                        return
        except Exception:
            pass
        finally:
            with self._lock:
                if client_sock in self._clients:
                    self._clients.remove(client_sock)
            try:
                client_sock.close()
            except Exception:
                pass

    def _dispatch_command(self, req: Dict[str, Any], client_sock: socket.socket) -> bool:
        """
        Handle a JSON-RPC / IPC command.
        Returns True to continue listening, False to terminate client connection.
        """
        method = str(req.get("method") or req.get("command") or req.get("action") or "").upper()
        req_id = req.get("id")
        params = req.get("params") or req.get("args") or req
        if not isinstance(params, dict):
            params = {}

        if method in ("GET_STATUS", "STATUS"):
            with self._lock:
                res = self.metadata.to_dict()
            resp = {"jsonrpc": "2.0", "result": res, "id": req_id}
            client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True

        elif method in ("GET_SCREEN", "SCREEN"):
            snap = ScreenSnapshot.from_terminal_state(self.state)
            res = snap.to_dict()
            resp = {"jsonrpc": "2.0", "result": res, "id": req_id}
            client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True

        elif method in ("GET_RAW", "RAW"):
            with getattr(self.state, "_lock", threading.Lock()):
                raw_text = self.state.get_rendered_text(strip_trailing=False)
            resp = {
                "jsonrpc": "2.0",
                "result": raw_text,
                "raw": raw_text,
                "text": raw_text,
                "id": req_id,
            }
            client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True

        elif method == "SUBSCRIBE":
            fps = float(params.get("fps", 10.0))
            fps = max(1.0, min(fps, 30.0))
            interval = 1.0 / fps

            init_snap = ScreenSnapshot.from_terminal_state(self.state)
            conf = {
                "jsonrpc": "2.0",
                "method": "subscription",
                "result": {
                    "status": "subscribed",
                    "fps": fps,
                    "snapshot": init_snap.to_dict(),
                },
                "id": req_id,
            }
            client_sock.sendall((json.dumps(conf) + "\n").encode("utf-8"))

            while self._running and not self._stop_event.is_set():
                time.sleep(interval)
                if not self._running or self._stop_event.is_set():
                    break

                snap = ScreenSnapshot.from_terminal_state(self.state)
                stream_msg = {
                    "jsonrpc": "2.0",
                    "method": "screen_snapshot",
                    "params": snap.to_dict(),
                }
                try:
                    client_sock.sendall((json.dumps(stream_msg) + "\n").encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break

            return False

        elif method in ("CAPTURE_IMAGE", "CAPTURE", "SCREENSHOT"):
            if self.renderer is None:
                resp = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Renderer not available"},
                    "id": req_id,
                }
                client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                return True

            dest_path = params.get("path") or params.get("file") or params.get("output")
            with self._lock:
                self.renderer.draw_frame(self.state)
                buf = io.BytesIO()
                self.renderer.surface.write_to_png(buf)
                png_bytes = buf.getvalue()

            if dest_path:
                full_path = os.path.abspath(dest_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(png_bytes)
                res = {
                    "status": "ok",
                    "path": full_path,
                    "size": len(png_bytes),
                    "format": "png",
                }
            else:
                b64 = base64.b64encode(png_bytes).decode("ascii")
                res = {
                    "status": "ok",
                    "format": "png",
                    "size": len(png_bytes),
                    "data": b64,
                }

            resp = {"jsonrpc": "2.0", "result": res, "id": req_id}
            client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True

        else:
            resp = {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
                "id": req_id,
            }
            client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            return True

    def update_step(
        self,
        step_idx: int,
        total_steps: int,
        step_type: str = "",
        step_desc: str = "",
    ) -> None:
        """Update current scenario timeline step metadata."""
        with self._lock:
            self.metadata.current_step_index = step_idx
            self.metadata.total_steps = total_steps
            self.metadata.current_step_type = step_type
            self.metadata.current_step_desc = step_desc
        self.write_fallback_files()

    def update_rendered_frame(
        self,
        rendered_frames: int,
        elapsed_seconds: float,
    ) -> None:
        """Update rendered frame counts and elapsed time."""
        with self._lock:
            self.metadata.rendered_frames = rendered_frames
            self.metadata.elapsed_seconds = elapsed_seconds

    def get_status(self) -> SessionMetadata:
        """Retrieve a copy of current SessionMetadata."""
        with self._lock:
            return SessionMetadata.from_dict(self.metadata.to_dict())

    def get_screen(self) -> ScreenSnapshot:
        """Retrieve a point-in-time ScreenSnapshot."""
        return ScreenSnapshot.from_terminal_state(self.state)

    def get_raw(self) -> str:
        """Retrieve raw screen text."""
        with getattr(self.state, "_lock", threading.Lock()):
            return self.state.get_rendered_text(strip_trailing=False)

    def capture_image(self, path: Optional[str] = None) -> bytes:
        """Render and export PNG frame directly."""
        if self.renderer is None:
            raise RuntimeError("Renderer is not available")
        with self._lock:
            self.renderer.draw_frame(self.state)
            buf = io.BytesIO()
            self.renderer.surface.write_to_png(buf)
            data = buf.getvalue()
        if path:
            full_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(data)
        return data

    def stop(self, status: str = "completed") -> None:
        """Clean shutdown of TelemetryServer: closes socket, removes socket file on exit."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            self.metadata.status = status

        # Final fallback flush
        self.write_fallback_files()

        # Close all active client connections
        with self._lock:
            for s in list(self._clients):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                    s.close()
                except Exception:
                    pass
            self._clients.clear()

        # Close server socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

        # Remove socket file
        if self.socket_path and os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        # Join background threads
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=1.0)

    def __enter__(self) -> "TelemetryServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        status = "failed" if exc_type else "completed"
        self.stop(status=status)
