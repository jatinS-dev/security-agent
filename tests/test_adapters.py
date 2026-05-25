from __future__ import annotations

import unittest

from sentient import (
    ApprovalStatus,
    HumanApprovalRequired,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    PythonRuntimeAdapter,
    SecuritySupervisor,
    ToolCallBlocked,
    ToolCallContext,
)


class PythonRuntimeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        policy = Policy.from_dict(
            {
                "name": "adapter policy",
                "version": "test",
                "allowed_tools": ["issue_refund", "read_ticket"],
                "blocked_tool_names": ["deploy_production"],
                "max_autonomous_amounts": {"issue_refund": 250},
                "tool_role_requirements": {
                    "issue_refund": ["support_manager"],
                },
            }
        )
        controller = InMemoryAgentController()
        controller.register("support-agent-1")
        self.supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=controller,
        )
        self.adapter = PythonRuntimeAdapter(self.supervisor)

    def test_wrap_tool_executes_with_default_context(self) -> None:
        calls = []

        def read_ticket(ticket_id: str) -> str:
            calls.append(ticket_id)
            return "ticket"

        wrapped = self.adapter.wrap_tool(
            "read_ticket",
            read_ticket,
            default_context=ToolCallContext(
                agent_id="support-agent-1",
                task_id="ticket-1",
                agent_role="support_agent",
            ),
        )

        result = wrapped("ticket-1")

        self.assertEqual(result, "ticket")
        self.assertEqual(calls, ["ticket-1"])

    def test_wrap_tool_requires_context_without_default(self) -> None:
        wrapped = self.adapter.wrap_tool("read_ticket", lambda ticket_id: "ticket")

        with self.assertRaises(ValueError):
            wrapped("ticket-1")

    def test_per_call_context_controls_role_policy(self) -> None:
        calls = []

        def issue_refund(customer_id: str, amount: int) -> str:
            calls.append((customer_id, amount))
            return "refund issued"

        wrapped = self.adapter.wrap_tool("issue_refund", issue_refund)

        with self.assertRaises(ToolCallBlocked):
            wrapped(
                "cust_123",
                100,
                security_context=ToolCallContext(
                    agent_id="support-agent-1",
                    agent_role="support_agent",
                ),
            )

        result = wrapped(
            "cust_123",
            100,
            security_context=ToolCallContext(
                agent_id="support-agent-1",
                agent_role="support_manager",
            ),
        )

        self.assertEqual(result, "refund issued")
        self.assertEqual(calls, [("cust_123", 100)])

    def test_adapter_tool_can_execute_after_human_approval(self) -> None:
        calls = []

        def issue_refund(customer_id: str, amount: int) -> str:
            calls.append((customer_id, amount))
            return "refund issued"

        wrapped = self.adapter.wrap_tool("issue_refund", issue_refund)

        with self.assertRaises(HumanApprovalRequired) as raised:
            wrapped(
                "cust_123",
                950,
                security_context=ToolCallContext(
                    agent_id="support-agent-1",
                    agent_role="support_manager",
                ),
            )

        request_id = raised.exception.approval_request.request_id
        self.supervisor.approve_request(
            request_id,
            reviewer="security@example.com",
        )
        result = wrapped.execute_approved(request_id)

        self.assertEqual(result, "refund issued")
        self.assertEqual(calls, [("cust_123", 950)])
        approval = self.supervisor.approval_store.get(request_id)
        self.assertEqual(approval.status, ApprovalStatus.EXECUTED)


if __name__ == "__main__":
    unittest.main()

