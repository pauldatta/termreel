"""
TermReel Peek Subsystem.
Provides live session observation, real-time TUI streaming, PNG frame capture,
session listing, and auto-refreshing web dashboard.
"""

import base64
import html
import http.server
import io
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from termreel.telemetry.models import SessionMetadata, ScreenSnapshot
from termreel.telemetry.registry import SessionRegistry


class PeekClient:
    """
    Client for observing and inspecting active and historical TermReel recording sessions.
    Supports single-shot snapshots, live TUI streaming, image export, and web previews.
    """

    def __init__(self, registry: Optional[SessionRegistry] = None):
        self.registry = registry if registry is not None else SessionRegistry()

    def find_target_session(self, target: Optional[str] = None) -> Optional[SessionMetadata]:
        """
        Find target session by ID, prefix, or PID, or defaults to the latest active session.
        """
        if not target:
            latest = self.registry.get_latest_session()
            if latest:
                return latest
            # Fall back to latest non-active session if no active sessions exist
            all_sessions = self.registry.list_sessions(active_only=False)
            return all_sessions[0] if all_sessions else None

        clean_target = str(target).strip()

        # 1. Exact match via registry
        direct = self.registry.get_session(clean_target)
        if direct:
            return direct

        # 2. Check all sessions for PID, exact ID, or prefix match
        all_sessions = self.registry.list_sessions(active_only=False)
        for s in all_sessions:
            if str(s.pid) == clean_target:
                return s
            if s.session_id == clean_target:
                return s
            if s.session_id.startswith(clean_target):
                return s

        # 3. Substring match as fallback
        for s in all_sessions:
            if clean_target in s.session_id:
                return s

        return None

    def _send_socket_request(
        self,
        socket_path: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 0.3,
    ) -> Optional[Dict[str, Any]]:
        """
        Send a single JSON-RPC request to the telemetry UNIX domain socket.
        Returns the parsed response or None on error/timeout.
        """
        if not socket_path or not os.path.exists(socket_path):
            return None

        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(socket_path)

            req = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": int(time.time() * 1000) % 100000,
            }
            msg = json.dumps(req) + "\n"
            sock.sendall(msg.encode("utf-8"))

            buffer = ""
            while "\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")

            if "\n" in buffer:
                line = buffer.split("\n", 1)[0].strip()
                if line:
                    resp = json.loads(line)
                    if "result" in resp:
                        return resp["result"]
                    return resp
            return None
        except (OSError, socket.timeout, json.JSONDecodeError):
            return None
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _resolve_session_dirs(self, session: SessionMetadata) -> List[str]:
        """Resolve candidate directories containing session fallback files."""
        dirs = []
        # Primary registry session directory: <registry_dir>/<session_id>
        primary = os.path.join(self.registry.directory, session.session_id)
        dirs.append(primary)

        # Registry directory itself
        if self.registry.directory not in dirs:
            dirs.append(self.registry.directory)

        # Socket parent directory
        if session.socket_path:
            p = os.path.dirname(os.path.abspath(session.socket_path))
            if os.path.isdir(p) and p not in dirs:
                dirs.append(p)
            sub = os.path.join(p, session.session_id)
            if os.path.isdir(sub) and sub not in dirs:
                dirs.append(sub)

        # Default fallback directories
        for fallback in (
            os.path.expanduser(f"~/.termreel/sessions/{session.session_id}"),
            f"/tmp/termreel_sessions/{session.session_id}",
        ):
            if os.path.isdir(fallback) and fallback not in dirs:
                dirs.append(fallback)

        return dirs

    def query_status(self, session: SessionMetadata) -> Dict[str, Any]:
        """
        Queries UNIX domain socket (or reads status.json fallback).
        Returns dictionary of session status metrics.
        """
        # 1. Try UNIX domain socket query
        res = self._send_socket_request(session.socket_path, "GET_STATUS")
        if isinstance(res, dict):
            return res

        # 2. Try status.json fallback file across candidate directories
        for s_dir in self._resolve_session_dirs(session):
            status_path = os.path.join(s_dir, "status.json")
            if os.path.exists(status_path):
                try:
                    with open(status_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass

        # 3. Try registry metadata file
        reg_file = self.registry._session_file(session.session_id)
        if os.path.exists(reg_file):
            try:
                with open(reg_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        # 4. Return existing session dict
        return session.to_dict()

    def query_screen(self, session: SessionMetadata) -> Tuple[str, Dict[str, Any]]:
        """
        Retrieves screen text/ANSI and cursor info from socket or fallback file.
        Returns (ansi_text, cursor_metadata).
        """
        # 1. Try UNIX domain socket query
        res = self._send_socket_request(session.socket_path, "GET_SCREEN")
        if isinstance(res, dict):
            ansi_text = res.get("ansi_text") or res.get("text", "")
            cursor_info = {
                "row": int(res.get("cursor_row", 0)),
                "col": int(res.get("cursor_col", 0)),
                "visible": bool(res.get("cursor_visible", True)),
                "rows": int(res.get("rows", 24)),
                "cols": int(res.get("cols", 80)),
                "timestamp": float(res.get("timestamp", time.time())),
            }
            return ansi_text, cursor_info

        # 2. Try fallback files: screen.ansi or screen.txt across candidate directories
        for s_dir in self._resolve_session_dirs(session):
            ansi_path = os.path.join(s_dir, "screen.ansi")
            if os.path.exists(ansi_path):
                try:
                    with open(ansi_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    cursor_info = {
                        "row": 0,
                        "col": 0,
                        "visible": True,
                        "rows": 24,
                        "cols": 80,
                        "timestamp": os.path.getmtime(ansi_path),
                    }
                    return content, cursor_info
                except Exception:
                    pass

            txt_path = os.path.join(s_dir, "screen.txt")
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    cursor_info = {
                        "row": 0,
                        "col": 0,
                        "visible": True,
                        "rows": 24,
                        "cols": 80,
                        "timestamp": os.path.getmtime(txt_path),
                    }
                    return content, cursor_info
                except Exception:
                    pass

        # 3. Default empty screen
        return (
            "(No active terminal screen buffer found)",
            {"row": 0, "col": 0, "visible": False, "rows": 24, "cols": 80, "timestamp": time.time()},
        )

    def render_snapshot(self, session: SessionMetadata, raw: bool = False) -> str:
        """
        Renders a snapshot of the live terminal session.
        If raw: returns plain screen text without HUD.
        If not raw: formats a sleek HUD banner with session name, PID, step index/type/description,
        frames rendered, elapsed time, and output video path, followed by live 2D ANSI terminal content.
        """
        if raw:
            # Query raw text from socket if available
            raw_res = self._send_socket_request(session.socket_path, "GET_RAW")
            if isinstance(raw_res, str):
                return raw_res
            if isinstance(raw_res, dict) and "result" in raw_res:
                return str(raw_res["result"])

            screen_text, _ = self.query_screen(session)
            # Strip ANSI escape sequences for raw plain text
            return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", screen_text)

        # Non-raw: Fetch updated status and ANSI screen
        status = self.query_status(session)
        screen_ansi, _ = self.query_screen(session)

        # Extract HUD metrics
        session_id = status.get("session_id", session.session_id)
        pid = status.get("pid", session.pid)
        state_str = str(status.get("status", "running")).upper()
        title = status.get("scenario_title") or session_id
        step_idx = status.get("current_step_index", 0)
        total_steps = status.get("total_steps", 0)
        step_type = status.get("current_step_type", "")
        step_desc = status.get("current_step_desc", "")
        frames = status.get("rendered_frames", 0)
        fps = status.get("fps", 30)
        elapsed = status.get("elapsed_seconds", 0.0)
        output_video = status.get("output_video") or "None"

        # Format elapsed time as mm:ss.s or ss.s
        if elapsed >= 60:
            mins = int(elapsed // 60)
            secs = elapsed % 60
            elapsed_str = f"{mins:02d}:{secs:04.1f}"
        else:
            elapsed_str = f"{elapsed:.1f}s"

        # Status badge color
        if state_str == "RUNNING":
            status_badge = "\033[1;32m● RUNNING\033[0m"
        elif state_str == "COMPLETED":
            status_badge = "\033[1;34m✔ COMPLETED\033[0m"
        else:
            status_badge = f"\033[1;31m✖ {state_str}\033[0m"

        # Step description
        if total_steps > 0:
            step_part = f"\033[1;33mStep {step_idx}/{total_steps}\033[0m"
            if step_type:
                step_part += f": \033[1m{step_type}\033[0m"
            if step_desc:
                step_part += f" ({step_desc})"
        else:
            step_part = f"\033[1;33mStep {step_idx}\033[0m" + (f": \033[1m{step_type}\033[0m" if step_type else "")

        width = 80
        bar_line = "─" * (width - 2)

        hud_lines = [
            f"\033[1;36m╭{bar_line}╮\033[0m",
            f"\033[1;36m│\033[0m \033[1;37mTermReel Live Peek\033[0m │ \033[36mSession:\033[0m \033[1m{title}\033[0m │ \033[36mPID:\033[0m {pid} │ {status_badge}",
            f"\033[1;36m│\033[0m {step_part}",
            f"\033[1;36m│\033[0m \033[36mFrames:\033[0m {frames} ({fps} fps) │ \033[36mElapsed:\033[0m {elapsed_str} │ \033[36mVideo:\033[0m {output_video}",
            f"\033[1;36m╰{bar_line}╯\033[0m",
        ]

        return "\n".join(hud_lines) + "\n\n" + screen_ansi

    def follow(
        self,
        session: SessionMetadata,
        interval: float = 0.1,
        max_iterations: Optional[int] = None,
    ) -> None:
        """
        Interactive live TUI stream (10 FPS default).
        Uses alternate screen buffer (\x1b[?1049h) and cursor resets (\x1b[H) to smoothly refresh.
        Handles 'q', 'Esc', or Ctrl+C to cleanly exit back to parent terminal without stopping recording.
        """
        is_tty = sys.stdin.isatty() and sys.stdout.isatty()
        old_settings = None

        if is_tty:
            try:
                import termios
                import tty
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                is_tty = False

        # Switch to alternate screen buffer and hide cursor
        try:
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
        except Exception:
            pass

        iteration = 0
        try:
            while True:
                iteration += 1
                snapshot = self.render_snapshot(session, raw=False)
                # Move cursor home and write full snapshot
                sys.stdout.write(f"\x1b[H{snapshot}\x1b[J")
                sys.stdout.flush()

                if max_iterations is not None and iteration >= max_iterations:
                    break

                # Sleep / poll for keystrokes
                start_wait = time.time()
                while (time.time() - start_wait) < interval:
                    remaining = max(0.01, interval - (time.time() - start_wait))
                    if is_tty:
                        rlist, _, _ = select.select([sys.stdin], [], [], remaining)
                        if rlist:
                            char = sys.stdin.read(1)
                            # 'q', 'Q', Esc ('\x1b'), or Ctrl+C ('\x03')
                            if char in ("q", "Q", "\x1b", "\x03"):
                                return
                    else:
                        time.sleep(remaining)
                        break

        except KeyboardInterrupt:
            pass
        finally:
            # Restore cursor and leave alternate screen buffer
            try:
                sys.stdout.write("\x1b[?25h\x1b[?1049l\n")
                sys.stdout.flush()
            except Exception:
                pass
            if is_tty and old_settings is not None:
                try:
                    import termios
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass

    def capture_image(self, session: SessionMetadata, output_path: str) -> bool:
        """
        Requests high-res PNG frame capture from server or extracts from video via FFmpeg.
        """
        full_out_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(full_out_path), exist_ok=True)

        # 1. Request image capture from TelemetryServer via socket
        resp = self._send_socket_request(
            session.socket_path,
            "CAPTURE_IMAGE",
            {"path": full_out_path},
            timeout=1.5,
        )
        if isinstance(resp, dict):
            if resp.get("status") == "ok" and os.path.exists(full_out_path):
                return True
            if "data" in resp:
                try:
                    png_data = base64.b64decode(resp["data"])
                    with open(full_out_path, "wb") as f:
                        f.write(png_data)
                    return True
                except Exception:
                    pass

        # 2. Extract latest frame from output video using FFmpeg
        video_path = session.output_video
        if not video_path:
            status = self.query_status(session)
            video_path = status.get("output_video")

        if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            try:
                # Seek to near end of video for latest frame
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-sseof",
                    "-0.1",
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    full_out_path,
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0 and os.path.exists(full_out_path) and os.path.getsize(full_out_path) > 0:
                    return True

                # Fallback: capture first frame if video is very short
                cmd2 = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    full_out_path,
                ]
                res2 = subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res2.returncode == 0 and os.path.exists(full_out_path) and os.path.getsize(full_out_path) > 0:
                    return True
            except Exception:
                pass

        # 3. Fallback: synthesize vector frame using CairoTerminalRenderer if available
        try:
            from termreel.renderer.cairo_renderer import CairoTerminalRenderer
            from termreel.emulator.state import TerminalState
            from termreel.emulator.parser import ANSIParser

            screen_text, info = self.query_screen(session)
            renderer = CairoTerminalRenderer(
                width=1280,
                height=720,
                title=session.scenario_title or session.session_id,
                subtitle=f"Live Snapshot (PID: {session.pid})",
            )
            state = TerminalState(rows=renderer.rows, cols=renderer.cols)
            parser = ANSIParser(state)
            parser.feed(screen_text)
            renderer.draw_frame(state)
            renderer.surface.write_to_png(full_out_path)
            if os.path.exists(full_out_path) and os.path.getsize(full_out_path) > 0:
                return True
        except Exception:
            pass

        return False

    def list_sessions(self) -> str:
        """
        Formats a table of active and recent sessions (Session ID, PID, Status, Scenario, Current Step, Frames, Elapsed).
        """
        sessions = self.registry.list_sessions(active_only=False)
        if not sessions:
            return "No active or recent TermReel sessions found."

        headers = ["SESSION ID", "PID", "STATUS", "SCENARIO", "CURRENT STEP", "FRAMES", "ELAPSED"]
        rows: List[List[str]] = []

        for s in sessions:
            sid = s.session_id
            pid = str(s.pid)
            status_str = s.status.upper()
            scenario = s.scenario_title or (os.path.basename(s.scenario_path) if s.scenario_path else "-")
            if len(scenario) > 24:
                scenario = scenario[:21] + "..."

            if s.total_steps > 0:
                step_str = f"{s.current_step_index}/{s.total_steps}"
                if s.current_step_type:
                    step_str += f" ({s.current_step_type})"
            elif s.current_step_type:
                step_str = s.current_step_type
            else:
                step_str = "-"

            frames_str = str(s.rendered_frames)
            elapsed_str = f"{s.elapsed_seconds:.1f}s"

            rows.append([sid, pid, status_str, scenario, step_str, frames_str, elapsed_str])

        col_widths = [len(h) for h in headers]
        for r in rows:
            for i, val in enumerate(r):
                col_widths[i] = max(col_widths[i], len(val))

        header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
        data_lines = ["  ".join(r[i].ljust(col_widths[i]) for i in range(len(headers))) for r in rows]

        return "\n".join([header_line, sep_line] + data_lines)

    def serve_web(self, session: SessionMetadata, port: int = 8989) -> None:
        """
        Lightweight local HTTP server serving an auto-refreshing dark-mode web page
        displaying the live terminal screen (styled in Catppuccin / Tokyo Night),
        HUD badges, and step progress. Accessible via http://localhost:<port>
        or http://pauldatta.c.googlers.com:<port>.
        """
        client_ref = self
        server_instance = self.create_web_server(session, port=port)

        print(f"\n🎬 \033[1;36mTermReel Live Web Dashboard\033[0m active for session '\033[1m{session.session_id}\033[0m':")
        print(f"   Local:   \033[1;32mhttp://localhost:{port}\033[0m")
        print(f"   Network: \033[1;32mhttp://pauldatta.c.googlers.com:{port}\033[0m")
        print("   Press \033[1mCtrl+C\033[0m to cleanly stop the dashboard server.\n")

        try:
            server_instance.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down web dashboard server...")
        finally:
            server_instance.server_close()

    def create_web_server(self, session: SessionMetadata, port: int = 8989) -> http.server.HTTPServer:
        """
        Instantiate configured HTTP server instance for web dashboard without blocking.
        """
        client_ref = self

        class ReusableTCPServer(http.server.HTTPServer):
            allow_reuse_address = True

        class PeekWebHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress noisy standard HTTP access logs
                pass

            def do_GET(self) -> None:
                path = self.path.split("?")[0]

                if path in ("/", "/index.html"):
                    self._serve_html()
                elif path == "/api/snapshot":
                    self._serve_snapshot_json()
                elif path == "/api/status":
                    self._serve_status_json()
                elif path == "/api/raw":
                    self._serve_raw_text()
                elif path in ("/api/image", "/screenshot.png"):
                    self._serve_image()
                else:
                    self.send_error(404, "Not Found")

            def _serve_html(self) -> None:
                content = client_ref._generate_dashboard_html(session, port)
                data = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_snapshot_json(self) -> None:
                status = client_ref.query_status(session)
                ansi_text, cursor = client_ref.query_screen(session)
                html_screen = client_ref.ansi_to_html(ansi_text)
                resp = {
                    "status": status,
                    "cursor": cursor,
                    "screen_html": html_screen,
                    "screen_text": re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", ansi_text),
                }
                data = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_status_json(self) -> None:
                status = client_ref.query_status(session)
                data = json.dumps(status).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_raw_text(self) -> None:
                raw_text = client_ref.render_snapshot(session, raw=True)
                data = raw_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_image(self) -> None:
                # Capture temporary image
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_name = tmp.name

                try:
                    success = client_ref.capture_image(session, tmp_name)
                    if success and os.path.exists(tmp_name):
                        with open(tmp_name, "rb") as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    else:
                        self.send_error(500, "Failed to capture image")
                finally:
                    if os.path.exists(tmp_name):
                        try:
                            os.unlink(tmp_name)
                        except OSError:
                            pass

        return ReusableTCPServer(("", port), PeekWebHandler)

    @staticmethod
    def ansi_to_html(ansi_text: str) -> str:
        """
        Convert ANSI TrueColor and styling escape sequences into modern styled HTML spans.
        """
        if not ansi_text:
            return ""

        parts = []
        open_spans = 0

        # Pattern matches ANSI SGR escape sequences: \033[...m
        token_regex = re.compile(r"(\x1b\[[0-9;]*m)")
        tokens = token_regex.split(ansi_text)

        for token in tokens:
            if not token:
                continue

            if token.startswith("\x1b[") and token.endswith("m"):
                code_str = token[2:-1]
                if not code_str or code_str == "0":
                    while open_spans > 0:
                        parts.append("</span>")
                        open_spans -= 1
                    continue

                codes = [int(c) for c in code_str.split(";") if c.isdigit()]
                styles = []
                idx = 0
                while idx < len(codes):
                    c = codes[idx]
                    if c == 0:
                        while open_spans > 0:
                            parts.append("</span>")
                            open_spans -= 1
                    elif c == 1:
                        styles.append("font-weight:bold")
                    elif c == 2:
                        styles.append("opacity:0.7")
                    elif c == 3:
                        styles.append("font-style:italic")
                    elif c == 4:
                        styles.append("text-decoration:underline")
                    elif c == 7:
                        styles.append("filter:invert(1)")
                    elif c == 9:
                        styles.append("text-decoration:line-through")
                    elif c == 38 and idx + 4 < len(codes) and codes[idx + 1] == 2:
                        # 24-bit TrueColor foreground
                        r, g, b = codes[idx + 2], codes[idx + 3], codes[idx + 4]
                        styles.append(f"color:rgb({r},{g},{b})")
                        idx += 4
                    elif c == 48 and idx + 4 < len(codes) and codes[idx + 1] == 2:
                        # 24-bit TrueColor background
                        r, g, b = codes[idx + 2], codes[idx + 3], codes[idx + 4]
                        styles.append(f"background-color:rgb({r},{g},{b})")
                        idx += 4
                    elif 30 <= c <= 37:
                        # Basic ANSI colors
                        basic_colors = [
                            "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
                            "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                        ]
                        styles.append(f"color:{basic_colors[c - 30]}")
                    elif 90 <= c <= 97:
                        # Bright ANSI colors
                        bright_colors = [
                            "#585b70", "#eba0ac", "#a6e3a1", "#f9e2af",
                            "#74c7ec", "#b4befe", "#89dceb", "#cdd6f4",
                        ]
                        styles.append(f"color:{bright_colors[c - 90]}")
                    idx += 1

                if styles:
                    style_attr = ";".join(styles)
                    parts.append(f'<span style="{style_attr}">')
                    open_spans += 1
            else:
                # Text node: escape HTML entities
                parts.append(html.escape(token))

        while open_spans > 0:
            parts.append("</span>")
            open_spans -= 1

        return "".join(parts)

    def _generate_dashboard_html(self, session: SessionMetadata, port: int) -> str:
        """
        Generates dark-mode HTML dashboard styled in Catppuccin Mocha / Tokyo Night.
        """
        title = session.scenario_title or session.session_id
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TermReel Live Peek: {html.escape(title)}</title>
  <style>
    :root {{
      --ctp-base: #1e1e2e;
      --ctp-mantle: #181825;
      --ctp-crust: #11111b;
      --ctp-surface0: #313244;
      --ctp-surface1: #45475a;
      --ctp-surface2: #585b70;
      --ctp-text: #cdd6f4;
      --ctp-subtext0: #a6adc8;
      --ctp-blue: #89b4fa;
      --ctp-green: #a6e3a1;
      --ctp-yellow: #f9e2af;
      --ctp-red: #f38ba8;
      --ctp-mauve: #cba6f7;
      --ctp-teal: #94e2d5;
      --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Consolas, monospace;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background-color: var(--ctp-base);
      color: var(--ctp-text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}

    header {{
      background-color: var(--ctp-mantle);
      border-bottom: 1px solid var(--ctp-surface0);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }}

    .logo-container {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .logo-badge {{
      background: linear-gradient(135deg, var(--ctp-blue), var(--ctp-mauve));
      color: var(--ctp-crust);
      font-weight: 800;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 14px;
      letter-spacing: 0.5px;
    }}

    .title-group h1 {{
      font-size: 18px;
      font-weight: 700;
      color: var(--ctp-text);
    }}

    .title-group p {{
      font-size: 12px;
      color: var(--ctp-subtext0);
      font-family: var(--font-mono);
    }}

    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.5px;
      background-color: var(--ctp-surface0);
    }}

    .status-badge.running {{
      background-color: rgba(166, 227, 161, 0.15);
      color: var(--ctp-green);
      border: 1px solid rgba(166, 227, 161, 0.3);
    }}

    .status-badge.completed {{
      background-color: rgba(137, 180, 250, 0.15);
      color: var(--ctp-blue);
      border: 1px solid rgba(137, 180, 250, 0.3);
    }}

    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: currentColor;
      box-shadow: 0 0 8px currentColor;
    }}

    main {{
      flex: 1;
      padding: 24px;
      max-width: 1400px;
      margin: 0 auto;
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }}

    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }}

    .metric-card {{
      background-color: var(--ctp-mantle);
      border: 1px solid var(--ctp-surface0);
      border-radius: 8px;
      padding: 12px 16px;
    }}

    .metric-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--ctp-subtext0);
      margin-bottom: 4px;
    }}

    .metric-value {{
      font-size: 16px;
      font-weight: 600;
      color: var(--ctp-text);
      font-family: var(--font-mono);
      word-break: break-all;
    }}

    .progress-container {{
      background-color: var(--ctp-surface0);
      height: 6px;
      border-radius: 3px;
      overflow: hidden;
      margin-top: 8px;
    }}

    .progress-fill {{
      height: 100%;
      background: linear-gradient(90deg, var(--ctp-blue), var(--ctp-teal));
      width: 0%;
      transition: width 0.3s ease;
    }}

    .terminal-window {{
      background-color: var(--ctp-crust);
      border: 1px solid var(--ctp-surface0);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
      display: flex;
      flex-direction: column;
    }}

    .terminal-header {{
      background-color: var(--ctp-mantle);
      border-bottom: 1px solid var(--ctp-surface0);
      padding: 10px 16px;
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .window-controls {{
      display: flex;
      gap: 6px;
    }}

    .dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
    }}

    .dot-close {{ background-color: #ff5f56; }}
    .dot-min {{ background-color: #ffbd2e; }}
    .dot-max {{ background-color: #27c93f; }}

    .window-title {{
      flex: 1;
      text-align: center;
      font-size: 12px;
      color: var(--ctp-subtext0);
      font-family: var(--font-mono);
      padding-right: 48px;
    }}

    .terminal-body {{
      padding: 16px;
      overflow-x: auto;
      min-height: 480px;
      max-height: 75vh;
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 1.4;
      white-space: pre;
      color: var(--ctp-text);
      background-color: var(--ctp-crust);
    }}

    footer {{
      background-color: var(--ctp-mantle);
      border-top: 1px solid var(--ctp-surface0);
      padding: 12px 24px;
      font-size: 12px;
      color: var(--ctp-subtext0);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    .pulse {{
      animation: pulse-animation 2s infinite;
    }}

    @keyframes pulse-animation {{
      0% {{ opacity: 1; }}
      50% {{ opacity: 0.4; }}
      100% {{ opacity: 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="logo-container">
      <div class="logo-badge">TERMREEL PEEK</div>
      <div class="title-group">
        <h1 id="header-title">{html.escape(title)}</h1>
        <p id="header-subtitle">Session: {html.escape(session.session_id)} • PID: {session.pid}</p>
      </div>
    </div>
    <div id="status-badge" class="status-badge running">
      <div class="status-dot"></div>
      <span id="status-text">RUNNING</span>
    </div>
  </header>

  <main>
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">Timeline Step</div>
        <div class="metric-value" id="val-step">Loading...</div>
        <div class="progress-container">
          <div class="progress-fill" id="progress-bar"></div>
        </div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Frames Rendered</div>
        <div class="metric-value" id="val-frames">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Elapsed Time</div>
        <div class="metric-value" id="val-elapsed">0.0s</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Output Video</div>
        <div class="metric-value" id="val-video" style="font-size: 12px;">{html.escape(session.output_video or "-")}</div>
      </div>
    </div>

    <div class="terminal-window">
      <div class="terminal-header">
        <div class="window-controls">
          <div class="dot dot-close"></div>
          <div class="dot dot-min"></div>
          <div class="dot dot-max"></div>
        </div>
        <div class="window-title" id="terminal-title">termreel ~ live terminal</div>
      </div>
      <pre class="terminal-body" id="terminal-screen">Connecting to TermReel telemetry stream...</pre>
    </div>
  </main>

  <footer>
    <div>TermReel v0.1.0 • Real-Time Terminal Telemetry</div>
    <div class="pulse">⚡ Live polling (5 FPS)</div>
  </footer>

  <script>
    async function updateDashboard() {{
      try {{
        const resp = await fetch('/api/snapshot');
        if (!resp.ok) return;
        const data = await resp.json();

        // Update Screen
        const screenEl = document.getElementById('terminal-screen');
        if (data.screen_html) {{
          screenEl.innerHTML = data.screen_html;
        }} else if (data.screen_text) {{
          screenEl.textContent = data.screen_text;
        }}

        // Update Metrics
        const st = data.status || {{}};
        if (st.status) {{
          const badge = document.getElementById('status-badge');
          const txt = document.getElementById('status-text');
          txt.textContent = st.status.toUpperCase();
          badge.className = 'status-badge ' + st.status.toLowerCase();
        }}

        const stepIdx = st.current_step_index || 0;
        const totalSteps = st.total_steps || 0;
        const stepType = st.current_step_type || '';
        const stepDesc = st.current_step_desc || '';

        let stepText = stepIdx + ' / ' + totalSteps;
        if (stepType) stepText += ' (' + stepType + ')';
        document.getElementById('val-step').textContent = stepText;

        if (totalSteps > 0) {{
          const pct = Math.min(100, Math.round((stepIdx / totalSteps) * 100));
          document.getElementById('progress-bar').style.width = pct + '%';
        }}

        document.getElementById('val-frames').textContent = (st.rendered_frames || 0) + ' (' + (st.fps || 30) + ' fps)';
        document.getElementById('val-elapsed').textContent = (st.elapsed_seconds || 0).toFixed(1) + 's';
        if (st.output_video) {{
          document.getElementById('val-video').textContent = st.output_video;
        }}
      }} catch (err) {{
        console.warn('Dashboard poll error:', err);
      }}
    }}

    setInterval(updateDashboard, 200);
    updateDashboard();
  </script>
</body>
</html>
"""
