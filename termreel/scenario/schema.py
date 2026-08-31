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
        model_config = ConfigDict(extra="ignore", populate_by_name=True, arbitrary_types_allowed=True)

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
        auto_approve_dialogs: bool = True
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
        redactions: List[str] = Field(default_factory=list)
        triggers: List[TriggerConfig] = Field(default_factory=list)
        timeline: List[TimelineStep] = Field(default_factory=list)

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
        auto_approve_dialogs: bool = True
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
        redactions: List[str] = field(default_factory=list)
        triggers: List[TriggerConfig] = field(default_factory=list)
        timeline: List[TimelineStep] = field(default_factory=list)


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
    "assert",
    "wait_for_hook_event", "wait_hook",
    "assert_hook_event", "assert_hook",
    "set_statusbar",
    "inspect_modal",
}


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

    perms = env_dict.get("permissions") if "permissions" in env_dict else data.get("permissions")
    settings_cfg = env_dict.get("settings") if "settings" in env_dict else data.get("settings")

    auto_dialogs = env_dict.get(
        "auto_approve_dialogs",
        env_dict.get("agy_auto_approve", data.get("auto_approve_dialogs", True))
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
    if not isinstance(redactions, list):
        raise ScenarioValidationError(f"Invalid 'redactions': expected list, got {type(redactions).__name__}")

    triggers_data = data.get("triggers", [])
    if not isinstance(triggers_data, list):
        raise ScenarioValidationError(f"Invalid 'triggers': expected list, got {type(triggers_data).__name__}")

    triggers = []
    for t in triggers_data:
        if not isinstance(t, dict):
            raise ScenarioValidationError(f"Invalid trigger format: expected dictionary, got {type(t).__name__}")
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
                    f"Unknown timeline step action: '{step_key}' at step {idx + 1}. Supported actions: {', '.join(sorted(VALID_ACTIONS))}"
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
            elif isinstance(step_val, dict):
                params = step_val
            elif isinstance(step_val, list):
                params = {"commands": step_val}
            else:
                params = {"value": step_val}

            timeline.append(TimelineStep(step_type=step_key, params=params))

    return ScenarioManifest(
        version=str(data.get("version", "1.0")),
        metadata=metadata,
        environment=environment,
        redactions=redactions,
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


