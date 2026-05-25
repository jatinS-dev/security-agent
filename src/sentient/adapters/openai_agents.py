from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from ..supervisor import SecuritySupervisor
from .base import ToolCallContext


@dataclass
class OpenAIAgentsAdapter:
    supervisor: SecuritySupervisor
    function_tool_factory: Callable[..., Any] | None = None

    def wrap_function(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_context: ToolCallContext,
    ) -> Callable[..., Any]:
        guarded_tool = self.supervisor.guard_tool(
            tool_name,
            tool,
            default_metadata=default_context.to_metadata(),
        )

        @wraps(tool)
        def guarded_function(*args: Any, **kwargs: Any) -> Any:
            return guarded_tool(
                *args,
                agent_id=default_context.agent_id,
                task_id=default_context.task_id,
                **kwargs,
            )

        # The Agents SDK inspects the callable signature to build the tool schema.
        guarded_function.__signature__ = inspect.signature(tool)  # type: ignore[attr-defined]
        setattr(guarded_function, "guarded_tool", guarded_tool)
        setattr(guarded_function, "execute_approved", guarded_tool.execute_approved)
        return guarded_function

    def wrap_function_tool(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_context: ToolCallContext,
        **function_tool_kwargs: Any,
    ) -> Any:
        guarded_function = self.wrap_function(
            tool_name,
            tool,
            default_context=default_context,
        )
        factory = self.function_tool_factory or _load_function_tool()
        decorator = factory(
            name_override=tool_name,
            **function_tool_kwargs,
        )
        return decorator(guarded_function)


def _load_function_tool() -> Callable[..., Any]:
    try:
        from agents import function_tool
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "OpenAI Agents SDK is not installed. Install it or pass "
            "function_tool_factory for testing/custom runtimes."
        ) from error
    return function_tool

