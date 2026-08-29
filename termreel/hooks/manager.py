"""
Hook lifecycle manager responsible for provisioning and cleaning up .agents/hooks.json
and custom hook handler scripts in the workspace.
"""

import json
import os
import shutil
import stat
from typing import Any, Dict, List, Optional
from termreel.hooks.bridge import AgyHookBridge
from termreel.hooks.presets import generate_hook_script, create_agy_hooks_config


class HookManager:
    """
    Provisions and manages Antigravity (agy) hooks in a workspace directory.
    """

    def __init__(
        self,
        workspace_dir: str,
        bridge: Optional[AgyHookBridge] = None,
        auto_approve: bool = True,
        log_events: bool = True,
        custom_policy: Optional[Dict[str, str]] = None,
        custom_hooks_config: Optional[Dict[str, Any]] = None,
    ):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.bridge = bridge
        self.auto_approve = auto_approve
        self.log_events = log_events
        self.custom_policy = custom_policy or {}
        self.custom_hooks_config = custom_hooks_config

        self.agents_dir = os.path.join(self.workspace_dir, ".agents")
        self.hooks_dir = os.path.join(self.agents_dir, "hooks")
        self.hooks_json_path = os.path.join(self.agents_dir, "hooks.json")
        self.hook_script_path = os.path.join(self.hooks_dir, "termreel_hook.py")

        self.events_file = (
            self.bridge.events_file
            if (self.bridge and self.bridge.events_file)
            else os.path.join(self.workspace_dir, ".termreel_events.jsonl")
        )

        self._created_files: List[str] = []
        self._created_dirs: List[str] = []
        self._orig_hooks_json_backup: Optional[str] = None
        self._is_provisioned = False

    def provision(self) -> Dict[str, str]:
        """Deploy hook script and .agents/hooks.json into the target workspace."""
        if self._is_provisioned:
            return {
                "hooks_json": self.hooks_json_path,
                "hook_script": self.hook_script_path,
                "events_file": self.events_file,
            }

        os.makedirs(self.hooks_dir, exist_ok=True)
        if not os.path.exists(self.agents_dir):
            self._created_dirs.append(self.agents_dir)

        # 1. Generate hook script
        script_code = generate_hook_script(
            bridge_file=self.events_file,
            auto_approve=self.auto_approve,
            log_events=self.log_events,
            custom_policy=self.custom_policy,
        )
        with open(self.hook_script_path, "w", encoding="utf-8") as f:
            f.write(script_code)

        # Ensure executable permissions
        try:
            st = os.stat(self.hook_script_path)
            os.chmod(self.hook_script_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass

        self._created_files.append(self.hook_script_path)

        # 2. Backup existing hooks.json if present
        if os.path.exists(self.hooks_json_path):
            self._orig_hooks_json_backup = self.hooks_json_path + ".termreel_backup"
            shutil.copy2(self.hooks_json_path, self._orig_hooks_json_backup)

        # 3. Write hooks.json
        if self.custom_hooks_config:
            hooks_data = self.custom_hooks_config
        else:
            hooks_data = create_agy_hooks_config(hook_script_path=self.hook_script_path)

        with open(self.hooks_json_path, "w", encoding="utf-8") as f:
            json.dump(hooks_data, f, indent=2)

        self._created_files.append(self.hooks_json_path)

        # 4. Attach bridge file if bridge is active
        if self.bridge:
            self.bridge.events_file = self.events_file
            self.bridge.start()

        self._is_provisioned = True
        return {
            "hooks_json": self.hooks_json_path,
            "hook_script": self.hook_script_path,
            "events_file": self.events_file,
        }

    def cleanup(self):
        """Clean up deployed hook artifacts and restore original hooks.json if needed."""
        if self.bridge:
            self.bridge.stop()

        # Restore original hooks.json if backed up
        if self._orig_hooks_json_backup and os.path.exists(self._orig_hooks_json_backup):
            try:
                shutil.move(self._orig_hooks_json_backup, self.hooks_json_path)
            except Exception:
                pass
        else:
            # Delete generated hooks.json
            if os.path.exists(self.hooks_json_path):
                try:
                    os.remove(self.hooks_json_path)
                except Exception:
                    pass

        # Delete generated hook script
        if os.path.exists(self.hook_script_path):
            try:
                os.remove(self.hook_script_path)
            except Exception:
                pass

        # Delete generated events file
        if os.path.exists(self.events_file):
            try:
                os.remove(self.events_file)
            except Exception:
                pass

        # Remove empty hooks/ or .agents/ dirs if we created them
        if os.path.exists(self.hooks_dir) and not os.listdir(self.hooks_dir):
            try:
                os.rmdir(self.hooks_dir)
            except Exception:
                pass

        if os.path.exists(self.agents_dir) and not os.listdir(self.agents_dir):
            try:
                os.rmdir(self.agents_dir)
            except Exception:
                pass

        self._is_provisioned = False

    def __enter__(self):
        self.provision()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
