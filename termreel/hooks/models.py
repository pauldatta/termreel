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
    STOP = "Stop"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    ON_INTERACTION = "OnInteraction"
    ON_TOOL_ERROR = "OnToolError"

    @classmethod
    def from_string(cls, name: Optional[str]) -> "HookEventType":
        """Normalize event name string (case-insensitive and format-tolerant)."""
        if not name:
            return cls.PRE_TOOL_USE
        clean = name.replace("_", "").replace("-", "").lower()
        mapping = {
            "pretooluse": cls.PRE_TOOL_USE,
            "pretool": cls.PRE_TOOL_USE,
            "posttooluse": cls.POST_TOOL_USE,
            "posttool": cls.POST_TOOL_USE,
            "preinvocation": cls.PRE_INVOCATION,
            "preturn": cls.PRE_INVOCATION,
            "postinvocation": cls.POST_INVOCATION,
            "postturn": cls.POST_INVOCATION,
            "stop": cls.STOP,
            "sessionstart": cls.SESSION_START,
            "sessionend": cls.SESSION_END,
            "oninteraction": cls.ON_INTERACTION,
            "interaction": cls.ON_INTERACTION,
            "ontoolerror": cls.ON_TOOL_ERROR,
            "toolerror": cls.ON_TOOL_ERROR,
        }
        if clean in mapping:
            return mapping[clean]
        try:
            return cls(name)
        except ValueError:
            # Tolerant fallback: preserve provided name
            return cls.PRE_TOOL_USE


class HookDecision(str, Enum):
    """Standard hook verdict decisions."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    FORCE_ASK = "force_ask"
    CONTINUE = "continue"
    AUTO_APPROVE = "auto_approve"


@dataclass
class HookResult:
    """Verdict returned by a hook handler to Antigravity CLI."""
    allow: bool = True
    decision: str = "allow"
    reason: Optional[str] = None
    message: Optional[str] = None
    permission_overrides: Optional[List[str]] = None
    overwrite: Optional[Dict[str, Any]] = None
    modified_args: Optional[Dict[str, Any]] = None
    inject_steps: Optional[List[Dict[str, Any]]] = None
    termination_behavior: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "allow": self.allow,
            "decision": self.decision,
        }
        msg = self.reason or self.message
        if msg is not None:
            d["reason"] = msg
            d["message"] = msg
        if self.permission_overrides is not None:
            d["permissionOverrides"] = self.permission_overrides
        ow = self.overwrite or self.modified_args
        if ow is not None:
            d["overwrite"] = ow
            d["modified_args"] = ow
        if self.inject_steps is not None:
            d["injectSteps"] = self.inject_steps
        if self.termination_behavior is not None:
            d["terminationBehavior"] = self.termination_behavior
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookResult":
        decision = str(data.get("decision", "allow" if data.get("allow", True) else "deny"))
        allow = decision == "allow" if "decision" in data else bool(data.get("allow", True))
        reason = data.get("reason") or data.get("message")
        perm_overrides = data.get("permissionOverrides") or data.get("permission_overrides")
        overwrite = data.get("overwrite") or data.get("modified_args")
        inject_steps = data.get("injectSteps") or data.get("inject_steps")
        term_behavior = data.get("terminationBehavior") or data.get("termination_behavior")
        return cls(
            allow=allow,
            decision=decision,
            reason=reason,
            message=reason,
            permission_overrides=perm_overrides,
            overwrite=overwrite,
            modified_args=overwrite,
            inject_steps=inject_steps,
            termination_behavior=term_behavior,
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
    reason: Optional[str] = None
    error_message: Optional[str] = None
    step_idx: Optional[int] = None
    invocation_num: Optional[int] = None
    termination_reason: Optional[str] = None
    workspace_paths: Optional[List[str]] = None
    transcript_path: Optional[str] = None
    artifact_directory_path: Optional[str] = None
    model_name: Optional[str] = None
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
            "reason": self.reason,
            "error_message": self.error_message,
            "step_idx": self.step_idx,
            "invocation_num": self.invocation_num,
            "termination_reason": self.termination_reason,
            "workspace_paths": self.workspace_paths,
            "transcript_path": self.transcript_path,
            "artifact_directory_path": self.artifact_directory_path,
            "model_name": self.model_name,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookEvent":
        # Extract nested toolCall if sent via standard Antigravity protojson
        tool_call = data.get("toolCall") or data.get("tool_call") or {}
        if isinstance(tool_call, dict) and tool_call:
            tool_name = tool_call.get("name") or data.get("tool_name") or data.get("tool")
            tool_args = tool_call.get("args") or data.get("tool_args") or data.get("args")
        else:
            tool_name = data.get("tool_name") or data.get("tool") or data.get("name")
            tool_args = data.get("tool_args") or data.get("args") or data.get("arguments")

        session_id = (
            data.get("conversationId")
            or data.get("conversation_id")
            or data.get("session_id")
            or data.get("sessionId")
        )
        step_idx = data.get("stepIdx") or data.get("step_idx")
        invocation_num = data.get("invocationNum") or data.get("invocation_num")
        term_reason = data.get("terminationReason") or data.get("termination_reason")
        err_msg = data.get("error") or data.get("error_message") or data.get("errorMessage")
        reason = data.get("reason") or data.get("message")
        ws_paths = data.get("workspacePaths") or data.get("workspace_paths")
        tr_path = data.get("transcriptPath") or data.get("transcript_path")
        art_path = data.get("artifactDirectoryPath") or data.get("artifact_directory_path")
        model = data.get("modelName") or data.get("model_name")

        return cls(
            event_type=str(data.get("event_type", data.get("event", "Unknown"))),
            timestamp=float(data.get("timestamp", time.time())),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=data.get("tool_output") or data.get("toolOutput") or data.get("output"),
            session_id=session_id,
            prompt=data.get("prompt") or data.get("user_prompt") or data.get("userPrompt"),
            response=data.get("response") or data.get("model_response") or data.get("modelResponse"),
            decision=data.get("decision"),
            reason=reason,
            error_message=err_msg,
            step_idx=int(step_idx) if step_idx is not None else None,
            invocation_num=int(invocation_num) if invocation_num is not None else None,
            termination_reason=term_reason,
            workspace_paths=ws_paths,
            transcript_path=tr_path,
            artifact_directory_path=art_path,
            model_name=model,
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
