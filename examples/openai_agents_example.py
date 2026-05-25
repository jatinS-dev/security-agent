from sentient import OpenAIAgentsAdapter, ToolCallContext


def issue_refund(customer_id: str, amount: int) -> str:
    """Issue a customer refund."""
    return f"Refunded {amount} to {customer_id}"


def build_tool(supervisor):
    adapter = OpenAIAgentsAdapter(supervisor)
    return adapter.wrap_function_tool(
        "issue_refund",
        issue_refund,
        default_context=ToolCallContext(
            agent_id="support-agent-7",
            task_id="ticket-1842",
            agent_role="support_manager",
        ),
    )

