from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from sentient import (
    AgentEvent,
    ContextAwarePolicyEngine,
    ContextGraph,
    EventType,
    InMemoryAgentController,
    Policy,
    SecuritySupervisor,
)


def run_demo(output_dir: str | Path) -> list[str]:
    output_path = Path(output_dir)
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    tenant_id = "acme"
    graph = ContextGraph(output_path / "context", tenant_id)
    policy_pack = Path(__file__).parent / "company_policy_pack"
    ingest = graph.ingest_path(policy_pack)

    lines = [
        "Sentient Context Graph Demo",
        "============================",
        f"1. ingested documents: {ingest.documents_added}",
        f"   chunks: {ingest.chunks_added}",
        f"   draft rules: {ingest.rules_proposed}",
        "2. draft rules:",
    ]

    draft_rules = graph.rules(status="draft")
    for rule in draft_rules:
        lines.append(f"   - {rule.id}: {rule.description}")

    for rule in draft_rules:
        if rule.rule_type in {
            "amount_requires_approval",
            "blocked_tool",
            "role_requirement",
            "tool_requires_approval",
            "blocked_content",
        }:
            graph.activate_rule(rule.id, reviewed_by="demo-reviewer")

    lines.append("3. activated reviewed rules")

    policy = Policy(
        name="Context Demo Base Policy",
        version="1.0.0",
        allowed_tools=frozenset(
            {
                "issue_refund",
                "send_email",
                "export_customer_database",
                "deploy_production",
            }
        ),
        completion_requires_artifacts=False,
        factual_claims_require_sources=True,
    )
    supervisor = SecuritySupervisor(
        policy_engine=ContextAwarePolicyEngine(policy, context_graph=graph),
        controller=InMemoryAgentController(),
    )

    events = [
        (
            "large refund",
            AgentEvent(
                agent_id="support-agent-context-demo",
                task_id="ticket-1842",
                event_type=EventType.TOOL_CALL,
                content="Issue refund",
                metadata={
                    "tool_name": "issue_refund",
                    "agent_role": "support_manager",
                    "tool_args": {"amount": 950},
                },
            ),
        ),
        (
            "wrong role refund",
            AgentEvent(
                agent_id="support-agent-context-demo",
                task_id="ticket-1842",
                event_type=EventType.TOOL_CALL,
                content="Issue refund",
                metadata={
                    "tool_name": "issue_refund",
                    "agent_role": "support_agent",
                    "tool_args": {"amount": 50},
                },
            ),
        ),
        (
            "customer database export",
            AgentEvent(
                agent_id="support-agent-context-demo",
                task_id="ticket-1842",
                event_type=EventType.TOOL_CALL,
                content="Export all customer records",
                metadata={"tool_name": "export_customer_database"},
            ),
        ),
        (
            "supported policy claim",
            AgentEvent(
                agent_id="support-agent-context-demo",
                task_id="ticket-1842",
                event_type=EventType.RESULT,
                content="Duplicate subscription charges can be refunded after verification.",
                metadata={
                    "claims": [
                        "Duplicate subscription charges can be refunded after invoice and payment processor verification."
                    ]
                },
            ),
        ),
        (
            "unsupported customer-history claim",
            AgentEvent(
                agent_id="support-agent-context-demo",
                task_id="ticket-1842",
                event_type=EventType.RESULT,
                content="The customer has never disputed a payment before.",
                metadata={"claims": ["The customer has never disputed a payment before."]},
            ),
        ),
    ]

    lines.append("4. monitored agent events:")
    for label, event in events:
        decision = supervisor.observe(event)
        lines.append(f"   - {label}: {decision.decision_type.value}")
        lines.append(f"     {decision.summary}")

    lines.append("")
    lines.append("Artifacts")
    lines.append(f"- context graph: {graph.graph_path}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="demo_output/context_graph")
    args = parser.parse_args()
    for line in run_demo(args.output_dir):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
