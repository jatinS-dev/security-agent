from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sentient import (
    EnforcementMode,
    EventType,
    HttpToolRoute,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolProxy,
    ToolProxyRequest,
)
from sentient.api import make_handler


class ToolProxyTests(unittest.TestCase):
    def test_allowed_tool_call_is_forwarded_to_upstream(self) -> None:
        upstream = _ToolServer()
        proxy = ToolProxy(
            _supervisor({"allowed_tools": ["read_ticket"]}),
            {
                "read_ticket": HttpToolRoute(
                    tool_name="read_ticket",
                    url=upstream.url,
                )
            },
        )
        try:
            result = proxy.call(
                ToolProxyRequest(
                    agent_id="agent-1",
                    tool_name="read_ticket",
                    tool_args={"ticket_id": "ticket-1842"},
                )
            )
        finally:
            upstream.close()

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.upstream_response.status_code, 200)
        self.assertEqual(result.upstream_response.body["ok"], True)
        self.assertEqual(upstream.requests[0]["tool_args"]["ticket_id"], "ticket-1842")

    def test_blocked_tool_call_is_not_forwarded(self) -> None:
        upstream = _ToolServer()
        proxy = ToolProxy(
            _supervisor(
                {
                    "allowed_tools": ["export_customer_database"],
                    "blocked_tool_names": ["export_customer_database"],
                }
            ),
            {
                "export_customer_database": HttpToolRoute(
                    tool_name="export_customer_database",
                    url=upstream.url,
                )
            },
        )
        try:
            result = proxy.call(
                ToolProxyRequest(
                    agent_id="agent-1",
                    tool_name="export_customer_database",
                    tool_args={},
                )
            )
        finally:
            upstream.close()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.decision.decision_type.value, "block")
        self.assertEqual(result.decision.enforcement_mode, EnforcementMode.ENFORCE)
        self.assertTrue(result.decision.enforced)
        self.assertEqual(upstream.requests, [])

    def test_shadow_blocked_tool_call_is_forwarded_and_not_stopped(self) -> None:
        upstream = _ToolServer()
        supervisor = _supervisor(
            {
                "allowed_tools": ["export_customer_database"],
                "blocked_tool_names": ["export_customer_database"],
            },
            enforcement_mode=EnforcementMode.SHADOW,
        )
        proxy = ToolProxy(
            supervisor,
            {
                "export_customer_database": HttpToolRoute(
                    tool_name="export_customer_database",
                    url=upstream.url,
                )
            },
        )
        try:
            result = proxy.call(
                ToolProxyRequest(
                    agent_id="agent-1",
                    tool_name="export_customer_database",
                    tool_args={},
                )
            )
        finally:
            upstream.close()

        self.assertEqual(result.status, "shadow_executed")
        self.assertEqual(result.decision.decision_type.value, "block")
        self.assertEqual(result.decision.enforcement_mode, EnforcementMode.SHADOW)
        self.assertFalse(result.decision.enforced)
        self.assertEqual(len(upstream.requests), 1)
        self.assertFalse(supervisor.controller.is_stopped("agent-1"))

    def test_approval_required_call_executes_after_approval(self) -> None:
        upstream = _ToolServer()
        supervisor = _supervisor(
            {
                "allowed_tools": ["issue_refund"],
                "max_autonomous_amounts": {"issue_refund": 100},
            }
        )
        proxy = ToolProxy(
            supervisor,
            {
                "issue_refund": HttpToolRoute(
                    tool_name="issue_refund",
                    url=upstream.url,
                )
            },
        )
        try:
            pending = proxy.call(
                ToolProxyRequest(
                    agent_id="agent-1",
                    task_id="ticket-1842",
                    tool_name="issue_refund",
                    tool_args={"customer_id": "cust_991", "amount": 950},
                )
            )
            self.assertEqual(pending.status, "approval_required")
            self.assertEqual(upstream.requests, [])

            supervisor.approve_request(
                pending.approval_request.request_id,
                reviewer="finance@example.com",
            )
            executed = proxy.execute_approved(pending.approval_request.request_id)
        finally:
            upstream.close()

        self.assertEqual(executed.status, "executed")
        self.assertEqual(executed.approval_request.status.value, "executed")
        self.assertEqual(upstream.requests[0]["tool_args"]["amount"], 950)

    def test_shadow_approval_required_call_forwards_without_approval(self) -> None:
        upstream = _ToolServer()
        supervisor = _supervisor(
            {
                "allowed_tools": ["issue_refund"],
                "max_autonomous_amounts": {"issue_refund": 100},
            },
            enforcement_mode=EnforcementMode.SHADOW,
        )
        proxy = ToolProxy(
            supervisor,
            {
                "issue_refund": HttpToolRoute(
                    tool_name="issue_refund",
                    url=upstream.url,
                )
            },
        )
        try:
            result = proxy.call(
                ToolProxyRequest(
                    agent_id="agent-1",
                    task_id="ticket-1842",
                    tool_name="issue_refund",
                    tool_args={"customer_id": "cust_991", "amount": 950},
                )
            )
        finally:
            upstream.close()

        self.assertEqual(result.status, "shadow_executed")
        self.assertEqual(result.decision.decision_type.value, "require_human_approval")
        self.assertIsNone(result.approval_request)
        self.assertEqual(supervisor.approval_store.list(), [])
        self.assertEqual(upstream.requests[0]["tool_args"]["amount"], 950)

    def test_api_tool_call_endpoint_forwards_allowed_call(self) -> None:
        upstream = _ToolServer()
        supervisor = _supervisor({"allowed_tools": ["read_ticket"]})
        proxy = ToolProxy(
            supervisor,
            {
                "read_ticket": HttpToolRoute(
                    tool_name="read_ticket",
                    url=upstream.url,
                )
            },
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(supervisor, "logs/test-audit.jsonl", tool_proxy=proxy),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/tool-call",
                data=json.dumps(
                    {
                        "agent_id": "agent-1",
                        "tool_name": "read_ticket",
                        "tool_args": {"ticket_id": "ticket-1842"},
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            upstream.close()

        self.assertEqual(payload["tool_call"]["status"], "executed")
        self.assertEqual(payload["tool_call"]["upstream_response"]["body"]["ok"], True)


def _supervisor(
    policy_data: dict,
    *,
    enforcement_mode: EnforcementMode = EnforcementMode.ENFORCE,
) -> SecuritySupervisor:
    policy = Policy.from_dict({"name": "tool proxy", "version": "1", **policy_data})
    return SecuritySupervisor(
        policy_engine=PolicyEngine(policy),
        controller=InMemoryAgentController(),
        enforcement_mode=enforcement_mode,
    )


class _ToolServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.requests.append(json.loads(body.decode("utf-8")))
                payload = json.dumps({"ok": True, "received": owner.requests[-1]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/tool"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
