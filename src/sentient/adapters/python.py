from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..sdk import GuardedTool
from ..supervisor import SecuritySupervisor
from .base import ToolCallContext


@dataclass
class PythonRuntimeAdapter:
    supervisor: SecuritySupervisor

    def wrap_tool(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_context: ToolCallContext | None = None,
    ) -> Callable[..., Any]:
        guarded_tool = self.supervisor.guard_tool(
            tool_name,
            tool,
            default_metadata=(
                default_context or ToolCallContext(agent_id="unknown-agent")
            ).to_metadata(),
        )

        def wrapped_tool(
            *args: Any,
            security_context: ToolCallContext | None = None,
            **kwargs: Any,
        ) -> Any:
            active_context = security_context or default_context
            if active_context is None:
                raise ValueError(
                    "ToolCallContext is required when no default_context is configured."
                )
            active_guarded_tool = self.supervisor.guard_tool(
                tool_name,
                tool,
                default_metadata=active_context.to_metadata(),
            )
            return active_guarded_tool(
                *args,
                agent_id=active_context.agent_id,
                task_id=active_context.task_id,
                **kwargs,
            )

        setattr(wrapped_tool, "guarded_tool", guarded_tool)
        setattr(wrapped_tool, "execute_approved", guarded_tool.execute_approved)
        return wrapped_tool

    def guard_tool(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_context: ToolCallContext,
    ) -> GuardedTool:
        return self.supervisor.guard_tool(
            tool_name,
            tool,
            default_metadata=default_context.to_metadata(),
        )
