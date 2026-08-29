"""
Declarative YAML/JSON scenario manifest schema and timeline step definitions.
Engineered with Pydantic v2 structured validation and dual dataclass compatibility.
"""

from typing import Dict, List, Optional, Tuple, Any, Union
import os
import yaml

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
        delay_before: float = 0.0
        delay_after: float = 0.3

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
        delay_before: float = 0.0
        delay_after: float = 0.3

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


def parse_manifest_dict(data: Dict[str, Any]) -> ScenarioManifest:
    """Parse dictionary data into validated ScenarioManifest."""
    meta_dict = data.get("metadata", {})
    res = meta_dict.get("resolution", [1280, 720])
    if isinstance(res, (list, tuple)) and len(res) == 2:
        res_tuple = (int(res[0]), int(res[1]))
    else:
        res_tuple = (1280, 720)

    metadata = ScenarioMetadata(
        title=meta_dict.get("title", "TermReel Workshop"),
        subtitle=meta_dict.get("subtitle", "Live CLI Execution"),
        output=meta_dict.get("output", "output/session.mp4"),
        resolution=res_tuple,
        fps=int(meta_dict.get("fps", 30)),
        theme=meta_dict.get("theme", "catppuccin-mocha"),
        font=meta_dict.get("font", "DejaVu Sans Mono"),
        font_size=float(meta_dict.get("font_size", 14.5)),
        crf=int(meta_dict.get("crf", 20)),
        preset=meta_dict.get("preset", "medium"),
        cast_output=meta_dict.get("cast_output"),
        poster_output=meta_dict.get("poster_output"),
        statusbar_left=meta_dict.get("statusbar_left"),
        statusbar_right=meta_dict.get("statusbar_right"),
    )

    env_dict = data.get("environment", {})
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

    triggers_data = data.get("triggers", [])
    triggers = []
    for t in triggers_data:
        if isinstance(t, dict):
            pat = t.get("on_match") or t.get("pattern") or t.get("match")
            if pat:
                triggers.append(
                    TriggerConfig(
                        on_match=pat,
                        action=t.get("action", "Enter"),
                        once=bool(t.get("once", True)),
                        cooldown=float(t.get("cooldown", 1.0)),
                        max_firings=int(t.get("max_firings", 1)),
                        delay_before=float(t.get("delay_before", 0.0)),
                        delay_after=float(t.get("delay_after", t.get("delay", 0.3))),
                    )
                )

    timeline_data = data.get("timeline", [])
    timeline = []
    for item in timeline_data:
        if isinstance(item, dict):
            for step_key, step_val in item.items():
                if isinstance(step_val, dict):
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


def _from_yaml_file(cls, filepath: str) -> ScenarioManifest:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Scenario file not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return cls.from_dict(data)


def _from_yaml_string(cls, yaml_string: str) -> ScenarioManifest:
    data = yaml.safe_load(yaml_string) or {}
    return cls.from_dict(data)


ScenarioManifest.from_yaml_file = classmethod(_from_yaml_file)
ScenarioManifest.from_yaml_string = classmethod(_from_yaml_string)
ScenarioManifest.from_yaml_str = classmethod(_from_yaml_string)

