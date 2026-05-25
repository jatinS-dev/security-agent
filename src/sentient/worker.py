from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import ApprovalRequest, ApprovalStatus
from .stores import ApprovalStore


@dataclass(frozen=True)
class ApprovalExecutionResult:
    request_id: str
    tool_name: str
    executed: bool
    result: Any = None
    error: str | None = None


class ApprovalExecutionWorker:
    def __init__(
        self,
        approval_store: ApprovalStore,
        tools: dict[str, Callable[..., Any]],
    ) -> None:
        self.approval_store = approval_store
        self.tools = tools

    def run_once(self) -> list[ApprovalExecutionResult]:
        results: list[ApprovalExecutionResult] = []
        for approval in self.approval_store.list(ApprovalStatus.APPROVED):
            results.append(self._execute(approval))
        return results

    def _execute(self, approval: ApprovalRequest) -> ApprovalExecutionResult:
        tool = self.tools.get(approval.tool_name)
        if tool is None:
            return ApprovalExecutionResult(
                request_id=approval.request_id,
                tool_name=approval.tool_name,
                executed=False,
                error=f"No registered tool for approval: {approval.tool_name}",
            )

        try:
            result = _call_tool(tool, approval.tool_args)
        except Exception as error:
            return ApprovalExecutionResult(
                request_id=approval.request_id,
                tool_name=approval.tool_name,
                executed=False,
                error=str(error),
            )

        self.approval_store.update(
            approval.request_id,
            status=ApprovalStatus.EXECUTED,
            reviewed_at=approval.reviewed_at,
            reviewer=approval.reviewer,
            review_reason=approval.review_reason,
        )
        return ApprovalExecutionResult(
            request_id=approval.request_id,
            tool_name=approval.tool_name,
            executed=True,
            result=result,
        )


def _call_tool(tool: Callable[..., Any], tool_args: dict[str, Any]) -> Any:
    if "args" in tool_args or "kwargs" in tool_args:
        return tool(*tool_args.get("args", ()), **tool_args.get("kwargs", {}))
    if "bound_args" in tool_args:
        return tool(**tool_args["bound_args"])
    return tool(**tool_args)
