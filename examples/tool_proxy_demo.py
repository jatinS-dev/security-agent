from __future__ import annotations

import argparse
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sentient import (
    EnforcementMode,
    HttpToolRoute,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolProxy,
    ToolProxyRequest,
)
from sentient.api import make_handler


def run_demo() -> list[str]:
    upstream = _DemoToolServer()
    policy = Policy.from_dict(
        {
            "name": "Tool Proxy Demo Policy",
            "version": "1",
            "allowed_tools": [
                "read_ticket",
                "issue_refund",
                "export_customer_database",
            ],
            "blocked_tool_names": ["export_customer_database"],
            "max_autonomous_amounts": {"issue_refund": 100},
        }
    )
    supervisor = SecuritySupervisor(
        policy_engine=PolicyEngine(policy),
        controller=InMemoryAgentController(),
    )
    proxy = ToolProxy(
        supervisor,
        {
            "read_ticket": HttpToolRoute("read_ticket", upstream.url),
            "issue_refund": HttpToolRoute("issue_refund", upstream.url),
            "export_customer_database": HttpToolRoute(
                "export_customer_database",
                upstream.url,
            ),
        },
    )
    sentient = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(supervisor, "logs/tool-proxy-demo-audit.jsonl", tool_proxy=proxy),
    )
    thread = threading.Thread(target=sentient.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{sentient.server_port}"
        allowed = _post_json(
            f"{base_url}/v1/tool-call",
            {
                "agent_id": "support-agent-7",
                "task_id": "ticket-1842",
                "tool_name": "read_ticket",
                "tool_args": {"ticket_id": "ticket-1842"},
            },
        )
        approval = _post_json(
            f"{base_url}/v1/tool-call",
            {
                "agent_id": "support-agent-7",
                "task_id": "ticket-1842",
                "tool_name": "issue_refund",
                "tool_args": {"customer_id": "cust_991", "amount": 950},
            },
        )
        request_id = approval["tool_call"]["approval_request"]["request_id"]
        supervisor.approve_request(request_id, reviewer="finance@example.com")
        executed = _post_json(
            f"{base_url}/v1/tool-call/execute-approved",
            {"request_id": request_id},
        )
        blocked = _post_json(
            f"{base_url}/v1/tool-call",
            {
                "agent_id": "support-agent-7",
                "task_id": "ticket-1842",
                "tool_name": "export_customer_database",
                "tool_args": {},
            },
        )
        shadow_supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=InMemoryAgentController(),
            enforcement_mode=EnforcementMode.SHADOW,
        )
        shadow_proxy = ToolProxy(
            shadow_supervisor,
            {
                "export_customer_database": HttpToolRoute(
                    "export_customer_database",
                    upstream.url,
                )
            },
        )
        shadow_result = shadow_proxy.call(
            ToolProxyRequest(
                agent_id="support-agent-7",
                task_id="ticket-1842",
                tool_name="export_customer_database",
                tool_args={},
            )
        )
    finally:
        sentient.shutdown()
        sentient.server_close()
        thread.join(timeout=5)
        upstream.close()

    return [
        "Sentient Runtime Tool Proxy Demo",
        "================================",
        f"1. read_ticket: {allowed['tool_call']['status']}",
        f"   upstream: {allowed['tool_call']['upstream_response']['body']['ok']}",
        f"2. issue_refund: {approval['tool_call']['status']}",
        f"   approval_request: {request_id}",
        f"3. issue_refund after approval: {executed['tool_call']['status']}",
        f"   upstream amount: {executed['tool_call']['upstream_response']['body']['received']['tool_args']['amount']}",
        f"4. export_customer_database: {blocked['tool_call']['status']}",
        f"   mitigation: {blocked['tool_call']['decision']['summary']}",
        f"5. export_customer_database in shadow: {shadow_result.status}",
        f"   would-have decision: {shadow_result.decision.decision_type.value}",
        "",
        f"Upstream calls forwarded: {len(upstream.requests)}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    lines = run_demo()
    if args.json:
        print(json.dumps({"lines": lines}, indent=2))
    else:
        for line in lines:
            print(line)
    return 0


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class _DemoToolServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                owner.requests.append(payload)
                response = json.dumps({"ok": True, "received": payload}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

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
    raise SystemExit(main())
