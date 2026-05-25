from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from sentient import (
    HumanApprovalRequired,
    InMemoryAgentController,
    OpenAIAgentsAdapter,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolCallBlocked,
    ToolCallContext,
)


class OpenAIAgentsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        policy = Policy.from_dict(
            {
                "name": "openai adapter policy",
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

    def test_wrap_function_preserves_signature_and_executes(self) -> None:
        adapter = OpenAIAgentsAdapter(self.supervisor)
        calls = []

        def read_ticket(ticket_id: str) -> str:
            """Read a support ticket."""
            calls.append(ticket_id)
            return "ticket"

        wrapped = adapter.wrap_function(
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
        self.assertEqual(inspect.signature(wrapped), inspect.signature(read_ticket))
        self.assertEqual(wrapped.__doc__, read_ticket.__doc__)

    def test_wrap_function_blocks_before_execution(self) -> None:
        adapter = OpenAIAgentsAdapter(self.supervisor)
        calls = []

        def deploy_production(service: str) -> str:
            calls.append(service)
            return "deployed"

        wrapped = adapter.wrap_function(
            "deploy_production",
            deploy_production,
            default_context=ToolCallContext(agent_id="support-agent-1"),
        )

        with self.assertRaises(ToolCallBlocked):
            wrapped("payments")

        self.assertEqual(calls, [])

    def test_wrap_function_tool_uses_injected_function_tool_factory(self) -> None:
        calls = []
        factory_calls = []

        def fake_function_tool(**factory_kwargs):
            factory_calls.append(factory_kwargs)

            def decorate(func):
                setattr(func, "openai_function_tool", True)
                setattr(func, "openai_tool_kwargs", factory_kwargs)
                return func

            return decorate

        def read_ticket(ticket_id: str) -> str:
            calls.append(ticket_id)
            return "ticket"

        adapter = OpenAIAgentsAdapter(
            self.supervisor,
            function_tool_factory=fake_function_tool,
        )

        wrapped = adapter.wrap_function_tool(
            "read_ticket",
            read_ticket,
            default_context=ToolCallContext(agent_id="support-agent-1"),
            strict_mode=False,
        )

        self.assertTrue(wrapped.openai_function_tool)
        self.assertEqual(
            factory_calls,
            [{"name_override": "read_ticket", "strict_mode": False}],
        )
        self.assertEqual(wrapped("ticket-1"), "ticket")
        self.assertEqual(calls, ["ticket-1"])

    def test_wrap_function_tool_creates_approval_request(self) -> None:
        def fake_function_tool(**factory_kwargs):
            def decorate(func):
                return func

            return decorate

        def issue_refund(customer_id: str, amount: int) -> str:
            return "refund issued"

        adapter = OpenAIAgentsAdapter(
            self.supervisor,
            function_tool_factory=fake_function_tool,
        )
        wrapped = adapter.wrap_function_tool(
            "issue_refund",
            issue_refund,
            default_context=ToolCallContext(
                agent_id="support-agent-1",
                agent_role="support_manager",
            ),
        )

        with self.assertRaises(HumanApprovalRequired) as raised:
            wrapped("cust_123", 950)

        self.assertIsNotNone(raised.exception.approval_request)
        self.assertEqual(len(self.supervisor.list_pending_approvals()), 1)

    def test_missing_agents_sdk_raises_clear_runtime_error(self) -> None:
        adapter = OpenAIAgentsAdapter(self.supervisor)

        def read_ticket(ticket_id: str) -> str:
            return "ticket"

        with patch(
            "sentient.adapters.openai_agents._load_function_tool",
            side_effect=RuntimeError("OpenAI Agents SDK is not installed."),
        ):
            with self.assertRaisesRegex(RuntimeError, "OpenAI Agents SDK is not installed"):
                adapter.wrap_function_tool(
                    "read_ticket",
                    read_ticket,
                    default_context=ToolCallContext(agent_id="support-agent-1"),
                )


if __name__ == "__main__":
    unittest.main()
