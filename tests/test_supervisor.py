from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sentient import (
    AgentEvent,
    ApprovalStatus,
    DecisionType,
    EventType,
    FileApprovalStore,
    FileAuditStore,
    HumanApprovalRequired,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolCallBlocked,
)


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        policy = Policy.from_dict(
            {
                "name": "test policy",
                "version": "test",
                "allowed_tools": ["read_file", "issue_refund", "update_crm_record"],
                "blocked_tool_names": ["deploy_production"],
                "tools_requiring_approval": ["update_crm_record"],
                "max_autonomous_amounts": {"issue_refund": 250},
                "tool_role_requirements": {
                    "issue_refund": ["support_manager"],
                    "update_crm_record": ["sales_ops", "support_manager"],
                },
                "approval_tool_environments": {},
                "blocked_tool_environments": {},
                "blocked_content_patterns": [
                    {
                        "id": "destructive-command",
                        "description": "No destructive commands.",
                        "severity": "critical",
                        "action": "stop_agent",
                        "pattern": "rm\\s+-rf\\s+/",
                    }
                ],
                "completion_requires_artifacts": True,
                "factual_claims_require_sources": True,
            }
        )
        self.controller = InMemoryAgentController()
        self.controller.register("agent-1")
        self.supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=self.controller,
        )

    def test_allows_approved_tool(self) -> None:
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="Read a file.",
                metadata={"tool_name": "read_file"},
            )
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(self.controller.is_stopped("agent-1"))

    def test_stops_blocked_tool(self) -> None:
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="Deploying.",
                metadata={"tool_name": "deploy_production"},
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.should_stop_agent)
        self.assertTrue(self.controller.is_stopped("agent-1"))

    def test_stops_destructive_command(self) -> None:
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="Run command.",
                metadata={"tool_name": "read_file", "command": "rm -rf /"},
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(self.controller.is_stopped("agent-1"))

    def test_stops_tool_when_agent_role_is_not_authorized(self) -> None:
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="Issue refund.",
                metadata={
                    "tool_name": "issue_refund",
                    "agent_role": "support_agent",
                    "tool_args": {"amount": 100},
                },
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(self.controller.is_stopped("agent-1"))
        self.assertEqual(decision.decision_type, DecisionType.BLOCK)

    def test_guarded_tool_executes_when_allowed(self) -> None:
        calls = []

        def read_file(path: str) -> str:
            calls.append(path)
            return "file contents"

        guarded_read_file = self.supervisor.guard_tool("read_file", read_file)
        result = guarded_read_file(
            "/tmp/report.txt",
            agent_id="agent-1",
            task_id=None,
        )

        self.assertEqual(result, "file contents")
        self.assertEqual(calls, ["/tmp/report.txt"])
        self.assertEqual(self.supervisor.audit_log[-1].decision.decision_type, DecisionType.ALLOW)

    def test_guarded_tool_blocks_before_execution(self) -> None:
        calls = []

        def deploy_production(service: str) -> str:
            calls.append(service)
            return "deployed"

        guarded_deploy = self.supervisor.guard_tool(
            "deploy_production",
            deploy_production,
        )

        with self.assertRaises(ToolCallBlocked):
            guarded_deploy("payments", agent_id="agent-1")

        self.assertEqual(calls, [])
        self.assertTrue(self.controller.is_stopped("agent-1"))
        self.assertEqual(self.supervisor.audit_log[-1].decision.decision_type, DecisionType.BLOCK)

    def test_guarded_tool_requires_approval_before_execution(self) -> None:
        calls = []

        def issue_refund(customer_id: str, amount: int) -> str:
            calls.append((customer_id, amount))
            return "refund issued"

        guarded_refund = self.supervisor.guard_tool(
            "issue_refund",
            issue_refund,
            default_metadata={"agent_role": "support_manager"},
        )

        with self.assertRaises(HumanApprovalRequired):
            guarded_refund("cust_123", 950, agent_id="agent-1")

        self.assertEqual(calls, [])
        self.assertFalse(self.controller.is_stopped("agent-1"))
        self.assertEqual(
            self.supervisor.audit_log[-1].decision.decision_type,
            DecisionType.REQUIRE_HUMAN_APPROVAL,
        )
        pending = self.supervisor.list_pending_approvals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].tool_name, "issue_refund")

    def test_guarded_tool_requires_approval_for_production_environment(self) -> None:
        policy = Policy.from_dict(
            {
                "name": "environment policy",
                "version": "test",
                "allowed_tools": ["deploy_service"],
                "approval_tool_environments": {"deploy_service": ["production"]},
            }
        )
        controller = InMemoryAgentController()
        controller.register("agent-2")
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=controller,
        )
        calls = []

        def deploy_service(service: str, environment: str) -> str:
            calls.append((service, environment))
            return "deployed"

        guarded_deploy = supervisor.guard_tool("deploy_service", deploy_service)

        with self.assertRaises(HumanApprovalRequired):
            guarded_deploy("billing", "production", agent_id="agent-2")

        self.assertEqual(calls, [])
        self.assertFalse(controller.is_stopped("agent-2"))
        self.assertEqual(
            supervisor.audit_log[-1].decision.decision_type,
            DecisionType.REQUIRE_HUMAN_APPROVAL,
        )

    def test_approved_guarded_tool_executes_and_marks_request_executed(self) -> None:
        calls = []

        def issue_refund(customer_id: str, amount: int) -> str:
            calls.append((customer_id, amount))
            return "refund issued"

        guarded_refund = self.supervisor.guard_tool(
            "issue_refund",
            issue_refund,
            default_metadata={"agent_role": "support_manager"},
        )

        with self.assertRaises(HumanApprovalRequired) as raised:
            guarded_refund("cust_123", 950, agent_id="agent-1")

        approval = raised.exception.approval_request
        self.assertIsNotNone(approval)
        request_id = approval.request_id
        self.supervisor.approve_request(
            request_id,
            reviewer="security@example.com",
            reason="Customer escalation approved.",
        )

        result = guarded_refund.execute_approved(request_id)

        self.assertEqual(result, "refund issued")
        self.assertEqual(calls, [("cust_123", 950)])
        stored = self.supervisor.approval_store.get(request_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ApprovalStatus.EXECUTED)
        self.assertEqual(stored.reviewer, "security@example.com")

    def test_rejected_guarded_tool_does_not_execute(self) -> None:
        calls = []

        def issue_refund(customer_id: str, amount: int) -> str:
            calls.append((customer_id, amount))
            return "refund issued"

        guarded_refund = self.supervisor.guard_tool(
            "issue_refund",
            issue_refund,
            default_metadata={"agent_role": "support_manager"},
        )

        with self.assertRaises(HumanApprovalRequired) as raised:
            guarded_refund("cust_123", 950, agent_id="agent-1")

        request_id = raised.exception.approval_request.request_id
        self.supervisor.reject_request(
            request_id,
            reviewer="security@example.com",
            reason="Refund policy evidence missing.",
        )

        with self.assertRaises(PermissionError):
            guarded_refund.execute_approved(request_id)

        self.assertEqual(calls, [])

    def test_file_audit_and_approval_stores_persist_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audit_path = temp_path / "audit.jsonl"
            approvals_path = temp_path / "approvals.jsonl"
            policy = Policy.from_dict(
                {
                    "name": "approval policy",
                    "version": "test",
                    "allowed_tools": ["issue_refund"],
                    "max_autonomous_amounts": {"issue_refund": 250},
                }
            )
            controller = InMemoryAgentController()
            controller.register("agent-2")
            supervisor = SecuritySupervisor(
                policy_engine=PolicyEngine(policy),
                controller=controller,
                audit_store=FileAuditStore(audit_path),
                approval_store=FileApprovalStore(approvals_path),
            )

            def issue_refund(customer_id: str, amount: int) -> str:
                return "refund issued"

            guarded_refund = supervisor.guard_tool("issue_refund", issue_refund)

            with self.assertRaises(HumanApprovalRequired) as raised:
                guarded_refund("cust_123", 950, agent_id="agent-2")

            request_id = raised.exception.approval_request.request_id
            supervisor.approve_request(request_id, reviewer="security@example.com")

            audit_lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(audit_lines), 1)
            self.assertEqual(
                json.loads(audit_lines[0])["decision"]["decision_type"],
                DecisionType.REQUIRE_HUMAN_APPROVAL.value,
            )

            reloaded_store = FileApprovalStore(approvals_path)
            reloaded = reloaded_store.get(request_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.status, ApprovalStatus.APPROVED)
            self.assertEqual(reloaded.reviewer, "security@example.com")

    def test_example_policies_load(self) -> None:
        policy_paths = Path("policies").glob("**/*.json")
        policies = [Policy.from_file(path) for path in policy_paths]

        self.assertGreaterEqual(len(policies), 4)
        self.assertTrue(all(policy.name for policy in policies))

    def test_stops_unsupported_claim(self) -> None:
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="Completed. Package is secure.",
                metadata={
                    "claims": ["Package is secure."],
                    "sources": [],
                    "artifacts": ["report.md"],
                },
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(self.controller.is_stopped("agent-1"))

    def test_stops_wrong_task(self) -> None:
        self.supervisor.assign_task("agent-1", "task-a")
        decision = self.supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                task_id="task-b",
                event_type=EventType.MESSAGE,
                content="Working on another task.",
            )
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(self.controller.is_stopped("agent-1"))


if __name__ == "__main__":
    unittest.main()
