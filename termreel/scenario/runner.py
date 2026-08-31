"""
Scenario runner orchestrating environment lifecycle, PTY supervision,
keystroke injection, reactive triggers, and continuous video synthesis.
"""

from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Optional, Dict, Any, List, Union
from termreel.exceptions import ScenarioValidationError
from termreel.emulator.state import TerminalState
from termreel.emulator.parser import ANSIParser
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
        self.redactor = Redactor(custom_patterns=self.manifest.redactions)

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

        self._is_recording = False
        self._capture_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._temp_dir: Optional[str] = None
        self._work_dir: str = os.getcwd()
        self._last_captured_ansi = ""
        self.active_conversation_id: Optional[str] = self.manifest.environment.conversation_id

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
                )
            )

        # Auto-register workspace trust dialog trigger if enabled and not already configured
        if self.manifest.environment.auto_trust:
            has_trust_trigger = any("trust" in str(getattr(t, "pattern", "")).lower() for t in self.monitor.triggers)
            if not has_trust_trigger:
                self.monitor.add_trigger(create_trust_dialog_trigger(delay_before=0.4, delay_after=0.4))

        # Auto-register interactive permission dialog triggers if enabled and not already configured
        if self.manifest.environment.auto_approve_dialogs or self.manifest.environment.agy_auto_approve:
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

    def _capture_loop(self):
        """Continuous frame rasterization and video streaming loop."""
        frame_interval = 1.0 / float(self.fps)
        while self._is_recording:
            t0 = time.time()
            try:
                if self.supervisor and self.supervisor.is_alive():
                    # Capture screen
                    if isinstance(self.supervisor, TmuxSupervisor):
                        raw_ansi = self.supervisor.capture_ansi()
                        if raw_ansi:
                            with self._lock:
                                self.parser.feed_tmux_pane(raw_ansi)
                            if self.asciicast and raw_ansi != self._last_captured_ansi:
                                self.asciicast.record_output(raw_ansi)
                                self._last_captured_ansi = raw_ansi
                    else:
                        pass  # PtySupervisor updates state in real-time

                    # Evaluate reactive screen triggers asynchronously without blocking frame capture
                    self.monitor.evaluate_and_react(self.supervisor, async_action=True)

                with self._lock:
                    # Apply token/secret redactions
                    self.redactor.apply_to_terminal_state(self.state)

                    card = self._active_card
                    s_left = self._status_left
                    s_right = self._status_right
                    s_pill = self._status_pill

                # Render PyCairo frame to raw BGRA bytes
                frame_bytes = self.renderer.draw_frame(
                    term_state=self.state,
                    banner_card=card,
                    status_left=s_left,
                    status_right=s_right,
                    status_pill=s_pill,
                )

                # Stream directly to FFmpeg
                if self.ffmpeg_pipe and self.ffmpeg_pipe.is_open:
                    self.ffmpeg_pipe.write_frame(frame_bytes)

            except Exception as e:
                # Avoid breaking capture loop on minor frame jitter
                pass

            elapsed = time.time() - t0
            sleep_time = max(0.002, frame_interval - elapsed)
            time.sleep(sleep_time)

    def run(self) -> ScenarioReport:
        """Execute the full recording scenario."""
        start_time = time.time()
        error_msg = None

        try:
            self._setup_environment()

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
                )
                self.asciicast.start()
                self._log(f"Started Asciicast logging -> {cast_path}")

            # Start continuous background video capture thread
            self._is_recording = True
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()

            # Execute timeline steps sequentially
            for step_idx, step in enumerate(self.manifest.timeline):
                self._execute_step(step, step_idx)

        except Exception as e:
            error_msg = str(e)
            self._log(f"❌ Scenario execution encountered error: {e}")
            raise
        finally:
            # Tear down capture loop and finalize encoding
            self._is_recording = False
            if self._capture_thread:
                self._capture_thread.join(timeout=2.0)

            # Wait for any active trigger actions to complete
            self.monitor.wait_for_actions(timeout=2.0)

            if self.supervisor:
                self.supervisor.terminate()

            if self.asciicast:
                self.asciicast.close()

            if self.ffmpeg_pipe:
                self.ffmpeg_pipe.close()

            # Extract poster frame after video file is fully written and finalized
            poster_path = self.manifest.metadata.poster_output
            if poster_path and self.ffmpeg_pipe and os.path.exists(self.output_file):
                poster_full_path = os.path.abspath(poster_path)
                if self.ffmpeg_pipe.extract_poster(poster_full_path, timestamp_sec=1.0):
                    self._log(f"Extracted poster thumbnail -> {poster_full_path}")

            self._cleanup_environment()

        duration = time.time() - start_time
        frame_count = self.ffmpeg_pipe.frame_count if self.ffmpeg_pipe else 0
        file_size = os.path.getsize(self.output_file) if os.path.exists(self.output_file) else 0

        self._log(f"✅ Finished recording: {self.output_file} ({frame_count} frames, {duration:.1f}s, {file_size / 1024:.1f} KB)")

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

    def _execute_step(self, step: TimelineStep, index: int):
        """Execute a single timeline step."""
        st = step.step_type
        params = step.params
        self._log(f"Step {index + 1}: [{st}] {params}")

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

        elif st == "assert":
            pattern = params.get("pattern") or params.get("value", "")
            timeout = float(params.get("timeout", 10.0))
            negate = bool(params.get("negate", False))
            if self.supervisor:
                if negate:
                    self.monitor.assert_text_absent(pattern, supervisor=self.supervisor, timeout=timeout)
                else:
                    self.monitor.assert_text_present(pattern, supervisor=self.supervisor, timeout=timeout)

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
