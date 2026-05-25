from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class ToolCallContext:
    agent_id: str
    task_id: str | None = None
    agent_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        if self.agent_role is not None:
            metadata["agent_role"] = self.agent_role
        return metadata


class AgentRuntimeAdapter(Protocol):
    def wrap_tool(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_context: ToolCallContext | None = None,
    ) -> Callable[..., Any]:
        raise NotImplementedError

