from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import AgentEvent, ApprovalRequest, ApprovalStatus, Decision, EventType
from .supervisor import SecuritySupervisor


@dataclass(frozen=True)
class HttpToolRoute:
    tool_name: str
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    body_mode: str = "envelope"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HttpToolRoute":
        return cls(
            tool_name=str(data["tool_name"]),
            url=str(data["url"]),
            method=str(data.get("method", "POST")).upper(),
            headers={str(key): str(value) for key, value in data.get("headers", {}).items()},
            timeout_seconds=float(data.get("timeout_seconds", 10.0)),
            body_mode=str(data.get("body_mode", "envelope")),
        )


@dataclass(frozen=True)
class ToolProxyRequest:
    agent_id: str
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolProxyRequest":
        tool_args = data.get("tool_args", {})
        if not isinstance(tool_args, dict):
            raise ValueError("tool_args must be an object.")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object.")
        return cls(
            agent_id=str(data["agent_id"]),
            tool_name=str(data["tool_name"]),
            tool_args=tool_args,
            task_id=data.get("task_id"),
            content=data.get("content"),
            metadata=metadata,
        )

    def to_event(self) -> AgentEvent:
        return AgentEvent(
            agent_id=self.agent_id,
            task_id=self.task_id,
            event_type=EventType.TOOL_CALL,
            content=self.content or f"Agent requested proxied tool call: {self.tool_name}.",
            metadata={
                **self.metadata,
                "tool_name": self.tool_name,
                "tool_args": self.tool_args,
            },
        )


@dataclass(frozen=True)
class UpstreamToolResponse:
    status_code: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolProxyResult:
    status: str
    decision: Decision
    approval_request: ApprovalRequest | None = None
    upstream_response: UpstreamToolResponse | None = None


class ToolProxy:
    def __init__(
        self,
        supervisor: SecuritySupervisor,
        routes: dict[str, HttpToolRoute],
    ) -> None:
        self.supervisor = supervisor
        self.routes = routes

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        supervisor: SecuritySupervisor,
    ) -> "ToolProxy":
        return cls(supervisor, load_tool_routes(path))

    def call(self, request: ToolProxyRequest) -> ToolProxyResult:
        event = request.to_event()
        decision = self.supervisor.observe(event)
        if not decision.enforced:
            upstream_response = self._forward(request)
            return ToolProxyResult(
                status="shadow_executed" if decision.violations else "executed",
                decision=decision,
                upstream_response=upstream_response,
            )
        if decision.requires_human_approval:
            approval = self.supervisor.create_approval_request(event, decision)
            return ToolProxyResult(
                status="approval_required",
                decision=decision,
                approval_request=approval,
            )
        if not decision.allowed:
            return ToolProxyResult(status="blocked", decision=decision)

        upstream_response = self._forward(request)
        return ToolProxyResult(
            status="executed",
            decision=decision,
            upstream_response=upstream_response,
        )

    def execute_approved(self, request_id: str) -> ToolProxyResult:
        approval = self.supervisor.approval_store.get(request_id)
        if approval is None:
            raise KeyError(f"Unknown approval request: {request_id}")
        if approval.status != ApprovalStatus.APPROVED:
            raise PermissionError(
                f"Approval request {request_id} is {approval.status.value}"
            )

        request = ToolProxyRequest(
            agent_id=approval.agent_id,
            task_id=approval.task_id,
            tool_name=approval.tool_name,
            tool_args=approval.tool_args,
            metadata=approval.metadata,
        )
        upstream_response = self._forward(request)
        executed = self.supervisor.mark_request_executed(request_id)
        return ToolProxyResult(
            status="executed",
            decision=Decision(agent_id=approval.agent_id, allowed=True),
            approval_request=executed,
            upstream_response=upstream_response,
        )

    def _forward(self, request: ToolProxyRequest) -> UpstreamToolResponse:
        route = self.routes.get(request.tool_name)
        if route is None:
            raise KeyError(f"No tool route configured for {request.tool_name}")

        body = _route_body(route, request)
        headers = {"Content-Type": "application/json", **route.headers}
        http_request = urllib.request.Request(
            route.url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method=route.method,
        )
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=route.timeout_seconds,
            ) as response:
                raw_body = response.read()
                return UpstreamToolResponse(
                    status_code=response.status,
                    body=_decode_body(raw_body, response.headers.get("Content-Type", "")),
                    headers={str(key): str(value) for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as error:
            raw_body = error.read()
            return UpstreamToolResponse(
                status_code=error.code,
                body=_decode_body(raw_body, error.headers.get("Content-Type", "")),
                headers={str(key): str(value) for key, value in error.headers.items()},
            )
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach upstream tool {request.tool_name}: {error}") from error


def load_tool_routes(path: str | Path) -> dict[str, HttpToolRoute]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    raw_routes = data.get("tools", data)
    if isinstance(raw_routes, dict):
        routes = [
            {"tool_name": tool_name, **config}
            for tool_name, config in raw_routes.items()
        ]
    elif isinstance(raw_routes, list):
        routes = raw_routes
    else:
        raise ValueError("Tool route config must be an object or list.")
    parsed = [HttpToolRoute.from_dict(route) for route in routes]
    return {route.tool_name: route for route in parsed}


def _route_body(route: HttpToolRoute, request: ToolProxyRequest) -> dict[str, Any]:
    if route.body_mode == "tool_args":
        return request.tool_args
    if route.body_mode != "envelope":
        raise ValueError(f"Unsupported tool route body_mode: {route.body_mode}")
    return {
        "agent_id": request.agent_id,
        "task_id": request.task_id,
        "tool_name": request.tool_name,
        "tool_args": request.tool_args,
        "metadata": request.metadata,
    }


def _decode_body(raw_body: bytes, content_type: str) -> Any:
    text = raw_body.decode("utf-8") if raw_body else ""
    if "application/json" in content_type and text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text
