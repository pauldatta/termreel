"""
Data models and schemas for Antigravity (agy) hooks, lifecycle events, and decisions.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import time
from typing import Any, Dict, List, Optional, Union


class HookEventType(str, Enum):
    """Lifecycle hook event types supported by Antigravity (agy) and agent harness."""
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_INVOCATION = "PreInvocation"
    POST_INVOCATION = "PostInvocation"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    ON_INTERACTION = "OnInteraction"
    ON_TOOL_ERROR = "OnToolError"
    STOP = "Stop"

    @classmethod
    def from_string(cls, name: str) -> "HookEventType":
        """Normalize event name string (case-insensitive and format-tolerant)."""
        clean = name.replace("_", "").lower()
        mapping = {
            "pretooluse": cls.PRE_TOOL_USE,
            "pretool": cls.PRE_TOOL_USE,
            "posttooluse": cls.POST_TOOL_USE,
            "posttool": cls.POST_TOOL_USE,
            "preinvocation": cls.PRE_INVOCATION,
            "preturn": cls.PRE_INVOCATION,
            "postinvocation": cls.POST_INVOCATION,
            "postturn": cls.POST_INVOCATION,
            "sessionstart": cls.SESSION_START,
            "sessionend": cls.SESSION_END,
            "oninteraction": cls.ON_INTERACTION,
            "interaction": cls.ON_INTERACTION,
            "ontoolerror": cls.ON_TOOL_ERROR,
            "toolerror": cls.ON_TOOL_ERROR,
            "stop": cls.STOP,
        }
        if clean in mapping:
            return mapping[clean]
        return cls(name)


class HookDecision(str, Enum):
    """Standard hook verdict decisions."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    AUTO_APPROVE = "auto_approve"


@dataclass
class HookResult:
    """Verdict returned by a hook handler to Antigravity CLI."""
    allow: bool = True
    decision: str = "allow"
    message: Optional[str] = None
    modified_args: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "allow": self.allow,
            "decision": self.decision,
        }
        if self.message is not None:
            d["message"] = self.message
        if self.modified_args is not None:
            d["modified_args"] = self.modified_args
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookResult":
        allow = bool(data.get("allow", True))
        decision = str(data.get("decision", "allow" if allow else "deny"))
        return cls(
            allow=allow,
            decision=decision,
            message=data.get("message"),
            modified_args=data.get("modified_args"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class HookEvent:
    """Structured telemetry event captured from an active hook invocation."""
    event_type: str
    timestamp: float = field(default_factory=time.time)
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_output: Optional[Any] = None
    session_id: Optional[str] = None
    prompt: Optional[str] = None
    response: Optional[str] = None
    decision: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_output": self.tool_output,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "response": self.response,
            "decision": self.decision,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookEvent":
        return cls(
            event_type=str(data.get("event_type", data.get("event", "Unknown"))),
            timestamp=float(data.get("timestamp", time.time())),
            tool_name=data.get("tool_name") or data.get("tool"),
            tool_args=data.get("tool_args") or data.get("args"),
            tool_output=data.get("tool_output") or data.get("output"),
            session_id=data.get("session_id"),
            prompt=data.get("prompt"),
            response=data.get("response"),
            decision=data.get("decision"),
            error_message=data.get("error_message") or data.get("error"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "HookEvent":
        data = json.loads(json_str)
        return cls.from_dict(data)


@dataclass
class HookHandlerConfig:
    """Configuration for an individual hook handler definition."""
    type: str = "command"
    command: Optional[str] = None
    timeout: float = 10.0
    matcher: str = "*"
    action: str = "allow"
    allow_tools: Optional[List[str]] = None
    deny_tools: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "timeout": self.timeout,
        }
        if self.command:
            d["command"] = self.command
        if self.matcher:
            d["matcher"] = self.matcher
        if self.action:
            d["action"] = self.action
        return d
