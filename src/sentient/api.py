from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .agent_registry import FileAgentRegistry
from .context import ContextAwarePolicyEngine, ContextGraph
from .controller import InMemoryAgentController
from .llm import build_llm_brain
from .models import AgentEvent, ApprovalStatus, EnforcementMode, EventType
from .openapi import openapi_spec
from .policy import Policy, PolicyEngine
from .security import ApiSecurityConfig, InMemoryRateLimiter, verify_api_key, verify_signature
from .stores import FileApprovalStore, FileAuditStore
from .supervisor import SecuritySupervisor
from .tenancy import FileTenantRegistry, TenantSupervisorRouter
from .tool_proxy import HttpToolRoute, ToolProxy, ToolProxyRequest, load_tool_routes
from .verifiers import KeywordEvidenceVerifier, VerifierRegistry


class BodyTooLarge(ValueError):
    pass


def build_supervisor(
    policy_path: str | Path,
    *,
    audit_path: str | Path = "logs/audit.jsonl",
    approvals_path: str | Path = "logs/approvals.jsonl",
    registry_path: str | Path | None = None,
    context_store: str | Path | None = None,
    tenant_id: str | None = None,
    llm_provider: str | None = None,
    llm_model: str = "llama3.2",
    llm_base_url: str = "http://127.0.0.1:11434",
    llm_min_confidence: float = 0.55,
    enforcement_mode: str | EnforcementMode = EnforcementMode.ENFORCE,
) -> SecuritySupervisor:
    registry = FileAgentRegistry.from_file(registry_path) if registry_path else None
    policy = Policy.from_file(policy_path)
    verifier_registry = VerifierRegistry(KeywordEvidenceVerifier())
    llm_brain = build_llm_brain(
        llm_provider,
        model=llm_model,
        base_url=llm_base_url,
        min_confidence=llm_min_confidence,
    )
    if context_store is not None and llm_brain is None:
        raise ValueError("Context-aware monitoring requires an LLM brain.")
    if context_store is not None or llm_brain is not None:
        if context_store is not None and tenant_id is None:
            raise ValueError("tenant_id is required when context_store is provided")
        policy_engine = ContextAwarePolicyEngine(
            policy,
            context_graph=ContextGraph(context_store, tenant_id) if context_store else None,
            llm_brain=llm_brain,
            verifier_registry=verifier_registry,
            agent_registry=registry,
        )
    else:
        policy_engine = PolicyEngine(
            policy,
            verifier_registry=verifier_registry,
            agent_registry=registry,
        )
    return SecuritySupervisor(
        policy_engine=policy_engine,
        controller=InMemoryAgentController(),
        enforcement_mode=EnforcementMode(enforcement_mode),
        audit_store=FileAuditStore(audit_path),
        approval_store=FileApprovalStore(approvals_path),
    )


def make_handler(
    supervisor: SecuritySupervisor,
    audit_path: str | Path,
    security: ApiSecurityConfig | None = None,
    tenant_router: TenantSupervisorRouter | None = None,
    tool_proxy: ToolProxy | None = None,
    tool_routes: dict[str, HttpToolRoute] | None = None,
):
    audit_store_path = Path(audit_path)
    security_config = security or ApiSecurityConfig()
    metrics = {"requests_total": 0, "decisions_total": 0, "errors_total": 0}
    rate_limiter = (
        InMemoryRateLimiter(security_config.rate_limit_per_minute)
        if security_config.rate_limit_per_minute
        else None
    )

    class SecurityAIHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            metrics["requests_total"] += 1
            try:
                path = urlparse(self.path).path
                tenant_id = self._tenant_id(required=path in {"/v1/approvals", "/v1/audit"})
                if tenant_id is False:
                    return
                if not self._authorized(b"", tenant_id=tenant_id, required_scope=_scope_for("GET", path)):
                    return
                if path in {"/v1/health", "/v1/ready"}:
                    self._send_json({"status": "ok"})
                    return
                if path == "/metrics":
                    self._send_text(_format_metrics(metrics))
                    return
                if path == "/openapi.json":
                    self._send_json(openapi_spec())
                    return
                if path == "/v1/approvals":
                    active_supervisor = self._active_supervisor(tenant_id)
                    approvals = [
                        _approval_to_dict(item)
                        for item in active_supervisor.approval_store.list(ApprovalStatus.PENDING)
                    ]
                    self._send_json({"approvals": approvals})
                    return
                if path == "/v1/audit":
                    self._send_json({"records": _load_jsonl(self._active_audit_path(tenant_id))})
                    return
                self._send_error("not_found", "not found", status=404)
            except Exception as error:
                metrics["errors_total"] += 1
                self._send_error("internal_error", str(error), status=500)

        def do_POST(self) -> None:
            metrics["requests_total"] += 1
            try:
                body = self._read_body()
                path = urlparse(self.path).path
                tenant_id = self._tenant_id(
                    required=(
                        path == "/v1/decide"
                        or path == "/v1/tool-call"
                        or path == "/v1/tool-call/execute-approved"
                        or path.startswith("/v1/approvals/")
                    )
                )
                if tenant_id is False:
                    return
                if not self._authorized(
                    body,
                    tenant_id=tenant_id,
                    required_scope=_scope_for("POST", path),
                ):
                    return
                payload = self._decode_json(body)
                if path == "/v1/decide":
                    event = _event_from_dict(payload["event"])
                    decision = self._active_supervisor(tenant_id).observe(event)
                    metrics["decisions_total"] += 1
                    self._send_json({"decision": _decision_to_dict(decision)})
                    return
                if path == "/v1/tool-call":
                    active_tool_proxy = self._active_tool_proxy(tenant_id)
                    if active_tool_proxy is None:
                        self._send_error(
                            "tool_proxy_not_configured",
                            "tool proxy is not configured",
                            status=400,
                        )
                        return
                    result = active_tool_proxy.call(ToolProxyRequest.from_dict(payload))
                    metrics["decisions_total"] += 1
                    self._send_json({"tool_call": _tool_proxy_result_to_dict(result)})
                    return
                if path == "/v1/tool-call/execute-approved":
                    active_tool_proxy = self._active_tool_proxy(tenant_id)
                    if active_tool_proxy is None:
                        self._send_error(
                            "tool_proxy_not_configured",
                            "tool proxy is not configured",
                            status=400,
                        )
                        return
                    result = active_tool_proxy.execute_approved(str(payload["request_id"]))
                    self._send_json({"tool_call": _tool_proxy_result_to_dict(result)})
                    return
                if path.startswith("/v1/approvals/") and path.endswith("/approve"):
                    request_id = path.split("/")[3]
                    approval = self._active_supervisor(tenant_id).approve_request(
                        request_id,
                        reviewer=str(payload.get("reviewer", "api")),
                        reason=payload.get("reason"),
                    )
                    self._send_json({"approval": _approval_to_dict(approval)})
                    return
                if path.startswith("/v1/approvals/") and path.endswith("/reject"):
                    request_id = path.split("/")[3]
                    approval = self._active_supervisor(tenant_id).reject_request(
                        request_id,
                        reviewer=str(payload.get("reviewer", "api")),
                        reason=payload.get("reason"),
                    )
                    self._send_json({"approval": _approval_to_dict(approval)})
                    return
                self._send_error("not_found", "not found", status=404)
            except KeyError as error:
                metrics["errors_total"] += 1
                self._send_error("not_found", str(error), status=404)
            except BodyTooLarge as error:
                metrics["errors_total"] += 1
                self._send_error("payload_too_large", str(error), status=413)
            except PermissionError as error:
                metrics["errors_total"] += 1
                self._send_error("forbidden", str(error), status=403)
            except ValueError as error:
                metrics["errors_total"] += 1
                self._send_error("bad_request", str(error), status=400)
            except Exception as error:
                metrics["errors_total"] += 1
                self._send_error("internal_error", str(error), status=500)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length > security_config.max_body_bytes:
                raise BodyTooLarge(
                    f"request body exceeds {security_config.max_body_bytes} bytes"
                )
            return self.rfile.read(length) if length else b"{}"

        def _decode_json(self, body: bytes) -> dict[str, Any]:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object.")
            return data

        def _tenant_id(self, *, required: bool) -> str | None | bool:
            tenant_id = self.headers.get("X-Tenant-ID")
            if tenant_router is not None and required and not tenant_id:
                self._send_error("missing_tenant", "X-Tenant-ID header is required", status=400)
                return False
            return tenant_id

        def _active_supervisor(self, tenant_id: str | None) -> SecuritySupervisor:
            if tenant_router is None:
                return supervisor
            if tenant_id is None:
                raise ValueError("X-Tenant-ID header is required")
            return tenant_router.supervisor_for(tenant_id)

        def _active_audit_path(self, tenant_id: str | None) -> Path:
            if tenant_router is None:
                return audit_store_path
            if tenant_id is None:
                raise ValueError("X-Tenant-ID header is required")
            return tenant_router.audit_path_for(tenant_id)

        def _active_tool_proxy(self, tenant_id: str | None) -> ToolProxy | None:
            if tool_routes is not None:
                return ToolProxy(self._active_supervisor(tenant_id), tool_routes)
            return tool_proxy

        def _authorized(
            self,
            body: bytes,
            *,
            tenant_id: str | None,
            required_scope: str | None,
        ) -> bool:
            identity = self.headers.get("X-API-Key") or self.client_address[0]
            if rate_limiter is not None and not rate_limiter.allow(identity):
                self._send_error("rate_limited", "rate limit exceeded", status=429)
                return False
            if security_config.api_key_store is not None:
                if security_config.api_key_store.authenticate(
                    self.headers.get("X-API-Key"),
                    tenant_id=tenant_id,
                    required_scope=required_scope,
                ) is None:
                    self._send_error("unauthorized", "invalid API key", status=401)
                    return False
                return self._valid_signature(body)
            if not verify_api_key(security_config.api_key, self.headers.get("X-API-Key")):
                self._send_error("unauthorized", "invalid API key", status=401)
                return False
            return self._valid_signature(body)

        def _valid_signature(self, body: bytes) -> bool:
            if not verify_signature(
                security_config.hmac_secret,
                body,
                self.headers.get("X-Signature"),
            ):
                self._send_error("unauthorized", "invalid signature", status=401)
                return False
            return True

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", self.headers.get("X-Request-ID", ""))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, payload: str, status: int = 200) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", self.headers.get("X-Request-ID", ""))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, code: str, message: str, status: int) -> None:
            self._send_json({"error": {"code": code, "message": message}}, status=status)

    return SecurityAIHandler


def serve(
    policy_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    audit_path: str | Path = "logs/audit.jsonl",
    approvals_path: str | Path = "logs/approvals.jsonl",
    registry_path: str | Path | None = None,
    security: ApiSecurityConfig | None = None,
    tenant_registry_path: str | Path | None = None,
    context_store: str | Path | None = None,
    tenant_id: str | None = None,
    llm_provider: str | None = None,
    llm_model: str = "llama3.2",
    llm_base_url: str = "http://127.0.0.1:11434",
    llm_min_confidence: float = 0.55,
    tool_routes_path: str | Path | None = None,
    enforcement_mode: str | EnforcementMode = EnforcementMode.ENFORCE,
) -> None:
    supervisor = build_supervisor(
        policy_path,
        audit_path=audit_path,
        approvals_path=approvals_path,
        registry_path=registry_path,
        context_store=context_store,
        tenant_id=tenant_id,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
        llm_min_confidence=llm_min_confidence,
        enforcement_mode=enforcement_mode,
    )
    tenant_router = (
        TenantSupervisorRouter(
            FileTenantRegistry(tenant_registry_path),
            enforcement_mode=EnforcementMode(enforcement_mode),
        )
        if tenant_registry_path
        else None
    )
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(
            supervisor,
            audit_path,
            security=security,
            tenant_router=tenant_router,
            tool_routes=load_tool_routes(tool_routes_path) if tool_routes_path else None,
        ),
    )
    print(f"Sentient API listening on http://{host}:{port}")
    server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentient-api")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--audit-store", default="logs/audit.jsonl")
    parser.add_argument("--approval-store", default="logs/approvals.jsonl")
    parser.add_argument("--agent-registry", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--hmac-secret", default=None)
    parser.add_argument("--rate-limit-per-minute", type=int, default=None)
    parser.add_argument("--max-body-bytes", type=int, default=1_000_000)
    parser.add_argument("--tenant-registry", default=None)
    parser.add_argument("--api-key-store", default=None)
    parser.add_argument("--context-store", default=None)
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--llm-provider", choices=["ollama"], default="ollama")
    parser.add_argument("--no-llm-brain", action="store_true")
    parser.add_argument("--llm-model", default="llama3.2")
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--llm-min-confidence", type=float, default=0.55)
    parser.add_argument("--tool-routes", default=None)
    parser.add_argument(
        "--enforcement-mode",
        choices=[mode.value for mode in EnforcementMode],
        default=EnforcementMode.ENFORCE.value,
    )
    args = parser.parse_args(argv)
    api_key_store = None
    if args.api_key_store:
        from .keys import FileApiKeyStore

        api_key_store = FileApiKeyStore(args.api_key_store)
    serve(
        args.policy,
        host=args.host,
        port=args.port,
        audit_path=args.audit_store,
        approvals_path=args.approval_store,
        registry_path=args.agent_registry,
        tenant_registry_path=args.tenant_registry,
        context_store=args.context_store,
        tenant_id=args.tenant_id,
        llm_provider="none" if args.no_llm_brain else args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_min_confidence=args.llm_min_confidence,
        tool_routes_path=args.tool_routes,
        enforcement_mode=args.enforcement_mode,
        security=ApiSecurityConfig(
            api_key=args.api_key,
            hmac_secret=args.hmac_secret,
            rate_limit_per_minute=args.rate_limit_per_minute,
            max_body_bytes=args.max_body_bytes,
            api_key_store=api_key_store,
        ),
    )
    return 0


def _event_from_dict(data: dict[str, Any]) -> AgentEvent:
    return AgentEvent(
        agent_id=data["agent_id"],
        event_type=EventType(data["event_type"]),
        content=data["content"],
        task_id=data.get("task_id"),
        metadata=data.get("metadata", {}),
    )


def _decision_to_dict(decision) -> dict[str, Any]:
    return {
        "agent_id": decision.agent_id,
        "allowed": decision.allowed,
        "decision_type": decision.decision_type.value,
        "enforcement_mode": decision.enforcement_mode.value,
        "enforced": decision.enforced,
        "summary": decision.summary,
        "violations": [
            {
                "rule_id": violation.rule_id,
                "description": violation.description,
                "severity": violation.severity.value,
                "action": violation.action,
                "evidence": violation.evidence,
            }
            for violation in decision.violations
        ],
    }


def _tool_proxy_result_to_dict(result) -> dict[str, Any]:
    return {
        "status": result.status,
        "decision": _decision_to_dict(result.decision),
        "approval_request": (
            _approval_to_dict(result.approval_request)
            if result.approval_request is not None
            else None
        ),
        "upstream_response": (
            {
                "status_code": result.upstream_response.status_code,
                "body": result.upstream_response.body,
                "headers": result.upstream_response.headers,
            }
            if result.upstream_response is not None
            else None
        ),
    }


def _approval_to_dict(approval) -> dict[str, Any]:
    return {
        "request_id": approval.request_id,
        "agent_id": approval.agent_id,
        "task_id": approval.task_id,
        "tool_name": approval.tool_name,
        "status": approval.status.value,
        "decision_summary": approval.decision_summary,
        "reviewer": approval.reviewer,
        "review_reason": approval.review_reason,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _format_metrics(metrics: dict[str, int]) -> str:
    return "\n".join(
        [
            "# TYPE sentient_requests_total counter",
            f"sentient_requests_total {metrics['requests_total']}",
            "# TYPE sentient_decisions_total counter",
            f"sentient_decisions_total {metrics['decisions_total']}",
            "# TYPE sentient_errors_total counter",
            f"sentient_errors_total {metrics['errors_total']}",
            "",
        ]
    )


def _scope_for(method: str, path: str) -> str | None:
    if path == "/v1/decide":
        return "decide"
    if path == "/v1/tool-call":
        return "tool:call"
    if path == "/v1/tool-call/execute-approved":
        return "tool:execute"
    if path == "/v1/audit":
        return "audit:read"
    if path == "/v1/approvals":
        return "approvals:read"
    if path.startswith("/v1/approvals/") and method == "POST":
        return "approvals:write"
    if path in {"/v1/health", "/v1/ready", "/metrics", "/openapi.json"}:
        return "system:read"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
