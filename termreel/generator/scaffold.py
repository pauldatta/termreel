"""
Declarative YAML scenario generation and interactive scaffolding engine.
Transforms CLISpec models into production-ready TermReel scenario manifests.
"""

import os
from typing import Optional, Dict, Any, List, Tuple
import yaml
from termreel.generator.explorer import CLISpec, CLIExplorer
from termreel.renderer.themes import list_themes


class ScenarioGenerator:
    """
    Generates tailored, validated YAML scenario manifests from probed CLI tools.
    """

    @classmethod
    def generate(
        cls,
        spec: CLISpec,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        output_mp4: Optional[str] = None,
        theme: str = "catppuccin-mocha",
        fps: int = 30,
        resolution: Tuple[int, int] = (1280, 720),
    ) -> str:
        """Construct full YAML scenario manifest text."""
        cli_name = spec.name
        doc_title = title or f"{cli_name.upper()} Interactive Demonstration"
        doc_subtitle = subtitle or spec.summary
        doc_output = output_mp4 or f"output/{cli_name}_demo.mp4"
        poster_output = os.path.splitext(doc_output)[0] + "_poster.png"
        cast_output = os.path.splitext(doc_output)[0] + ".cast"

        manifest_data: Dict[str, Any] = {
            "version": "1.0",
            "metadata": {
                "title": doc_title,
                "subtitle": doc_subtitle,
                "output": doc_output,
                "poster_output": poster_output,
                "cast_output": cast_output,
                "resolution": list(resolution),
                "fps": fps,
                "theme": theme,
                "statusbar_left": f"{cli_name} | Real TTY | UTF-8",
                "statusbar_right": "TermReel HD",
            },
            "environment": {
                "create_temp_workspace": True,
                "temp_workspace_prefix": f"termreel_{cli_name}_ws_",
            },
        }

        if spec.suggested_setup_commands:
            manifest_data["environment"]["setup_commands"] = spec.suggested_setup_commands

        # Permissions
        if spec.recommended_permissions:
            manifest_data["permissions"] = {
                "auto_approve": True,
                "allow_commands": spec.recommended_permissions,
                "allow_tools": ["run_command", "write_to_file", "read_file", "grep_search"],
            }

        # Triggers
        triggers = []
        if spec.category == "agent":
            triggers.append({
                "on_match": "Do you trust the contents of this project|Yes, I trust|Trust project",
                "action": "Enter",
                "once": True,
            })
            triggers.append({
                "on_match": "Requesting permission for:|Do you want to proceed\\?|\\[y/N\\]",
                "action": {
                    "type": "send_key",
                    "value": "Enter",
                    "delay_before": 0.8,
                    "delay_after": 0.3,
                },
                "once": False,
                "cooldown": 1.5,
                "max_firings": 15,
            })
        manifest_data["triggers"] = triggers

        # Timeline
        timeline = []
        timeline.append({
            "show_card": {
                "tag": "Module 1",
                "title": doc_title,
                "desc": f"Exploring {cli_name} interactive workflows",
                "duration": 2.5,
            }
        })

        if spec.category == "agent":
            timeline.append({
                "launch": {
                    "command": cli_name,
                    "wait_for_idle": True,
                    "timeout": 25.0,
                }
            })
            for p in spec.inferred_prompts:
                timeline.append({
                    "type": {
                        "text": p["prompt"],
                        "speed": 0.035,
                        "jitter": 0.015,
                        "send_key": "Enter",
                    }
                })
                timeline.append({
                    "wait_for_idle": {
                        "timeout": 45.0,
                        "reading_pause": 2.5,
                    }
                })
            timeline.append({
                "type": {
                    "text": "/exit",
                    "speed": 0.04,
                    "send_key": "Enter",
                    "pause": 1.0,
                }
            })
        elif spec.category == "repl":
            timeline.append({
                "launch": {
                    "command": cli_name,
                }
            })
            for p in spec.inferred_prompts:
                timeline.append({
                    "type": {
                        "text": p["input"],
                        "speed": 0.035,
                        "send_key": "Enter",
                        "pause": 1.5,
                    }
                })
        else:
            timeline.append({
                "launch": {
                    "command": "bash",
                }
            })
            for p in spec.inferred_prompts:
                timeline.append({
                    "run_shell": {
                        "command": p["command"],
                        "speed": 0.03,
                        "pause": 1.5,
                    }
                })

        timeline.append({
            "show_card": {
                "tag": "Complete",
                "title": f"{cli_name} Session Completed",
                "desc": "Recorded deterministically via TermReel",
                "duration": 2.5,
            }
        })

        manifest_data["timeline"] = timeline
        return yaml.dump(manifest_data, sort_keys=False, default_flow_style=False)
