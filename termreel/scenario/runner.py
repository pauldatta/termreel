"""
Scenario runner orchestrating environment lifecycle, PTY supervision,
keystroke injection, reactive triggers, and continuous video synthesis.
"""

from dataclasses import dataclass, field
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Union
from termreel.exceptions import ScenarioValidationError
from termreel.telemetry.models import SessionMetadata
from termreel.telemetry.registry import SessionRegistry
from termreel.telemetry.server import TelemetryServer
from termreel.emulator.state import TerminalState
from termreel.emulator.parser import ANSIParser
from termreel.emulator.composite import composite_panes
from termreel.emulator.serialize import serialize_screen
from termreel.supervisor.base import BaseSupervisor
from termreel.supervisor.factory import create_supervisor
from termreel.supervisor.tmux_session import TmuxSupervisor
from termreel.renderer.cairo_renderer import CairoTerminalRenderer
from termreel.transcoder.ffmpeg_pipe import FFmpegPipe
from termreel.transcoder.gif_encoder import GifEncoder
from termreel.reactor.triggers import (
    Trigger,
    TriggerAction,
    ActionType,
    create_trust_dialog_trigger,
    create_agy_permission_dialog_trigger,
    create_yes_no_prompt_trigger,
)
from termreel.reactor.monitor import ScreenMonitor
from termreel.utils.keystrokes import KeystrokeGenerator, KeyMap
from termreel.utils.redaction import Redactor
from termreel.utils.asciicast import AsciicastRecorder
from termreel.scenario.schema import ScenarioManifest, TimelineStep
from termreel.hooks.bridge import AgyHookBridge
from termreel.hooks.manager import HookManager
from termreel.hooks.models import HookEvent, HookEventType


@dataclass
class ScenarioReport:
    """Execution summary report."""
    status: str
    duration_sec: float
    frame_count: int
    output_file: str
    file_size_bytes: int
    cast_file: Optional[str] = None
    poster_file: Optional[str] = None
    error_message: Optional[str] = None
    conversation_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    session_id: Optional[str] = None
    # Keystrokes typed by screen triggers (auto-approve, auto-trust, custom
    # triggers): [{"time", "trigger", "pattern", "keys"}, ...]
    injections: List[Dict[str, Any]] = field(default_factory=list)
    # Non-fatal teardown problems (poster, cleanup, ...) that did not stop
    # the recording from being written.
    teardown_errors: List[str] = field(default_factory=list)



class ScenarioRunner:
    """
    Drives end-to-end execution of a TermReel declarative scenario manifest.
    """

    def __init__(
        self,
        manifest: ScenarioManifest,
        output_override: Optional[str] = None,
        fps_override: Optional[int] = None,
        theme_override: Optional[str] = None,
        backend: str = "auto",
        verbose: bool = True,
    ):
        self.manifest = manifest
        self.backend = backend
        self.verbose = verbose

        # Apply CLI overrides if provided
        if output_override:
            self.manifest.metadata.output = output_override
        if fps_override:
            self.manifest.metadata.fps = fps_override
        if theme_override:
            self.manifest.metadata.theme = theme_override

        self.width, self.height = self.manifest.metadata.resolution
        self.fps = self.manifest.metadata.fps
        self.output_file = os.path.abspath(self.manifest.metadata.output)

        self.renderer = CairoTerminalRenderer(
            width=self.width,
            height=self.height,
            title=self.manifest.metadata.title,
            subtitle=self.manifest.metadata.subtitle,
            theme=self.manifest.metadata.theme,
            font_family=self.manifest.metadata.font,
            font_size=self.manifest.metadata.font_size,
        )

        self.state = TerminalState(
            rows=self.renderer.rows,
            cols=self.renderer.cols,
            default_fg=self.renderer.theme.default_fg,
            default_bg=self.renderer.theme.terminal_bg,
            palette=self.renderer.theme.palette,
        )
        self.parser = ANSIParser(self.state)
        self.redactor = Redactor.create(
            mask=getattr(self.manifest, "mask", None),
            redactions=getattr(self.manifest, "redactions", None),
            load_global=True,
        )

        self.supervisor: Optional[BaseSupervisor] = None
        self.ffmpeg_pipe: Optional[FFmpegPipe] = None
        self.asciicast: Optional[AsciicastRecorder] = None
        self.monitor = ScreenMonitor()

        # Antigravity Hooks integration
        self.hook_bridge = AgyHookBridge()
        self.hook_manager: Optional[HookManager] = None
        self._hook_active_tool: Optional[str] = None
        self._setup_hook_listeners()

        self._active_card: Optional[Dict[str, Any]] = None
        self._status_left: Optional[str] = self.manifest.metadata.statusbar_left
        self._status_right: Optional[str] = self.manifest.metadata.statusbar_right
        self._status_pill: str = "● LIVE TTY"
        self._base_status_pill: str = "● LIVE TTY"
        self._speedup_factor: float = 1.0
        self._speedup_indicator: Optional[str] = None
        self._frame_accumulator: float = 0.0

        self._is_recording = False
        self._capture_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._temp_dir: Optional[str] = None
        self._work_dir: str = os.getcwd()
        self._last_captured_ansi = ""
        self.active_conversation_id: Optional[str] = self.manifest.environment.conversation_id

        # Telemetry subsystem initialization
        self.session_id = uuid.uuid4().hex[:12]
        self.telemetry_registry = SessionRegistry()
        session_dir = os.path.join(self.telemetry_registry.directory, self.session_id)
        self.telemetry_session_dir = session_dir
        socket_path = os.path.join(session_dir, "telemetry.sock")
        if len(socket_path) > 100:
            socket_path = f"/tmp/tr_{self.session_id}.sock"

        self.telemetry_metadata = SessionMetadata(
            session_id=self.session_id,
            pid=os.getpid(),
            scenario_title=self.manifest.metadata.title,
            scenario_path=getattr(self.manifest, "source_file", "") or "",
            output_video=self.output_file,
            started_at=time.time(),
            current_step_index=0,
            total_steps=len(self.manifest.timeline),
            current_step_type="",
            current_step_desc="",
            fps=self.fps,
            rendered_frames=0,
            elapsed_seconds=0.0,
            socket_path=socket_path,
            status="running",
        )
        self.telemetry_server: Optional[TelemetryServer] = None
        self.telemetry: Optional[TelemetryServer] = None
        self.telemetry_renderer: Optional[CairoTerminalRenderer] = None

        self._init_triggers()

    def _setup_hook_listeners(self):
        """Wire hook lifecycle events to dynamic UI telemetry."""
        def _on_hook_event(ev: HookEvent):
            if ev.conversation_id:
                with self._lock:
                    self.active_conversation_id = ev.conversation_id

            norm = HookEventType.from_string(ev.event_type).value if ev.event_type else ""
            if norm == HookEventType.PRE_TOOL_USE.value and ev.tool_name:
                with self._lock:
                    self._hook_active_tool = ev.tool_name
                    if not self.manifest.metadata.statusbar_left:
                        self._status_left = f"Tool: {ev.tool_name}"
                    self._status_pill = f"● RUNNING {ev.tool_name.upper()}"
            elif norm == HookEventType.POST_TOOL_USE.value:
                with self._lock:
                    self._hook_active_tool = None
                    if not self.manifest.metadata.statusbar_left:
                        self._status_left = self.manifest.metadata.statusbar_left
                    self._status_pill = "● LIVE TTY"
            elif norm == HookEventType.PRE_INVOCATION.value:
                with self._lock:
                    self._status_pill = "● GENERATING"
            elif norm == HookEventType.POST_INVOCATION.value:
                with self._lock:
                    self._status_pill = "● LIVE TTY"
            elif norm == HookEventType.STOP.value:
                with self._lock:
                    self._status_pill = "● IDLE"

        self.hook_bridge.add_listener(_on_hook_event)


    def _log(self, msg: str):
        if self.verbose:
            print(f"[termreel] {msg}")

    @staticmethod
    def _parse_trigger_action(
        action_val: Any,
        delay_before: float = 0.0,
        delay_after: float = 0.3,
    ) -> Union[TriggerAction, List[TriggerAction], str]:
        if isinstance(action_val, list):
            acts = []
            for item in action_val:
                if isinstance(item, dict):
                    act_type_str = item.get("type", "send_key")
                    act_type = ActionType(act_type_str)
                    val = item.get("value") or item.get("send_key") or item.get("key") or item.get("choice") or "Enter"
                    d_after = float(item.get("delay", item.get("delay_after", delay_after)))
                    d_before = float(item.get("delay_before", delay_before))
                    acts.append(TriggerAction(action_type=act_type, value=val, delay_before=d_before, delay_after=d_after))
                elif isinstance(item, TriggerAction):
                    acts.append(item)
                else:
                    acts.append(str(item))
            return acts
        elif isinstance(action_val, dict):
            act_type_str = action_val.get("type", "send_key")
            act_type = ActionType(act_type_str)
            val = action_val.get("value") or action_val.get("send_key") or action_val.get("key") or action_val.get("choice") or "Enter"
            d_after = float(action_val.get("delay", action_val.get("delay_after", delay_after)))
            d_before = float(action_val.get("delay_before", delay_before))
            return TriggerAction(action_type=act_type, value=val, delay_before=d_before, delay_after=d_after)
        elif isinstance(action_val, TriggerAction):
            return action_val
        else:
            return TriggerAction(
                action_type=ActionType.SEND_KEY,
                value=str(action_val),
                delay_before=delay_before,
                delay_after=delay_after,
            )

    def _init_triggers(self):
        """Convert manifest trigger configs into active Trigger instances."""
        for tc in self.manifest.triggers:
            act = self._parse_trigger_action(tc.action, delay_before=tc.delay_before, delay_after=tc.delay_after)
            max_c = tc.max_count if getattr(tc, "max_count", None) is not None else tc.max_firings
            self.monitor.add_trigger(
                Trigger(
                    pattern=tc.on_match,
                    action=act,
                    once=tc.once,
                    cooldown_seconds=tc.cooldown,
                    max_firings=max_c,
                    max_count=max_c,
                    edge=getattr(tc, "edge", None),
                    label=f"trigger:{tc.on_match[:40]}",
                )
            )

        # Auto-register workspace trust dialog trigger if enabled and not already configured
        if self.manifest.environment.auto_trust:
            has_trust_trigger = any("trust" in str(getattr(t, "pattern", "")).lower() for t in self.monitor.triggers)
            if not has_trust_trigger:
                self.monitor.add_trigger(create_trust_dialog_trigger(delay_before=0.4, delay_after=0.4))

        # Auto-register interactive permission dialog triggers only when asked
        # to (environment.auto_approve_dialogs / auto_approve). These type into
        # the session, so they are opt-in and every firing is reported.
        if self.manifest.environment.auto_approve_dialogs:
            has_perm_trigger = any(
                ("permission" in str(getattr(t, "pattern", "")).lower() or "proceed" in str(getattr(t, "pattern", "")).lower())
                for t in self.monitor.triggers
            )
            if not has_perm_trigger:
                self.monitor.add_trigger(create_agy_permission_dialog_trigger(choice=1, delay_before=0.5, delay_after=0.4))

            has_yes_no_trigger = any("y/n" in str(getattr(t, "pattern", "")).lower() for t in self.monitor.triggers)
            if not has_yes_no_trigger:
                self.monitor.add_trigger(create_yes_no_prompt_trigger(response="y", delay_before=0.4, delay_after=0.4))

    def _setup_environment(self):
        """Set up working directory, temporary workspace, and run setup commands."""
        if self.manifest.environment.workspace_path:
            self._work_dir = os.path.abspath(self.manifest.environment.workspace_path)
            os.makedirs(self._work_dir, exist_ok=True)
            self._log(f"Using specified workspace directory: {self._work_dir}")
        elif self.manifest.environment.create_temp_workspace:
            self._temp_dir = tempfile.mkdtemp(prefix=self.manifest.environment.temp_workspace_prefix)
            self._work_dir = self._temp_dir
            self._log(f"Created temporary workspace at: {self._work_dir}")
        elif self.manifest.environment.cwd:
            self._work_dir = os.path.abspath(self.manifest.environment.cwd)
            os.makedirs(self._work_dir, exist_ok=True)
            self._log(f"Using working directory: {self._work_dir}")
        else:
            self._work_dir = os.getcwd()

        # Run setup commands
        for cmd in self.manifest.environment.setup_commands:
            self._log(f"Running setup command: {cmd}")
            subprocess.run(cmd, shell=True, cwd=self._work_dir, check=True)

        # Setup Antigravity lifecycle hooks & settings if enabled
        if self.manifest.environment.agy_hooks or self.manifest.environment.permissions or self.manifest.environment.settings:
            custom_cfg = (
                self.manifest.environment.hooks
                if isinstance(self.manifest.environment.hooks, dict)
                else None
            )
            self.hook_manager = HookManager(
                workspace_dir=self._work_dir,
                bridge=self.hook_bridge,
                auto_approve=self.manifest.environment.agy_auto_approve,
                log_events=self.manifest.environment.agy_event_bridge,
                custom_policy=self.manifest.environment.agy_custom_policy,
                custom_hooks_config=custom_cfg,
                permissions=self.manifest.environment.permissions,
                settings=self.manifest.environment.settings,
                provision_settings=True,
            )
            prov = self.hook_manager.provision()
            self._log(f"Provisioned Antigravity hooks & settings in: {self._work_dir}")

    def _cleanup_environment(self):
        """Run cleanup commands and delete temporary workspace if applicable."""
        if self.hook_manager:
            self.hook_manager.cleanup()
            self._log(f"Cleaned up Antigravity hooks in: {self._work_dir}")

        for cmd in self.manifest.environment.cleanup_commands:
            try:
                subprocess.run(cmd, shell=True, cwd=self._work_dir, check=False)
            except Exception:
                pass

        if self._temp_dir and os.path.exists(self._temp_dir):
            if self.manifest.environment.preserve_workspace:
                self._log(f"Preserving workspace directory for resumption: {self._temp_dir}")
            else:
                try:
                    shutil.rmtree(self._temp_dir)
                    self._log(f"Cleaned up temporary workspace: {self._temp_dir}")
                except Exception:
                    pass

    def _on_pty_output(self, chunk: bytes) -> None:
        """
        Mirror hook invoked by PtySupervisor's reader thread for every chunk
        read off the PTY master.

        The tmux backend can be polled with capture-pane, but the PTY master is
        a single-consumer stream, so this is the only place the raw child bytes
        are observable. Keep it cheap: it runs on the reader thread and blocking
        here stalls ANSI parsing.
        """
        rec = self.asciicast
        if rec is not None:
            try:
                rec.record_output_bytes(chunk)
            except Exception:
                pass

    def set_speedup(self, spec: Any) -> None:
        """Set dynamic terminal video speedup factor and status pill badge."""
        with self._lock:
            if spec is None or spec is False:
                self._speedup_factor = 1.0
                self._speedup_indicator = None
                self._frame_accumulator = 0.0
                return
            if isinstance(spec, (int, float)):
                self._speedup_factor = max(1.0, float(spec))
                self._speedup_indicator = (
                    f"⏩ {int(self._speedup_factor) if self._speedup_factor.is_integer() else self._speedup_factor}x"
                    if self._speedup_factor > 1.0
                    else None
                )
            elif isinstance(spec, dict):
                factor = float(spec.get("factor", spec.get("value", 2.0)))
                self._speedup_factor = max(1.0, factor)
                self._speedup_indicator = spec.get("indicator")
                if self._speedup_factor > 1.0 and not self._speedup_indicator:
                    self._speedup_indicator = (
                        f"⏩ {int(self._speedup_factor) if self._speedup_factor.is_integer() else self._speedup_factor}x"
                    )
            self._frame_accumulator = 0.0

    def _capture_tmux_frame(self) -> bool:
        """
        Poll every tmux pane and composite them into ``self.state``.

        Returns True when the screen (content, layout, or cursor) changed.
        """
        panes = self.supervisor.capture_frame()
        if not panes:
            return False
        key = tuple(
            (p.pane_id, p.left, p.top, p.width, p.height, p.active,
             p.cursor_x, p.cursor_y, p.cursor_visible, p.content)
            for p in panes
        )
        if key == self._last_captured_ansi:
            return False
        self._last_captured_ansi = key
        with self._lock:
            composite_panes(self.state, panes)
        return True

    def _capture_loop(self):
        """Continuous frame rasterization and video streaming loop."""
        frame_interval = 1.0 / float(self.fps)
        last_cast_screen: Optional[str] = None
        while self._is_recording:
            t0 = time.time()
            try:
                tmux_changed = False
                if self.supervisor and self.supervisor.is_alive():
                    if isinstance(self.supervisor, TmuxSupervisor):
                        tmux_changed = self._capture_tmux_frame()
                    # PtySupervisor was constructed with state=self.state, so
                    # its reader thread has already parsed the child's bytes
                    # into the grid and fed the cast via _on_pty_output.

                    # Evaluate reactive screen triggers asynchronously without blocking frame capture
                    self.monitor.evaluate_and_react(self.supervisor, async_action=True)

                with self._lock:
                    # Mask a copy. The live grid stays unmasked so the next
                    # frame's masking starts from the real text (masking in
                    # place re-matched its own replacements and grew them).
                    snapshot = self.redactor.redacted_snapshot(self.state)

                    card = self._active_card
                    s_left = self._status_left
                    s_right = self._status_right
                    if self._speedup_factor > 1.0:
                        s_pill = self._speedup_indicator or (
                            f"⏩ {int(self._speedup_factor) if self._speedup_factor.is_integer() else self._speedup_factor}x"
                        )
                    else:
                        s_pill = self._status_pill
                    speedup = self._speedup_factor

                if self.asciicast:
                    if tmux_changed:
                        # The tmux backend sees snapshots, not the byte
                        # stream, so the cast gets a positioned full redraw
                        # of the (already masked) composited screen.
                        screen = serialize_screen(snapshot)
                        if screen != last_cast_screen:
                            self.asciicast.record_output(screen, already_redacted=True)
                            last_cast_screen = screen
                    self.asciicast.tick()

                # Time dilation via frame decimation:
                should_render = False
                if speedup <= 1.0:
                    should_render = True
                else:
                    self._frame_accumulator += 1.0
                    if self._frame_accumulator >= speedup:
                        self._frame_accumulator -= speedup
                        should_render = True

                if should_render:
                    # Render PyCairo frame to raw BGRA bytes and stream to FFmpeg
                    self._render_and_write_frame(
                        card=card,
                        s_left=s_left,
                        s_right=s_right,
                        s_pill=s_pill,
                        term_state=snapshot,
                    )

            except Exception as e:
                # Keep capturing through a bad frame, but say so once per
                # distinct error instead of hiding it.
                msg = f"{type(e).__name__}: {e}"
                if msg != getattr(self, "_last_capture_error", None):
                    self._last_capture_error = msg
                    self._log(f"⚠️ frame capture error: {msg}")

            elapsed = time.time() - t0
            sleep_time = max(0.002, frame_interval - elapsed)
            time.sleep(sleep_time)

    def _render_and_write_frame(
        self,
        card: Optional[Dict[str, Any]] = None,
        s_left: Optional[str] = None,
        s_right: Optional[str] = None,
        s_pill: str = "● LIVE TTY",
        term_state: Optional[TerminalState] = None,
    ) -> Optional[bytes]:
        """Render frame with CairoTerminalRenderer, write to FFmpeg, and update telemetry metrics."""
        if term_state is None:
            with self._lock:
                term_state = self.redactor.redacted_snapshot(self.state)
        frame_bytes = self.renderer.draw_frame(
            term_state=term_state,
            banner_card=card,
            status_left=s_left,
            status_right=s_right,
            status_pill=s_pill,
        )

        if self.ffmpeg_pipe and self.ffmpeg_pipe.is_open:
            self.ffmpeg_pipe.write_frame(frame_bytes)
            frame_cnt = self.ffmpeg_pipe.frame_count
            elapsed = frame_cnt / float(self.fps) if self.fps > 0 else 0.0
            if self.telemetry:
                self.telemetry.update_rendered_frame(
                    rendered_frames=frame_cnt,
                    elapsed_seconds=elapsed,
                )

        return frame_bytes

    def run(self) -> ScenarioReport:
        """Execute the full recording scenario."""
        start_time = time.time()
        error_msg = None

        try:
            self._setup_environment()

            # Initialize and start TelemetryServer.
            # It gets its own renderer: CairoTerminalRenderer reuses exactly one
            # ImageSurface, and a `peek --image` capture drawing onto the frame
            # thread's renderer would tear one or both images.
            self.telemetry_renderer = CairoTerminalRenderer(
                width=self.width,
                height=self.height,
                title=self.manifest.metadata.title,
                subtitle=self.manifest.metadata.subtitle,
                theme=self.manifest.metadata.theme,
                font_family=self.manifest.metadata.font,
                font_size=self.manifest.metadata.font_size,
            )
            self.telemetry_server = TelemetryServer(
                session_id=self.session_id,
                state=self.state,
                renderer=self.telemetry_renderer,
                metadata=self.telemetry_metadata,
                registry=self.telemetry_registry,
                session_dir=self.telemetry_session_dir,
                redactor=self.redactor,
            )
            self.telemetry = self.telemetry_server
            self.telemetry_registry.register(self.telemetry_metadata)
            self.telemetry_server.start()
            self._log(f"Started TelemetryServer: session={self.session_id}, sock={self.telemetry_metadata.socket_path}")

            # Initialize FFmpeg transcoder pipe
            self.ffmpeg_pipe = FFmpegPipe(
                output_file=self.output_file,
                width=self.width,
                height=self.height,
                fps=self.fps,
                crf=self.manifest.metadata.crf,
                preset=self.manifest.metadata.preset,
            )
            self.ffmpeg_pipe.open()
            self._log(f"Opened FFmpeg streaming pipe -> {self.output_file}")

            # Initialize optional Asciicast recorder
            if self.manifest.metadata.cast_output:
                cast_path = os.path.abspath(self.manifest.metadata.cast_output)
                self.asciicast = AsciicastRecorder(
                    filepath=cast_path,
                    width=self.renderer.cols,
                    height=self.renderer.rows,
                    title=self.manifest.metadata.title,
                    redactor=self.redactor,
                    clock=lambda: (self.ffmpeg_pipe.frame_count / float(self.fps)) if (self.ffmpeg_pipe and self.fps > 0) else 0.0,
                )
                self.asciicast.start()
                self._log(f"Started Asciicast logging -> {cast_path}")

            # Start continuous background video capture thread
            self._is_recording = True
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()

            # Execute timeline steps sequentially
            total_steps = len(self.manifest.timeline)
            for step_idx, step in enumerate(self.manifest.timeline):
                step_desc = (
                    step.params.get("desc")
                    or step.params.get("title")
                    or step.params.get("text")
                    or step.params.get("command")
                    or str(step.params)
                )
                if self.telemetry:
                    self.telemetry.update_step(
                        step_idx=step_idx,
                        total_steps=total_steps,
                        step_type=step.step_type,
                        step_desc=step_desc,
                    )
                self._execute_step(step, step_idx)

        except Exception as e:
            error_msg = str(e)
            self._log(f"❌ Scenario execution encountered error: {e}")
            raise
        finally:
            teardown_errors: List[str] = []
            encode_error: Optional[BaseException] = None

            def _step(name: str, fn) -> None:
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - teardown must continue
                    teardown_errors.append(f"{name}: {type(exc).__name__}: {exc}")
                    self._log(f"⚠️ teardown step '{name}' failed: {exc}")

            # Tear down telemetry server and unregister session
            if self.telemetry:
                status = "failed" if error_msg else "completed"
                _step("telemetry", lambda: self.telemetry.stop(status=status))
            if self.telemetry_registry:
                _step("registry", lambda: self.telemetry_registry.unregister(self.session_id))

            # Tear down capture loop and finalize encoding
            self._is_recording = False
            if self._capture_thread:
                _step("capture thread", lambda: self._capture_thread.join(timeout=2.0))

            # Wait for any active trigger actions to complete
            _step("trigger actions", lambda: self.monitor.wait_for_actions(timeout=2.0))

            if self.supervisor:
                _step("supervisor", self.supervisor.terminate)

            if self.asciicast:
                _step("asciicast", self.asciicast.close)

            if self.ffmpeg_pipe:
                try:
                    self.ffmpeg_pipe.close()
                except Exception as exc:
                    encode_error = exc
                    teardown_errors.append(f"ffmpeg: {type(exc).__name__}: {exc}")
                    self._log(f"❌ video encoding failed: {exc}")

            # Extract poster frame after video file is fully written and finalized
            poster_path = self.manifest.metadata.poster_output
            if poster_path and self.ffmpeg_pipe and encode_error is None and os.path.exists(self.output_file):
                poster_full_path = os.path.abspath(poster_path)

                def _poster():
                    if self.ffmpeg_pipe.extract_poster(poster_full_path, timestamp_sec=1.0):
                        self._log(f"Extracted poster thumbnail -> {poster_full_path}")
                    else:
                        raise RuntimeError(f"could not extract poster to {poster_full_path}")

                _step("poster", _poster)

            _step("environment cleanup", self._cleanup_environment)
            self._teardown_errors = teardown_errors

            # A recording that could not be encoded is a failure even if the
            # timeline ran; surface it unless another error is already
            # propagating.
            if encode_error is not None and error_msg is None:
                raise encode_error

        duration = time.time() - start_time
        frame_count = self.ffmpeg_pipe.frame_count if self.ffmpeg_pipe else 0
        file_size = os.path.getsize(self.output_file) if os.path.exists(self.output_file) else 0

        self._log(f"✅ Finished recording: {self.output_file} ({frame_count} frames, {duration:.1f}s, {file_size / 1024:.1f} KB)")
        injections = list(self.monitor.injections)
        if injections:
            self._log(f"⌨️  Screen triggers typed into the session {len(injections)} time(s):")
            for inj in injections:
                self._log(f"   +{inj['time'] - start_time:.2f}s  [{inj['trigger']}]  keys={inj['keys']!r}")

        return ScenarioReport(
            status="pass" if not error_msg else "error",
            duration_sec=duration,
            frame_count=frame_count,
            output_file=self.output_file,
            file_size_bytes=file_size,
            cast_file=self.manifest.metadata.cast_output,
            poster_file=self.manifest.metadata.poster_output,
            error_message=error_msg,
            conversation_id=self.active_conversation_id,
            workspace_dir=self._work_dir,
            session_id=self.session_id,
            injections=injections,
            teardown_errors=list(getattr(self, "_teardown_errors", [])),
        )

    def _execute_type(self, params: Dict[str, Any]):
        """Execute a type step with typing cadence, optional newline collapse, and key emission."""
        text = str(params.get("text") if params.get("text") is not None else params.get("value", ""))
        speed = float(params.get("speed", 0.035))
        jitter = float(params.get("jitter", 0.015))
        typos = float(params.get("typos", 0.0))
        send_key_after = params.get("send_key")
        pause_after = float(params.get("pause", params.get("reading_pause", 0.5)))
        collapse_newlines = bool(params.get("collapse_newlines", True))
        multiline = bool(params.get("multiline", False))

        if not self.supervisor:
            raise RuntimeError("Cannot type: No CLI session launched yet.")

        if collapse_newlines and not multiline:
            text = re.sub(r'[ \t]*[\r\n][ \t\r\n]*', ' ', text.strip('\r\n'))

        kg = KeystrokeGenerator(base_speed=speed, jitter=jitter, typo_rate=typos)
        for action_type, val, delay in kg.generate_keystroke_events(text):
            if action_type == "char":
                self.supervisor.send_text(val)
            elif action_type == "key":
                self.supervisor.send_key(val)
            elif action_type == "pause":
                pass
            time.sleep(delay)

        if send_key_after:
            time.sleep(0.1)
            self.supervisor.send_key(send_key_after)

        if pause_after > 0:
            time.sleep(pause_after)

    def _execute_send_key(self, params: Dict[str, Any], step_type: str = "send_key"):
        """Execute send_key action with polymorphic string/dict support and timing delays."""
        if not self.supervisor:
            raise RuntimeError("Cannot send key: No CLI session launched.")

        key_name = params.get("key") or params.get("value")
        if not key_name or not isinstance(key_name, str):
            raise ScenarioValidationError(
                f"Invalid '{step_type}' step: 'key' is required and must be a non-empty string, got {params}"
            )

        delay_before = float(params.get("delay_before", 0.0))

        # Extract delay_after (or pause_after or delay)
        delay_after = None
        for k in ("delay_after", "pause_after", "delay", "pause"):
            if params.get(k) is not None:
                delay_after = float(params[k])
                break
        if delay_after is None:
            if "delay_before" in params:
                delay_after = 0.0
            elif "value" in params and not any(k in params for k in ("delay_after", "pause_after", "delay")):
                delay_after = 0.3
            else:
                delay_after = 0.0

        if delay_before > 0:
            time.sleep(delay_before)

        self.supervisor.send_key(key_name)

        if delay_after > 0:
            time.sleep(delay_after)

    def _execute_inspect_modal(self, params: Dict[str, Any]):
        """Execute inspect_modal: open modal via command or shortcut, wait for render, display, and dismiss."""
        if not self.supervisor:
            raise RuntimeError("Cannot inspect modal: No CLI session launched.")

        open_command = params.get("open_command")
        open_key = params.get("open_key")
        wait_for_render = params.get("wait_for_render")
        display_duration = float(params.get("display_duration", params.get("duration", 2.0)))
        dismiss_key = params.get("dismiss_key", "Escape")
        pause_after = float(params.get("pause_after", params.get("pause", 0.5)))
        timeout = float(params.get("timeout", 10.0))

        if open_command:
            speed = float(params.get("speed", 0.03))
            kg = KeystrokeGenerator(base_speed=speed, jitter=0.01)
            for _, ch, delay in kg.generate_keystroke_events(open_command):
                self.supervisor.send_text(ch)
                time.sleep(delay)
            time.sleep(0.1)
            self.supervisor.send_key("Enter")
        elif open_key:
            self.supervisor.send_key(open_key)

        if wait_for_render:
            regex = re.compile(wait_for_render, re.IGNORECASE | re.MULTILINE)
            if not self.monitor.wait_for_text(regex, supervisor=self.supervisor, timeout=timeout):
                raise TimeoutError(
                    f"Modal render pattern '{wait_for_render}' not found within {timeout}s."
                )

        if display_duration > 0:
            time.sleep(display_duration)

        self.supervisor.send_key(dismiss_key)

        if pause_after > 0:
            time.sleep(pause_after)

    def _execute_edit_file(self, params: Dict[str, Any]):
        """Hermetic Vim driver for editing files on screen."""
        if not self.supervisor:
            raise RuntimeError("Cannot execute edit_file: No CLI session launched.")

        raw_path = params.get("path") or params.get("value")
        if not raw_path:
            raise ValueError("Missing file path for edit_file step.")

        # Resolve target file path relative to working directory or active child process cwd
        expanded_path = os.path.expanduser(raw_path)
        active_cwd = self._work_dir
        if hasattr(self.supervisor, "process") and self.supervisor.process and getattr(self.supervisor.process, "pid", None):
            try:
                proc_cwd = os.readlink(f"/proc/{self.supervisor.process.pid}/cwd")
                if os.path.isdir(proc_cwd):
                    active_cwd = proc_cwd
            except Exception:
                pass
        full_path = expanded_path if os.path.isabs(expanded_path) else os.path.join(active_cwd, expanded_path)
        os.makedirs(os.path.dirname(os.path.abspath(full_path)), exist_ok=True)
        file_exists = os.path.isfile(full_path)
        file_size = os.path.getsize(full_path) if file_exists else 0
        path = raw_path

        action = str(params.get("action", "replace")).lower()
        content = params.get("content", "")
        pause_after = float(params.get("pause_after", params.get("pause", 1.0)))

        # Launch vim with clean options: no vimrc, no viminfo, no swap, autoindent and paste mode
        vim_cmd = f"vim -u NONE -i NONE -n -c \"set noswapfile nocompatible syntax=on autoindent paste\" {path}"
        self.supervisor.send_input(vim_cmd + "\n")
        time.sleep(0.6)

        # Perform action
        if action == "replace":
            # IMPORTANT: In Vim, running :%d on an empty or new file causes 'E16: Invalid range'
            if file_size > 0:
                self.supervisor.send_input(":%d\r")
                time.sleep(0.3)
            self.supervisor.send_input("i")
        elif action == "append":
            self.supervisor.send_input("G")
            time.sleep(0.2)
            self.supervisor.send_input("o")
        elif action in ("insert", "prepend"):
            self.supervisor.send_input("i")
        else:
            self.supervisor.send_input("i")
        time.sleep(0.3)

        # Paste content using bracketed paste
        if content:
            self.supervisor.send_input(f"\x1b[200~{content}\x1b[201~")
            time.sleep(0.5)

        # Exit insert mode and save
        self.supervisor.send_input("\x1b")
        time.sleep(0.3)
        self.supervisor.send_input(":wq\r")
        time.sleep(0.6)

        if pause_after > 0:
            time.sleep(pause_after)

    def _execute_assert(self, params: Dict[str, Any]):
        """Evaluate output/screen assertions against terminal content."""
        if not self.supervisor:
            raise RuntimeError("Cannot execute assertion: No CLI session launched.")

        scope = str(params.get("scope", "visible")).lower()
        timeout = float(params.get("timeout", 5.0))
        on_fail = str(params.get("on_fail", "abort")).lower()

        contains = params.get("contains")
        not_contains = params.get("not_contains")
        pattern = params.get("pattern")
        negate = bool(params.get("negate", False))

        start_t = time.time()
        last_text = ""
        success = False

        while time.time() - start_t < timeout:
            if scope == "all":
                if hasattr(self.supervisor, "capture_plain"):
                    try:
                        last_text = self.supervisor.capture_plain(include_scrollback=True)
                    except TypeError:
                        last_text = self.supervisor.capture_plain()
                elif hasattr(self.state, "get_full_text"):
                    last_text = self.state.get_full_text()
                else:
                    last_text = self.supervisor.capture_plain()
            else:
                last_text = self.supervisor.capture_plain()

            passed = True
            if contains is not None:
                items = [contains] if isinstance(contains, str) else contains
                for item in items:
                    if str(item) not in last_text:
                        passed = False
                        break

            if passed and not_contains is not None:
                items = [not_contains] if isinstance(not_contains, str) else not_contains
                for item in items:
                    if str(item) in last_text:
                        passed = False
                        break

            if passed and pattern is not None:
                regex = re.compile(pattern, re.MULTILINE)
                found = bool(regex.search(last_text))
                if (negate and found) or (not negate and not found):
                    passed = False

            if passed:
                success = True
                break
            time.sleep(0.2)

        if not success:
            err_msg = (
                f"Assertion failed (scope={scope}, timeout={timeout}s).\n"
                f"Conditions not met (contains={contains}, not_contains={not_contains}, pattern={pattern}).\n"
                f"Screen buffer tail:\n{last_text[-500:] if last_text else '<empty>'}"
            )
            if on_fail == "warn":
                self._log(f"⚠️ {err_msg}")
            else:
                raise AssertionError(err_msg)

    def _execute_step(self, step: TimelineStep, index: int):
        """Execute a single timeline step."""
        st = step.step_type
        params = step.params
        self._log(f"Step {index + 1}: [{st}] {params}")

        # Check for inline speedup configuration on step
        step_speedup = params.get("speedup") if isinstance(params, dict) else None
        prev_speedup = (self._speedup_factor, self._speedup_indicator)
        if step_speedup is not None:
            self.set_speedup(step_speedup)

        try:
            self._dispatch_step(st, params)
        finally:
            if step_speedup is not None:
                self.set_speedup({"factor": prev_speedup[0], "indicator": prev_speedup[1]})

    def _dispatch_step(self, st: str, params: Dict[str, Any]):
        """Dispatch timeline step action."""
        if st in ("show_card", "card"):
            tag = params.get("tag", "MODULE")
            title = params.get("title", "")
            desc = params.get("desc", "")
            duration = float(params.get("duration", 2.5))
            with self._lock:
                self._active_card = {"tag": tag, "title": title, "desc": desc}
            time.sleep(duration)
            with self._lock:
                self._active_card = None

        elif st == "launch":
            cmd = params.get("command") or params.get("value", "bash")
            env_vars = self.manifest.environment.env.copy()
            if "env" in params:
                env_vars.update(params["env"])

            # Check for session/conversation resumption
            should_resume = bool(params.get("resume", self.manifest.environment.resume))
            conv_id = params.get("conversation_id", self.manifest.environment.conversation_id)

            if should_resume or conv_id:
                if ("agy" in cmd or cmd.startswith("agy")) and "--continue" not in cmd and "-c" not in cmd and "--conversation" not in cmd:
                    if conv_id:
                        cmd = f"{cmd} --conversation {conv_id}"
                    else:
                        cmd = f"{cmd} -c"
                    self._log(f"Resuming CLI session with: {cmd}")

            if self.supervisor:
                self.supervisor.terminate()

            self.supervisor = create_supervisor(
                backend=self.backend,
                command=cmd,
                cwd=self._work_dir,
                rows=self.renderer.rows,
                cols=self.renderer.cols,
                env=env_vars,
                # The PTY backend has no capture-pane equivalent: it must parse
                # into the same TerminalState the renderer draws from, or every
                # rendered frame is an empty grid.
                state=self.state,
                parser=self.parser,
                on_output=self._on_pty_output,
            )

            self.supervisor.start()
            self.monitor.supervisor = self.supervisor

            if params.get("wait_for_idle", False):
                timeout = float(params.get("timeout", 15.0))
                self.monitor.wait_for_idle(self.supervisor, timeout=timeout)

            wait_for_prompt = bool(params.get("wait_for_prompt", False))
            if wait_for_prompt:
                prompt_pattern = params.get("prompt_pattern", r"([$#>]\s*$|%\s*$)")
                prompt_timeout = float(params.get("prompt_timeout", 10.0))
                regex = re.compile(prompt_pattern, re.MULTILINE)
                if not self.monitor.wait_for_text(regex, supervisor=self.supervisor, timeout=prompt_timeout):
                    raise TimeoutError(
                        f"Timed out waiting for prompt pattern '{prompt_pattern}' within {prompt_timeout}s."
                    )

        elif st == "type":
            self._execute_type(params)

        elif st in ("send_key", "key"):
            self._execute_send_key(params, step_type=st)

        elif st in ("send_keys", "keys"):
            keys_list = params.get("keys", params.get("value", []))
            if isinstance(keys_list, str):
                keys_list = [k.strip() for k in keys_list.split(",") if k.strip()]
            delay_between = float(params.get("delay", params.get("delay_between", 0.2)))
            pause_after = float(params.get("pause", 0.3))
            if not self.supervisor:
                raise RuntimeError("Cannot send keys: No CLI session launched.")
            for k in keys_list:
                self.supervisor.send_key(k)
                time.sleep(delay_between)
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "select_choice":
            choice_val = params.get("choice", params.get("value"))
            if choice_val is not None and str(choice_val).isdigit():
                steps = max(0, int(choice_val) - 1)
            else:
                steps = int(params.get("steps", params.get("times", 1)))
            direction = params.get("direction", "Down")
            confirm = bool(params.get("confirm", True))
            confirm_key = params.get("confirm_key", "Enter")
            pause_after = float(params.get("pause", 0.5))
            if not self.supervisor:
                raise RuntimeError("Cannot select choice: No CLI session launched.")
            for _ in range(steps):
                self.supervisor.send_key(direction)
                time.sleep(0.2)
            if confirm:
                time.sleep(0.2)
                self.supervisor.send_key(confirm_key)
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "shortcut":
            key_name = params.get("key") or params.get("value", "C-o")
            pause_after = float(params.get("pause", 0.5))
            if not self.supervisor:
                raise RuntimeError("Cannot send shortcut: No CLI session launched.")
            self.supervisor.send_key(key_name)
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "paste":
            text = params.get("text") or params.get("value", "")
            pause_after = float(params.get("pause", 0.5))
            if not self.supervisor:
                raise RuntimeError("Cannot paste: No CLI session launched.")
            self.supervisor.paste_text(text)
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "wait_for_idle":
            timeout = float(params.get("timeout", 60.0))
            reading_pause = float(params.get("reading_pause", 1.5))
            idle_regex = params.get("idle_regex")
            busy_regex = params.get("busy_regex")
            wait_for_prompt = bool(params.get("wait_for_prompt", False))
            prompt_pattern = params.get("prompt_pattern", r"([$#>]\s*$|%\s*$)")
            prompt_timeout = float(params.get("prompt_timeout", timeout))
            if self.supervisor:
                self.monitor.wait_for_idle(
                    supervisor=self.supervisor,
                    timeout=timeout,
                    idle_regex=idle_regex,
                    busy_regex=busy_regex,
                )
                if wait_for_prompt:
                    regex = re.compile(prompt_pattern, re.MULTILINE)
                    if not self.monitor.wait_for_text(regex, supervisor=self.supervisor, timeout=prompt_timeout):
                        raise TimeoutError(
                            f"Prompt pattern '{prompt_pattern}' not found on screen after idle within {prompt_timeout}s."
                        )
            if reading_pause > 0:
                time.sleep(reading_pause)

        elif st in ("wait_for_text", "wait"):
            pattern = params.get("pattern") or params.get("value", "")
            timeout = float(params.get("timeout", 30.0))
            pause_after = float(params.get("pause", 0.5))
            if self.supervisor:
                self.monitor.wait_for_text(pattern=pattern, supervisor=self.supervisor, timeout=timeout)
            if pause_after > 0:
                time.sleep(pause_after)

        elif st in ("pause", "sleep"):
            sec = float(params.get("seconds", params.get("value", 1.0)))
            time.sleep(sec)

        elif st in ("run_shell", "exec"):
            cmd = params.get("command") or params.get("value", "")
            speed = float(params.get("speed", 0.03))
            pause_after = float(params.get("pause", 1.0))
            if self.supervisor:
                # Type command into active terminal and hit Enter
                kg = KeystrokeGenerator(base_speed=speed, jitter=0.01)
                for _, ch, delay in kg.generate_keystroke_events(cmd):
                    self.supervisor.send_text(ch)
                    time.sleep(delay)
                time.sleep(0.1)
                self.supervisor.send_key("Enter")
            if pause_after > 0:
                time.sleep(pause_after)

            # Check assert_output if configured on run_shell
            assert_spec = params.get("assert_output") or params.get("assert")
            if assert_spec and self.supervisor:
                if isinstance(assert_spec, str):
                    assert_params = {"contains": assert_spec}
                elif isinstance(assert_spec, dict):
                    assert_params = dict(assert_spec)
                elif isinstance(assert_spec, list):
                    assert_params = {"contains": assert_spec}
                else:
                    assert_params = {"contains": str(assert_spec)}
                assert_params.setdefault("scope", "all")
                self._execute_assert(assert_params)

        elif st in ("assert", "assert_output", "assert_screen"):
            self._execute_assert(params)

        elif st in ("speedup", "timelapse"):
            self.set_speedup(params)
            pause_after = float(params.get("pause", 0.0))
            if pause_after > 0:
                time.sleep(pause_after)

        elif st in ("edit_file", "edit"):
            self._execute_edit_file(params)

        elif st in ("split_pane", "split"):
            direction = params.get("direction", "horizontal")
            percent = int(params.get("percent", 50))
            cmd_pane = params.get("command")
            pause_after = float(params.get("pause", 0.5))
            if isinstance(self.supervisor, TmuxSupervisor):
                self.supervisor.split_pane(direction=direction, percent=percent, command=cmd_pane)
            else:
                self._log(f"⚠️ split_pane requested but supervisor is not TmuxSupervisor (backend={self.backend})")
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "select_pane":
            pane_idx = int(params.get("pane_index", params.get("value", 0)))
            pause_after = float(params.get("pause", 0.5))
            if isinstance(self.supervisor, TmuxSupervisor):
                self.supervisor.select_pane(pane_index=pane_idx)
            else:
                self._log(f"⚠️ select_pane requested but supervisor is not TmuxSupervisor (backend={self.backend})")
            if pause_after > 0:
                time.sleep(pause_after)

        elif st == "close_pane":
            pane_idx = params.get("pane_index", params.get("value"))
            idx = int(pane_idx) if pane_idx is not None else None
            pause_after = float(params.get("pause", 0.5))
            if isinstance(self.supervisor, TmuxSupervisor):
                self.supervisor.close_pane(pane_index=idx)
            else:
                self._log(f"⚠️ close_pane requested but supervisor is not TmuxSupervisor (backend={self.backend})")
            if pause_after > 0:
                time.sleep(pause_after)

        elif st in ("wait_for_hook_event", "wait_hook"):
            ev_type = params.get("event") or params.get("event_type") or params.get("value", "")
            tool = params.get("tool") or params.get("tool_name")
            decision = params.get("decision")
            timeout = float(params.get("timeout", 30.0))
            strict = bool(params.get("strict", params.get("fail_on_timeout", False)))
            pause_after = float(params.get("pause", params.get("reading_pause", 0.5)))
            ev = self.hook_bridge.wait_for_event(event_type=ev_type, tool_name=tool, decision=decision, timeout=timeout)
            if not ev:
                if strict:
                    raise TimeoutError(f"Hook event '{ev_type}' (tool={tool}, decision={decision}) did not arrive within {timeout}s.")
                self._log(f"⚠️ Warning: Hook event '{ev_type}' (tool={tool}) did not arrive within {timeout}s.")
            if pause_after > 0:
                time.sleep(pause_after)

        elif st in ("assert_hook_event", "assert_hook"):
            ev_type = params.get("event") or params.get("event_type") or params.get("value", "")
            tool = params.get("tool") or params.get("tool_name")
            decision = params.get("decision")
            timeout = float(params.get("timeout", 5.0))
            negate = bool(params.get("negate", False))
            if negate:
                self.hook_bridge.assert_event_absent(event_type=ev_type, tool_name=tool, decision=decision, timeout=timeout)
            else:
                self.hook_bridge.assert_event_present(event_type=ev_type, tool_name=tool, decision=decision, timeout=timeout)

        elif st == "set_statusbar":
            with self._lock:
                if "left" in params:
                    self._status_left = params["left"]
                if "right" in params:
                    self._status_right = params["right"]
                if "pill" in params:
                    self._status_pill = params["pill"]

        elif st == "inspect_modal":
            self._execute_inspect_modal(params)
