from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from sentient import (
    AgentEvent,
    AgentProfile,
    ApprovalRequest,
    ApprovalStatus,
    AutoGenAdapter,
    CrewAIAdapter,
    DecisionType,
    EventType,
    FileAgentRegistry,
    InMemoryAgentController,
    InMemoryAgentRegistry,
    LangGraphAdapter,
    PhraseEvidenceVerifier,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    SQLiteApprovalStore,
    SQLiteAuditStore,
    ToolCallContext,
)
from sentient.api import make_handler
from sentient.cli import main


class NoDashboardMvpTests(unittest.TestCase):
    def test_policy_validate_and_scenario_library_pass(self) -> None:
        result, stdout, stderr = _run_cli(
            "policy",
            "test",
            "--policy",
            "policies/default_policy.json",
            "--all",
            "scenarios",
            "--no-llm-brain",
        )

        self.assertEqual(result, 0, stderr + stdout)
        self.assertIn("PASS High refund requires approval", stdout)
        self.assertIn("PASS Supported claim is allowed", stdout)

    def test_agent_registry_blocks_unknown_agent_and_wrong_task(self) -> None:
        registry = InMemoryAgentRegistry()
        registry.register(
            AgentProfile(
                agent_id="support-agent-7",
                role="support_manager",
                allowed_tasks=frozenset({"ticket-1842"}),
            )
        )
        engine = PolicyEngine(
            Policy.from_dict({"name": "test", "version": "test"}),
            agent_registry=registry,
        )

        unknown = engine.evaluate(
            AgentEvent(
                agent_id="unknown",
                event_type=EventType.MESSAGE,
                content="hello",
            )
        )
        wrong_task = engine.evaluate(
            AgentEvent(
                agent_id="support-agent-7",
                task_id="ticket-999",
                event_type=EventType.MESSAGE,
                content="hello",
            )
        )

        self.assertEqual(unknown[0].rule_id, "unknown-agent")
        self.assertEqual(wrong_task[0].rule_id, "task-not-allowed-for-agent")

    def test_file_agent_registry_loads_profiles(self) -> None:
        registry = FileAgentRegistry.from_file("agents/example_registry.json")

        profile = registry.get("support-agent-7")

        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, "support_manager")
        self.assertIn("ticket-1842", profile.allowed_tasks)

    def test_http_decide_endpoint(self) -> None:
        policy = Policy.from_dict(
            {
                "name": "api policy",
                "version": "test",
                "allowed_tools": ["issue_refund"],
                "max_autonomous_amounts": {"issue_refund": 250},
            }
        )
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=InMemoryAgentController(),
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(supervisor, "logs/test-audit.jsonl"),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1/decide"
            request = urllib.request.Request(
                url,
                data=json.dumps(
                    {
                        "event": {
                            "agent_id": "support-agent-7",
                            "event_type": "tool_call",
                            "content": "Issue refund",
                            "metadata": {
                                "tool_name": "issue_refund",
                                "tool_args": {"amount": 950},
                            },
                        }
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

        self.assertEqual(
            payload["decision"]["decision_type"],
            DecisionType.REQUIRE_HUMAN_APPROVAL.value,
        )

    def test_sqlite_stores_persist_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "security.db"
            approvals = SQLiteApprovalStore(db_path)
            audit = SQLiteAuditStore(db_path)
            request = ApprovalRequest(
                request_id="req-1",
                agent_id="agent-1",
                tool_name="issue_refund",
                decision_summary="approval needed",
                status=ApprovalStatus.PENDING,
                created_at="2026-05-21T00:00:00+00:00",
            )

            approvals.create(request)
            approvals.update("req-1", status=ApprovalStatus.APPROVED, reviewer="sec")
            reloaded_approvals = SQLiteApprovalStore(db_path)
            audit.append_decision(
                AgentEvent(
                    agent_id="agent-1",
                    event_type=EventType.MESSAGE,
                    content="ok",
                ),
                _decision_allow(),
                "2026-05-21T00:00:00+00:00",
            )

            self.assertEqual(
                reloaded_approvals.get("req-1").status,
                ApprovalStatus.APPROVED,
            )
            self.assertEqual(len(audit.list_records()), 1)

    def test_phrase_verifier(self) -> None:
        verifier = PhraseEvidenceVerifier()

        supported = verifier.verify(
            "Refund policy allows manager approval",
            "The refund policy allows manager approval for escalations.",
        )
        unsupported = verifier.verify(
            "Refund policy allows manager approval",
            "The deployment checklist was updated.",
        )

        self.assertTrue(supported.supported)
        self.assertFalse(unsupported.supported)

    def test_dependency_light_adapter_aliases_wrap_tools(self) -> None:
        for adapter_type in (LangGraphAdapter, CrewAIAdapter, AutoGenAdapter):
            supervisor = SecuritySupervisor(
                policy_engine=PolicyEngine(
                    Policy.from_dict(
                        {
                            "name": "adapter",
                            "version": "test",
                            "allowed_tools": ["read_ticket"],
                        }
                    )
                ),
                controller=InMemoryAgentController(),
            )
            adapter = adapter_type(supervisor)
            wrapped = adapter.wrap_tool(
                "read_ticket",
                lambda ticket_id: f"ticket {ticket_id}",
                default_context=ToolCallContext(agent_id="agent-1"),
            )

            self.assertEqual(wrapped("123"), "ticket 123")


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = main(argv)
    return result, stdout.getvalue(), stderr.getvalue()


def _decision_allow():
    from sentient import Decision

    return Decision(agent_id="agent-1", allowed=True)


if __name__ == "__main__":
    unittest.main()
