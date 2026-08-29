"""
TermReel Hooks Subsystem for Antigravity (agy) and CLI Agent Interception.

Provides lifecycle hook provisioning, tool auto-approval policies, event streaming
and deterministic synchronization with CLI agent execution.
"""

from termreel.hooks.models import (
    HookEventType,
    HookDecision,
    HookResult,
    HookEvent,
    HookHandlerConfig,
)
from termreel.hooks.bridge import AgyHookBridge
from termreel.hooks.presets import (
    generate_hook_script,
    create_agy_hooks_config,
    create_auto_approve_policy,
)
from termreel.hooks.manager import HookManager

__all__ = [
    "HookEventType",
    "HookDecision",
    "HookResult",
    "HookEvent",
    "HookHandlerConfig",
    "AgyHookBridge",
    "generate_hook_script",
    "create_agy_hooks_config",
    "create_auto_approve_policy",
    "HookManager",
]
