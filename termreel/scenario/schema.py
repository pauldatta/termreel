"""
Declarative YAML/JSON scenario manifest schema and timeline step definitions.
Engineered with Pydantic v2 structured validation and dual dataclass compatibility.
"""

from typing import Dict, List, Optional, Tuple, Any, Union
import os
import re
import yaml
from termreel.exceptions import ScenarioValidationError

try:
    from pydantic import BaseModel, Field, ConfigDict
    PYDANTIC_AVAILABLE = True
except ImportError:
    from dataclasses import dataclass, field
    BaseModel = object
    PYDANTIC_AVAILABLE = False


if PYDANTIC_AVAILABLE:
    class SchemaBase(BaseModel):
        model_config = ConfigDict(extra="allow", populate_by_name=True, arbitrary_types_allowed=True)

    class ScenarioMetadata(SchemaBase):
        title: str = "TermReel Workshop"
        subtitle: str = "Live CLI Execution"
        output: str = "output/session.mp4"
        resolution: Tuple[int, int] = (1280, 720)
        fps: int = 30
        theme: str = "catppuccin-mocha"
        font: str = "DejaVu Sans Mono"
        font_size: float = 14.5
        crf: int = 20
        preset: str = "medium"
        cast_output: Optional[str] = None
        poster_output: Optional[str] = None
        statusbar_left: Optional[str] = None
        statusbar_right: Optional[str] = None
        cols: Optional[int] = None
        rows: Optional[int] = None

    class ScenarioEnvironment(SchemaBase):
        cwd: Optional[str] = None
        env: Dict[str, str] = Field(default_factory=dict)
        create_temp_workspace: bool = False
        temp_workspace_prefix: str = "termreel_ws_"
        auto_trust: bool = True
        auto_approve_dialogs: bool = False
        setup_commands: List[str] = Field(default_factory=list)
        cleanup_commands: List[str] = Field(default_factory=list)
        hooks: Optional[Union[List[Any], Dict[str, Any]]] = None
        agy_hooks: bool = True
        agy_auto_approve: bool = True
        agy_event_bridge: bool = True
        agy_custom_policy: Dict[str, str] = Field(default_factory=dict)
        permissions: Optional[Union[List[str], Dict[str, Any]]] = None
        settings: Optional[Dict[str, Any]] = None
        resume: bool = False
        conversation_id: Optional[str] = None
        preserve_workspace: bool = False
        workspace_path: Optional[str] = None

    class TriggerConfig(SchemaBase):
        on_match: str
        action: Union[str, Dict[str, Any], List[Any]]
        once: bool = True
        cooldown: float = 1.0
        max_firings: int = 1
        max_count: Optional[int] = None
        delay_before: float = 0.0
        delay_after: float = 0.3
        edge: Optional[str] = None

        def model_post_init(self, __context: Any) -> None:
            if self.max_count is not None:
                self.max_firings = self.max_count
            else:
                self.max_count = self.max_firings
            if self.max_firings > 1 and self.once:
                self.once = False
            try:
                re.compile(self.on_match)
            except re.error as e:
                raise ScenarioValidationError(
                    f"Malformed trigger regex pattern '{self.on_match}': {e}"
                )

    class SendKeyParams(SchemaBase):
        key: str
        delay_before: float = 0.0
        delay_after: float = 0.0
        pause_after: float = 0.0
        delay: float = 0.0
        pause: float = 0.3

    class LaunchParams(SchemaBase):
        command: str = "bash"
        env: Dict[str, str] = Field(default_factory=dict)
        wait_for_idle: bool = False
        timeout: float = 15.0
        wait_for_prompt: bool = False
        prompt_pattern: str = r"([$#>]\s*$|%\s*$)"
        prompt_timeout: float = 10.0

    class WaitForIdleParams(SchemaBase):
        timeout: float = 60.0
        reading_pause: float = 1.5
        idle_regex: Optional[str] = None
        busy_regex: Optional[str] = None
        wait_for_prompt: bool = False
        prompt_pattern: str = r"([$#>]\s*$|%\s*$)"

    class InspectModalParams(SchemaBase):
        open_command: Optional[str] = None
        open_key: Optional[str] = None
        wait_for_render: Optional[str] = None
        display_duration: float = 2.0
        dismiss_key: str = "Escape"
        pause_after: float = 0.5
        timeout: float = 10.0

    class TimelineStep(SchemaBase):
        step_type: str
        params: Dict[str, Any] = Field(default_factory=dict)

    class ScenarioManifest(SchemaBase):
        version: str = "1.0"
        metadata: ScenarioMetadata = Field(default_factory=ScenarioMetadata)
        environment: ScenarioEnvironment = Field(default_factory=ScenarioEnvironment)
        redactions: Union[List[Any], Dict[str, Any]] = Field(default_factory=list)
        mask: Optional[Union[Dict[str, Any], List[Any]]] = None
        triggers: List[TriggerConfig] = Field(default_factory=list)
        timeline: List[TimelineStep] = Field(default_factory=list)
        source_file: Optional[str] = None

else:
    @dataclass
    class ScenarioMetadata:
        title: str = "TermReel Workshop"
        subtitle: str = "Live CLI Execution"
        output: str = "output/session.mp4"
        resolution: Tuple[int, int] = (1280, 720)
        fps: int = 30
        theme: str = "catppuccin-mocha"
        font: str = "DejaVu Sans Mono"
        font_size: float = 14.5
        crf: int = 20
        preset: str = "medium"
        cast_output: Optional[str] = None
        poster_output: Optional[str] = None
        statusbar_left: Optional[str] = None
        statusbar_right: Optional[str] = None
        cols: Optional[int] = None
        rows: Optional[int] = None

    @dataclass
    class ScenarioEnvironment:
        cwd: Optional[str] = None
        env: Dict[str, str] = field(default_factory=dict)
        create_temp_workspace: bool = False
        temp_workspace_prefix: str = "termreel_ws_"
        auto_trust: bool = True
        auto_approve_dialogs: bool = False
        setup_commands: List[str] = field(default_factory=list)
        cleanup_commands: List[str] = field(default_factory=list)
        hooks: Optional[Union[List[Any], Dict[str, Any]]] = None
        agy_hooks: bool = True
        agy_auto_approve: bool = True
        agy_event_bridge: bool = True
        agy_custom_policy: Dict[str, str] = field(default_factory=dict)
        permissions: Optional[Union[List[str], Dict[str, Any]]] = None
        settings: Optional[Dict[str, Any]] = None
        resume: bool = False
        conversation_id: Optional[str] = None
        preserve_workspace: bool = False
        workspace_path: Optional[str] = None

    @dataclass
    class TriggerConfig:
        on_match: str
        action: Union[str, Dict[str, Any], List[Any]]
        once: bool = True
        cooldown: float = 1.0
        max_firings: int = 1
        max_count: Optional[int] = None
        delay_before: float = 0.0
        delay_after: float = 0.3
        edge: Optional[str] = None

        def __post_init__(self):
            if self.max_count is not None:
                self.max_firings = self.max_count
            else:
                self.max_count = self.max_firings
            if self.max_firings > 1 and self.once:
                self.once = False
            try:
                re.compile(self.on_match)
            except re.error as e:
                raise ScenarioValidationError(
                    f"Malformed trigger regex pattern '{self.on_match}': {e}"
                )

    @dataclass
    class SendKeyParams:
        key: str
        delay_before: float = 0.0
        delay_after: float = 0.0
        pause_after: float = 0.0
        delay: float = 0.0
        pause: float = 0.3

    @dataclass
    class LaunchParams:
        command: str = "bash"
        env: Dict[str, str] = field(default_factory=dict)
        wait_for_idle: bool = False
        timeout: float = 15.0
        wait_for_prompt: bool = False
        prompt_pattern: str = r"([$#>]\s*$|%\s*$)"
        prompt_timeout: float = 10.0

    @dataclass
    class WaitForIdleParams:
        timeout: float = 60.0
        reading_pause: float = 1.5
        idle_regex: Optional[str] = None
        busy_regex: Optional[str] = None
        wait_for_prompt: bool = False
        prompt_pattern: str = r"([$#>]\s*$|%\s*$)"

    @dataclass
    class InspectModalParams:
        open_command: Optional[str] = None
        open_key: Optional[str] = None
        wait_for_render: Optional[str] = None
        display_duration: float = 2.0
        dismiss_key: str = "Escape"
        pause_after: float = 0.5
        timeout: float = 10.0

    @dataclass
    class TimelineStep:
        step_type: str
        params: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ScenarioManifest:
        version: str = "1.0"
        metadata: ScenarioMetadata = field(default_factory=ScenarioMetadata)
        environment: ScenarioEnvironment = field(default_factory=ScenarioEnvironment)
        redactions: Union[List[Any], Dict[str, Any]] = field(default_factory=list)
        mask: Optional[Union[Dict[str, Any], List[Any]]] = None
        triggers: List[TriggerConfig] = field(default_factory=list)
        timeline: List[TimelineStep] = field(default_factory=list)
        source_file: Optional[str] = None


VALID_ACTIONS = {
    "show_card", "card",
    "launch",
    "type",
    "send_key", "key",
    "send_keys", "keys",
    "select_choice",
    "shortcut",
    "paste",
    "wait_for_idle",
    "wait_for_text", "wait",
    "pause", "sleep",
    "run_shell", "exec",
    "assert", "assert_output", "assert_screen",
    "speedup", "timelapse",
    "edit_file", "edit",
    "split_pane", "split",
    "select_pane",
    "close_pane",
    "wait_for_hook_event", "wait_hook",
    "assert_hook_event", "assert_hook",
    "set_statusbar",
    "inspect_modal",
}


# Keys parse_manifest_dict actually reads. Anything else in these mappings is
# dropped, so in strict mode (the default for YAML files) it is an error
# instead of a silent fallback to defaults.
TOP_LEVEL_KEYS = {
    "version", "metadata", "environment", "redactions", "mask", "triggers", "timeline",
    # Legacy top-level spellings that parse_manifest_dict still honours.
    "theme", "fps", "cols", "rows", "dimensions", "permissions", "settings",
    "auto_approve_dialogs", "resume", "conversation_id", "preserve_workspace",
    "workspace_path",
}
METADATA_KEYS = {
    "title", "subtitle", "output", "resolution", "fps", "theme", "font", "font_size",
    "crf", "preset", "cast_output", "poster_output", "statusbar_left", "statusbar_right",
    "cols", "rows", "dimensions",
}
ENVIRONMENT_KEYS = {
    "cwd", "env", "create_temp_workspace", "temp_workspace_prefix", "auto_trust",
    "auto_approve_dialogs", "auto_approve", "setup_commands", "cleanup_commands", "hooks",
    "agy_hooks", "agy_auto_approve", "agy_event_bridge", "agy_custom_policy",
    "permissions", "settings", "resume", "conversation_id", "preserve_workspace",
    "workspace_path",
}
TRIGGER_KEYS = {
    "on_match", "pattern", "match", "action", "once", "cooldown", "max_count",
    "max_firings", "delay_before", "delay_after", "delay", "edge",
}
# Union of every step parameter the runner reads. Step parameters are only
# rejected when they are unknown *and* close to one of these (a likely typo
# such as ``txt`` for ``text``), because handlers read parameters in many
# places and an exhaustive per-step whitelist would reject valid scenarios.
STEP_PARAM_KEYS = {
    "action", "busy_regex", "choice", "collapse_newlines", "command", "confirm",
    "confirm_key", "contains", "content", "conversation_id", "decision", "delay",
    "delay_after", "delay_before", "delay_between", "desc", "direction", "dismiss_key",
    "display_duration", "duration", "env", "event", "event_type", "factor",
    "fail_on_timeout", "file", "fps", "idle_regex", "jitter", "key", "keys", "left",
    "multiline", "negate", "not_contains", "on_fail", "open_command", "open_key",
    "output", "pane_index", "path", "pattern", "pause", "pause_after", "percent", "pill",
    "prompt_pattern", "prompt_timeout", "reading_pause", "resume", "right", "scope",
    "seconds", "send_key", "speed", "speedup", "steps", "strict", "tag", "text",
    "timeout", "times", "title", "tool", "tool_name", "typos", "value", "wait_for_idle",
    "wait_for_prompt", "wait_for_render", "commands", "indicator", "assert", "assert_output",
}
# Step parameters that are regular expressions and must compile.
STEP_REGEX_KEYS = ("pattern", "idle_regex", "busy_regex", "prompt_pattern", "wait_for_render")


def _suggest(key: str, known) -> str:
    import difflib
    close = difflib.get_close_matches(str(key), sorted(known), n=1, cutoff=0.6)
    return f" Did you mean '{close[0]}'?" if close else ""


def _check_keys(mapping: Dict[str, Any], known, where: str, strict: bool) -> None:
    if not strict:
        return
    unknown = [k for k in mapping if k not in known]
    if not unknown:
        return
    key = unknown[0]
    raise ScenarioValidationError(
        f"Unknown key '{key}' in {where}.{_suggest(key, known)} "
        f"Valid keys: {', '.join(sorted(known))}"
    )


def _check_step_params(params: Dict[str, Any], step_key: str, idx: int, strict: bool) -> None:
    import difflib
    for k, v in params.items():
        if strict and k not in STEP_PARAM_KEYS:
            close = difflib.get_close_matches(str(k), sorted(STEP_PARAM_KEYS), n=1, cutoff=0.75)
            if close:
                raise ScenarioValidationError(
                    f"Unknown parameter '{k}' in '{step_key}' step {idx + 1}. Did you mean '{close[0]}'?"
                )
        regex_keys = STEP_REGEX_KEYS + (("value",) if step_key in ("wait_for_text", "wait") else ())
        if k in regex_keys and isinstance(v, str):
            try:
                re.compile(v)
            except re.error as e:
                raise ScenarioValidationError(
                    f"Invalid regex in '{step_key}' step {idx + 1} parameter '{k}': {v!r}: {e}"
                )



def _validate_step_keys(step_key: str, params: Dict[str, Any], idx: int) -> None:
    """Reject key specs neither backend can send, at load time."""
    from termreel.exceptions import KeySpecError
    from termreel.utils.keystrokes import parse_key_spec

    specs: List[Any] = []
    if step_key in ("send_key", "key", "shortcut"):
        specs.append(params.get("key") or params.get("value"))
    elif step_key in ("send_keys", "keys"):
        keys_list = params.get("keys", params.get("value", []))
        if isinstance(keys_list, str):
            keys_list = [k.strip() for k in keys_list.split(",") if k.strip()]
        if not isinstance(keys_list, list):
            raise ScenarioValidationError(
                f"Invalid '{step_key}' step {idx + 1}: expected a list of keys, got {keys_list!r}"
            )
        specs.extend(keys_list)
    elif step_key == "select_choice":
        specs.append(params.get("direction", "Down"))
        specs.append(params.get("confirm_key", "Enter"))
    elif step_key == "inspect_modal":
        specs.append(params.get("dismiss_key", "Escape"))
        if params.get("open_key"):
            specs.append(params.get("open_key"))
    if step_key in ("type",) and params.get("send_key"):
        specs.append(params.get("send_key"))
    for spec in specs:
        if spec is None:
            continue
        try:
            parse_key_spec(str(spec))
        except KeySpecError as e:
            raise ScenarioValidationError(f"Invalid key in '{step_key}' step {idx + 1}: {e}")


def parse_manifest_dict(data: Dict[str, Any], strict: bool = False) -> ScenarioManifest:
    """Parse dictionary data into validated ScenarioManifest."""
    if not isinstance(data, dict):
        raise ScenarioValidationError(
            f"Scenario manifest root must be a dictionary/mapping, got {type(data).__name__}"
        )

    if strict:
        if "version" not in data or data["version"] is None or not str(data["version"]).strip():
            raise ScenarioValidationError("Missing mandatory field: 'version'")
        if "timeline" not in data or data["timeline"] is None:
            raise ScenarioValidationError("Missing mandatory field: 'timeline'")
    _check_keys(data, TOP_LEVEL_KEYS, "the scenario root", strict)

    timeline_data = data.get("timeline")
    if timeline_data is None:
        timeline_data = []
    elif not isinstance(timeline_data, list):
        raise ScenarioValidationError(
            f"Invalid 'timeline': expected a list of steps, got {type(timeline_data).__name__}"
        )

    meta_dict = data.get("metadata", {})
    if not isinstance(meta_dict, dict):
        raise ScenarioValidationError(
            f"Invalid 'metadata': expected a dictionary, got {type(meta_dict).__name__}"
        )
    _check_keys(meta_dict, METADATA_KEYS, "'metadata'", strict)

    # Validate Theme
    theme_val = meta_dict.get("theme", data.get("theme", "catppuccin-mocha"))
    if theme_val:
        from termreel.renderer.themes import list_themes
        valid_themes = list_themes()
        if str(theme_val).lower().strip() not in valid_themes:
            raise ScenarioValidationError(
                f"Unrecognized theme '{theme_val}'. Available themes: {', '.join(sorted(valid_themes))}"
            )

    # Validate FPS
    fps_val = meta_dict.get("fps", data.get("fps", 30))
    try:
        fps_int = int(fps_val)
        if fps_int <= 0:
            raise ScenarioValidationError(
                f"Invalid FPS: {fps_val}. FPS must be a positive integer greater than 0."
            )
    except (ValueError, TypeError):
        raise ScenarioValidationError(f"Invalid FPS: {fps_val}. FPS must be a positive integer.")

    # Validate Dimensions (cols, rows, resolution)
    cols = (
        meta_dict.get("cols")
        or (meta_dict.get("dimensions", {}).get("cols") if isinstance(meta_dict.get("dimensions"), dict) else None)
        or data.get("cols")
        or (data.get("dimensions", {}).get("cols") if isinstance(data.get("dimensions"), dict) else None)
    )
    if cols is not None:
        try:
            cols_int = int(cols)
            if cols_int < 10:
                raise ScenarioValidationError(
                    f"Invalid dimensions: cols={cols_int} is too small. Terminal columns must be at least 10."
                )
        except (ValueError, TypeError):
            raise ScenarioValidationError(f"Invalid dimensions: cols={cols} must be an integer.")

    rows = (
        meta_dict.get("rows")
        or (meta_dict.get("dimensions", {}).get("rows") if isinstance(meta_dict.get("dimensions"), dict) else None)
        or data.get("rows")
        or (data.get("dimensions", {}).get("rows") if isinstance(data.get("dimensions"), dict) else None)
    )
    if rows is not None:
        try:
            rows_int = int(rows)
            if rows_int < 5:
                raise ScenarioValidationError(
                    f"Invalid dimensions: rows={rows_int} is too small. Terminal rows must be at least 5."
                )
        except (ValueError, TypeError):
            raise ScenarioValidationError(f"Invalid dimensions: rows={rows} must be an integer.")

    res = meta_dict.get("resolution", [1280, 720])
    if isinstance(res, (list, tuple)):
        if len(res) != 2:
            raise ScenarioValidationError(
                f"Invalid resolution: {res}. Expected [width, height] tuple/list of length 2."
            )
        try:
            w, h = int(res[0]), int(res[1])
            if w <= 0 or h <= 0:
                raise ScenarioValidationError(
                    f"Invalid resolution: [{w}, {h}]. Width and height must be positive integers."
                )
            res_tuple = (w, h)
        except (ValueError, TypeError):
            raise ScenarioValidationError(f"Invalid resolution: {res}. Width and height must be integers.")
    else:
        raise ScenarioValidationError(f"Invalid resolution: {res}. Expected [width, height] tuple/list.")

    metadata = ScenarioMetadata(
        title=meta_dict.get("title", "TermReel Workshop"),
        subtitle=meta_dict.get("subtitle", "Live CLI Execution"),
        output=meta_dict.get("output", "output/session.mp4"),
        resolution=res_tuple,
        fps=fps_int,
        theme=str(theme_val).lower().strip() if theme_val else "catppuccin-mocha",
        font=meta_dict.get("font", "DejaVu Sans Mono"),
        font_size=float(meta_dict.get("font_size", 14.5)),
        crf=int(meta_dict.get("crf", 20)),
        preset=meta_dict.get("preset", "medium"),
        cast_output=meta_dict.get("cast_output"),
        poster_output=meta_dict.get("poster_output"),
        statusbar_left=meta_dict.get("statusbar_left"),
        statusbar_right=meta_dict.get("statusbar_right"),
        cols=int(cols) if cols is not None else None,
        rows=int(rows) if rows is not None else None,
    )

    env_dict = data.get("environment", {})
    if not isinstance(env_dict, dict):
        raise ScenarioValidationError(f"Invalid 'environment': expected dictionary, got {type(env_dict).__name__}")
    _check_keys(env_dict, ENVIRONMENT_KEYS, "'environment'", strict)

    perms = env_dict.get("permissions") if "permissions" in env_dict else data.get("permissions")
    settings_cfg = env_dict.get("settings") if "settings" in env_dict else data.get("settings")

    # Typing into the session on TermReel's own initiative is opt-in.
    # ``auto_approve`` is accepted as an alias because the docs used it.
    # ``agy_auto_approve`` only controls the agy hook policy; it used to turn
    # screen auto-approve on as a side effect.
    auto_dialogs = env_dict.get(
        "auto_approve_dialogs",
        env_dict.get("auto_approve", data.get("auto_approve_dialogs", False))
    )

    environment = ScenarioEnvironment(
        cwd=env_dict.get("cwd"),
        env=env_dict.get("env", {}),
        create_temp_workspace=bool(env_dict.get("create_temp_workspace", False)),
        temp_workspace_prefix=env_dict.get("temp_workspace_prefix", "termreel_ws_"),
        auto_trust=bool(env_dict.get("auto_trust", True)),
        auto_approve_dialogs=bool(auto_dialogs),
        setup_commands=env_dict.get("setup_commands", []),
        cleanup_commands=env_dict.get("cleanup_commands", []),
        hooks=env_dict.get("hooks"),
        agy_hooks=bool(env_dict.get("agy_hooks", True)),
        agy_auto_approve=bool(env_dict.get("agy_auto_approve", True)),
        agy_event_bridge=bool(env_dict.get("agy_event_bridge", True)),
        agy_custom_policy=env_dict.get("agy_custom_policy", {}),
        permissions=perms,
        settings=settings_cfg,
        resume=bool(env_dict.get("resume", data.get("resume", False))),
        conversation_id=env_dict.get("conversation_id", data.get("conversation_id")),
        preserve_workspace=bool(env_dict.get("preserve_workspace", data.get("preserve_workspace", False))),
        workspace_path=env_dict.get("workspace_path", data.get("workspace_path")),
    )

    redactions = data.get("redactions", [])
    if redactions is not None and not isinstance(redactions, (list, dict)):
        raise ScenarioValidationError(f"Invalid 'redactions': expected list or dict, got {type(redactions).__name__}")

    mask = data.get("mask", None)
    if mask is not None and not isinstance(mask, (list, dict)):
        raise ScenarioValidationError(f"Invalid 'mask': expected list or dict, got {type(mask).__name__}")

    if redactions or mask:
        # Build the scenario's own rules now so a bad regex fails validation
        # instead of failing (closed) only when recording starts.
        from termreel.exceptions import MaskConfigError
        from termreel.mask.engine import MaskEngine
        try:
            MaskEngine.create(load_global=False, use_default_patterns=False,
                              redactions=redactions, mask=mask)
        except MaskConfigError as exc:
            raise ScenarioValidationError(str(exc)) from exc

    triggers_data = data.get("triggers", [])
    if not isinstance(triggers_data, list):
        raise ScenarioValidationError(f"Invalid 'triggers': expected list, got {type(triggers_data).__name__}")

    triggers = []
    for t in triggers_data:
        if not isinstance(t, dict):
            raise ScenarioValidationError(f"Invalid trigger format: expected dictionary, got {type(t).__name__}")
        _check_keys(t, TRIGGER_KEYS, f"trigger {len(triggers) + 1}", strict)
        pat = t.get("on_match") or t.get("pattern") or t.get("match")
        if pat is None or not str(pat).strip():
            raise ScenarioValidationError(f"Trigger missing regex pattern: {t}")
        try:
            re.compile(str(pat))
        except re.error as e:
            raise ScenarioValidationError(f"Malformed trigger regex pattern '{pat}': {e}")

        raw_count = t.get("max_count")
        if raw_count is None:
            raw_count = t.get("max_firings")
        count_val = int(raw_count) if raw_count is not None else 1
        once_val = t.get("once")
        if once_val is not None:
            once = bool(once_val)
        else:
            once = (count_val == 1)

        edge = t.get("edge")
        if edge is not None:
            edge = str(edge).strip().lower()
            if edge in ("", "none", "level"):
                edge = None
            elif edge not in ("presence", "line"):
                raise ScenarioValidationError(
                    f"Invalid trigger edge {t.get('edge')!r}: use 'presence', 'line' or omit it"
                )

        triggers.append(
            TriggerConfig(
                on_match=str(pat),
                action=t.get("action", "Enter"),
                once=once,
                cooldown=float(t.get("cooldown", 1.0)),
                max_firings=count_val,
                max_count=count_val,
                delay_before=float(t.get("delay_before", 0.0)),
                delay_after=float(t.get("delay_after", t.get("delay", 0.3))),
                edge=edge,
            )
        )

    timeline = []
    for idx, item in enumerate(timeline_data):
        if not isinstance(item, dict):
            raise ScenarioValidationError(
                f"Invalid timeline step at index {idx}: expected dictionary mapping step action to parameters, got {type(item).__name__}: {item}"
            )
        if not item:
            raise ScenarioValidationError(f"Invalid timeline step at index {idx}: step dictionary cannot be empty.")

        for step_key, step_val in item.items():
            if step_key not in VALID_ACTIONS:
                raise ScenarioValidationError(
                    f"Unknown timeline step action: '{step_key}' at step {idx + 1}.{_suggest(step_key, VALID_ACTIONS)} Supported actions: {', '.join(sorted(VALID_ACTIONS))}"
                )

            # Check numeric duration / timeout parameters for negative values
            if isinstance(step_val, (int, float)) and step_val < 0:
                raise ScenarioValidationError(
                    f"Invalid parameter in '{step_key}' step at index {idx}: duration/value cannot be negative, got {step_val}"
                )
            if isinstance(step_val, dict):
                for dur_key in ("duration", "seconds", "display_duration", "timeout", "delay", "pause"):
                    if dur_key in step_val:
                        try:
                            val_f = float(step_val[dur_key])
                            if val_f < 0:
                                raise ScenarioValidationError(
                                    f"Invalid parameter in '{step_key}' step at index {idx}: '{dur_key}' cannot be negative, got {val_f}"
                                )
                        except (ValueError, TypeError):
                            raise ScenarioValidationError(
                                f"Invalid parameter in '{step_key}' step at index {idx}: '{dur_key}' must be numeric, got {step_val[dur_key]}"
                            )

            # Mandatory command in launch or run_shell / exec
            if step_key == "launch":
                cmd = None
                if isinstance(step_val, dict):
                    cmd = step_val.get("command") or step_val.get("value")
                elif isinstance(step_val, str):
                    cmd = step_val
                if not cmd or not str(cmd).strip():
                    raise ScenarioValidationError(
                        f"Missing command in '{step_key}' step at index {idx}: 'command' must be a non-empty string."
                    )

            if step_key in ("run_shell", "exec"):
                cmd = None
                if isinstance(step_val, dict):
                    cmd = step_val.get("command") or step_val.get("value")
                elif isinstance(step_val, str):
                    cmd = step_val
                if not cmd or not str(cmd).strip():
                    raise ScenarioValidationError(
                        f"Missing command in '{step_key}' step at index {idx}: 'command' must be a non-empty string."
                    )

            if step_key in ("send_key", "key"):
                if isinstance(step_val, dict):
                    if "key" not in step_val or not step_val["key"]:
                        raise ScenarioValidationError(
                            f"Invalid '{step_key}' step: structured dictionary must contain a non-empty 'key' field, got: {step_val}"
                        )
                    params = dict(step_val)
                elif isinstance(step_val, str):
                    params = {"value": step_val, "key": step_val}
                else:
                    raise ScenarioValidationError(
                        f"Invalid '{step_key}' step: expected string or dictionary, got {type(step_val).__name__}: {step_val}"
                    )
            elif step_key in ("edit_file", "edit"):
                if isinstance(step_val, dict):
                    if not step_val.get("path"):
                        raise ScenarioValidationError(
                            f"Missing 'path' in '{step_key}' step at index {idx}: file path is required."
                        )
                    params = dict(step_val)
                elif isinstance(step_val, str):
                    params = {"path": step_val}
                else:
                    raise ScenarioValidationError(
                        f"Invalid '{step_key}' step at index {idx}: expected dictionary or file path string."
                    )
            elif step_key in ("speedup", "timelapse"):
                if isinstance(step_val, (int, float)):
                    if step_val <= 0:
                        raise ScenarioValidationError(f"Invalid speedup factor at index {idx}: must be positive, got {step_val}")
                    params = {"factor": float(step_val)}
                elif isinstance(step_val, dict):
                    params = dict(step_val)
                    factor = float(params.get("factor", params.get("value", 2.0)))
                    if factor <= 0:
                        raise ScenarioValidationError(f"Invalid speedup factor at index {idx}: must be positive, got {factor}")
                else:
                    raise ScenarioValidationError(f"Invalid '{step_key}' step at index {idx}: expected number or dictionary.")
            elif step_key in ("assert", "assert_output", "assert_screen"):
                if isinstance(step_val, str):
                    params = {"contains": step_val}
                elif isinstance(step_val, dict):
                    params = dict(step_val)
                elif isinstance(step_val, list):
                    params = {"contains": step_val}
                else:
                    params = {"value": step_val}
            elif step_key in ("split_pane", "split"):
                if isinstance(step_val, str):
                    params = {"direction": step_val}
                elif isinstance(step_val, dict):
                    params = dict(step_val)
                else:
                    params = {"direction": "horizontal"}
            elif step_key == "select_pane":
                if isinstance(step_val, int):
                    params = {"pane_index": step_val}
                elif isinstance(step_val, dict):
                    params = dict(step_val)
                else:
                    params = {"pane_index": int(step_val)}
            elif step_key == "close_pane":
                if isinstance(step_val, int):
                    params = {"pane_index": step_val}
                elif isinstance(step_val, dict):
                    params = dict(step_val)
                else:
                    params = {}
            elif isinstance(step_val, dict):
                params = step_val
            elif isinstance(step_val, list):
                if step_key in ("send_keys", "keys"):
                    params = {"keys": step_val}
                else:
                    params = {"commands": step_val}
            else:
                params = {"value": step_val}
            if isinstance(params, dict):
                _check_step_params(params, step_key, idx, strict)
                _validate_step_keys(step_key, params, idx)

            timeline.append(TimelineStep(step_type=step_key, params=params))

    return ScenarioManifest(
        version=str(data.get("version", "1.0")),
        metadata=metadata,
        environment=environment,
        redactions=redactions if redactions is not None else [],
        mask=mask,
        triggers=triggers,
        timeline=timeline,
    )


# Attach from_dict / from_yaml helpers to ScenarioManifest class
ScenarioManifest.from_dict = staticmethod(parse_manifest_dict)


def _from_yaml_file(cls, filepath: str, strict: bool = True) -> ScenarioManifest:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Scenario file not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    manifest = cls.from_yaml_string(content, strict=strict)
    manifest.source_file = filepath
    return manifest


def _from_yaml_string(cls, yaml_string: str, strict: bool = True) -> ScenarioManifest:
    try:
        data = yaml.safe_load(yaml_string)
    except yaml.YAMLError as e:
        raise ScenarioValidationError(f"Malformed YAML syntax: {e}") from e

    if data is None:
        raise ScenarioValidationError("Scenario YAML content is empty.")
    if not isinstance(data, dict):
        raise ScenarioValidationError(
            f"Scenario manifest root must be a dictionary/mapping, got {type(data).__name__}"
        )
    return cls.from_dict(data, strict=strict)


def _validate_manifest(cls, data: Union[str, Dict[str, Any]]) -> None:
    """Validate scenario dictionary or YAML string against schema."""
    if isinstance(data, str):
        cls.from_yaml_string(data, strict=True)
    elif isinstance(data, dict):
        cls.from_dict(data, strict=True)
    else:
        raise ScenarioValidationError(f"Expected dictionary or YAML string, got {type(data).__name__}")


ScenarioManifest.from_yaml_file = classmethod(_from_yaml_file)
ScenarioManifest.from_yaml_string = classmethod(_from_yaml_string)
ScenarioManifest.from_yaml_str = classmethod(_from_yaml_string)
ScenarioManifest.validate_manifest = classmethod(_validate_manifest)


