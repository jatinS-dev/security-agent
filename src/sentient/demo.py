from __future__ import annotations

from pathlib import Path

from .controller import InMemoryAgentController
from .models import AgentEvent, EventType
from .policy import Policy, PolicyEngine
from .sdk import HumanApprovalRequired, ToolCallBlocked
from .stores import FileApprovalStore, FileAuditStore
from .supervisor import SecuritySupervisor


def build_supervisor() -> SecuritySupervisor:
    project_root = Path(__file__).resolve().parents[2]
    policy = Policy.from_file(project_root / "policies" / "default_policy.json")
    controller = InMemoryAgentController()
    engine = PolicyEngine(policy)
    return SecuritySupervisor(
        policy_engine=engine,
        controller=controller,
        audit_store=FileAuditStore(project_root / "logs" / "audit.jsonl"),
        approval_store=FileApprovalStore(project_root / "logs" / "approvals.jsonl"),
    )


def main() -> None:
    supervisor = build_supervisor()
    controller = supervisor.controller

    safe_agent = "docs-agent"
    unsafe_agent = "research-agent"
    support_agent = "support-agent"

    if isinstance(controller, InMemoryAgentController):
        controller.register(safe_agent)
        controller.register(unsafe_agent)
        controller.register(support_agent)

    supervisor.assign_task(safe_agent, "write-docs")
    supervisor.assign_task(unsafe_agent, "research-dependency")

    events = [
        AgentEvent(
            agent_id=safe_agent,
            task_id="write-docs",
            event_type=EventType.TOOL_CALL,
            content="Reading approved docs.",
            metadata={"tool_name": "search_docs", "tool_args": {"query": "policy"}},
        ),
        AgentEvent(
            agent_id=unsafe_agent,
            task_id="research-dependency",
            event_type=EventType.RESULT,
            content="Completed. The dependency has no vulnerabilities.",
            metadata={
                "claims": ["The dependency has no vulnerabilities."],
                "sources": [],
                "artifacts": [],
            },
        ),
        AgentEvent(
            agent_id=safe_agent,
            task_id="write-docs",
            event_type=EventType.RESULT,
            content="Completed documentation update.",
            metadata={
                "claims": ["The policy requires sourced factual claims."],
                "sources": ["policies/default_policy.json"],
                "artifacts": ["README.md"],
            },
        ),
    ]

    for event in events:
        decision = supervisor.observe(event)
        print(f"{event.agent_id}: {decision.summary}")

    print("\nGuarded tool call:")

    def issue_refund(customer_id: str, amount: int) -> str:
        return f"Refunded {amount} to {customer_id}"

    guarded_refund = supervisor.guard_tool(
        "issue_refund",
        issue_refund,
        default_metadata={"agent_role": "support_manager"},
    )
    try:
        guarded_refund(
            "cust_991",
            950,
            agent_id=support_agent,
            task_id="resolve-ticket-1842",
        )
    except HumanApprovalRequired as approval:
        print(f"- issue_refund: requires approval: {approval.decision.summary}")
        if approval.approval_request is not None:
            print(f"- approval request: {approval.approval_request.request_id}")
            supervisor.approve_request(
                approval.approval_request.request_id,
                reviewer="security@example.com",
                reason="Demo approval for escalated refund.",
            )
            result = guarded_refund.execute_approved(
                approval.approval_request.request_id
            )
            print(f"- issue_refund after approval: {result}")
    except ToolCallBlocked as blocked:
        print(f"- issue_refund: blocked: {blocked.decision.summary}")

    if isinstance(controller, InMemoryAgentController):
        print("\nAgent states:")
        for agent_id, state in controller.states.items():
            reason = controller.stop_reasons.get(agent_id, "")
            print(f"- {agent_id}: {state.value} {reason}")


if __name__ == "__main__":
    main()
