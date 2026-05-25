from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generic, ParamSpec, TypeVar

from .models import AgentEvent, ApprovalRequest, ApprovalStatus, Decision, EventType

if TYPE_CHECKING:
    from .supervisor import SecuritySupervisor

P = ParamSpec("P")
R = TypeVar("R")


class ToolCallBlocked(RuntimeError):
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(decision.summary)


class HumanApprovalRequired(RuntimeError):
    def __init__(
        self,
        decision: Decision,
        approval_request: ApprovalRequest | None = None,
    ) -> None:
        self.decision = decision
        self.approval_request = approval_request
        super().__init__(decision.summary)


@dataclass
class GuardedTool(Generic[P, R]):
    supervisor: SecuritySupervisor
    tool_name: str
    tool: Callable[P, R]
    default_metadata: dict[str, Any] = field(default_factory=dict)

    def __call__(
        self,
        *args: P.args,
        agent_id: str,
        task_id: str | None = None,
        **kwargs: P.kwargs,
    ) -> R:
        metadata = {
            **self.default_metadata,
            "tool_name": self.tool_name,
            "tool_args": {
                "args": args,
                "kwargs": kwargs,
                "bound_args": self._bind_tool_args(*args, **kwargs),
            },
        }
        event = AgentEvent(
            agent_id=agent_id,
            task_id=task_id,
            event_type=EventType.TOOL_CALL,
            content=f"Agent requested tool call: {self.tool_name}.",
            metadata=metadata,
        )
        decision = self.supervisor.observe(event)

        if decision.enforced and decision.requires_human_approval:
            approval_request = self.supervisor.create_approval_request(event, decision)
            raise HumanApprovalRequired(decision, approval_request)
        if decision.enforced and not decision.allowed:
            raise ToolCallBlocked(decision)

        return self.tool(*args, **kwargs)

    def execute_approved(self, request_id: str) -> R:
        approval_request = self.supervisor.approval_store.get(request_id)
        if approval_request is None:
            raise KeyError(f"Unknown approval request: {request_id}")
        if approval_request.tool_name != self.tool_name:
            raise ValueError(
                f"Approval request is for {approval_request.tool_name}, not {self.tool_name}"
            )
        if approval_request.status != ApprovalStatus.APPROVED:
            raise PermissionError(
                f"Approval request {request_id} is {approval_request.status.value}"
            )

        tool_args = approval_request.tool_args
        args = tool_args.get("args", [])
        kwargs = tool_args.get("kwargs", {})
        result = self.tool(*args, **kwargs)
        self.supervisor.mark_request_executed(request_id)
        return result

    def _bind_tool_args(self, *args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            signature = inspect.signature(self.tool)
            bound = signature.bind_partial(*args, **kwargs)
        except (TypeError, ValueError):
            return {}
        return dict(bound.arguments)
