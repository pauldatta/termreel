"""
Declarative YAML/JSON scenario manifest schema and timeline step definitions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Union
import os
import yaml


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
    setup_commands: List[str] = field(default_factory=list)
    cleanup_commands: List[str] = field(default_factory=list)


@dataclass
class TriggerConfig:
    on_match: str
    action: Union[str, Dict[str, Any], List[Any]]
    once: bool = True
    cooldown: float = 1.0
    max_firings: int = 1


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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioManifest":
        meta_dict = data.get("metadata", {})
        res = meta_dict.get("resolution", [1280, 720])
        if isinstance(res, list) and len(res) == 2:
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
        environment = ScenarioEnvironment(
            cwd=env_dict.get("cwd"),
            env=env_dict.get("env", {}),
            create_temp_workspace=bool(env_dict.get("create_temp_workspace", False)),
            temp_workspace_prefix=env_dict.get("temp_workspace_prefix", "termreel_ws_"),
            setup_commands=env_dict.get("setup_commands", []),
            cleanup_commands=env_dict.get("cleanup_commands", []),
        )

        redactions = data.get("redactions", [])

        triggers_data = data.get("triggers", [])
        triggers = []
        for t in triggers_data:
            if isinstance(t, dict) and "on_match" in t:
                triggers.append(
                    TriggerConfig(
                        on_match=t["on_match"],
                        action=t.get("action", "Enter"),
                        once=t.get("once", True),
                        cooldown=float(t.get("cooldown", 1.0)),
                        max_firings=int(t.get("max_firings", 1)),
                    )
                )

        timeline_data = data.get("timeline", [])
        timeline = []
        for item in timeline_data:
            if isinstance(item, dict):
                # Standard syntax: {action_name: params} or {type: '...', params...}
                for k, v in item.items():
                    if k in ("show_card", "card", "launch", "type", "send_key", "key", "paste",
                             "wait_for_idle", "wait_for_text", "wait", "pause", "sleep",
                             "run_shell", "exec", "assert", "set_statusbar", "screenshot", "poster"):
                        params = v if isinstance(v, dict) else {"value": v}
                        timeline.append(TimelineStep(step_type=k, params=params))
                        break
                    elif k == "action":
                        timeline.append(TimelineStep(step_type=str(v), params=item))
                        break
                else:
                    # Generic step
                    timeline.append(TimelineStep(step_type="custom", params=item))

        return cls(
            version=str(data.get("version", "1.0")),
            metadata=metadata,
            environment=environment,
            redactions=redactions,
            triggers=triggers,
            timeline=timeline,
        )

    @classmethod
    def from_yaml_file(cls, filepath: str) -> "ScenarioManifest":
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})

    @classmethod
    def from_yaml_str(cls, yaml_str: str) -> "ScenarioManifest":
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data or {})
