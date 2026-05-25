from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .controller import InMemoryAgentController
from .models import AgentEvent, EventType
from .notifications import InMemoryNotifier
from .policy import Policy, PolicyEngine
from .sdk import HumanApprovalRequired, ToolCallBlocked
from .stores import FileApprovalStore, FileAuditStore
from .supervisor import SecuritySupervisor
from .verifiers import KeywordEvidenceVerifier, VerifierRegistry


@dataclass(frozen=True)
class DemoStep:
    name: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class DemoSummary:
    output_dir: str
    steps: tuple[DemoStep, ...]
    audit_path: str
    approvals_path: str
    agent_states: dict[str, str]
    notifications: tuple[dict[str, Any], ...]


class DemoSupportTools:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tickets = {
            "ticket-1842": {
                "customer_id": "cust_991",
                "subject": "Duplicate annual subscription charge",
                "amount": 950,
                "plan": "Enterprise",
            }
        }
        self.knowledge_base = {
            "refund duplicate charge": (
                "Duplicate subscription charges can be refunded after invoice "
                "and payment processor verification."
            )
        }
        self.refunds: list[dict[str, Any]] = []

    def read_ticket(self, ticket_id: str) -> dict[str, Any]:
        return self.tickets[ticket_id]

    def search_knowledge_base(self, query: str) -> str:
        return self.knowledge_base.get(query, "No article found.")

    def draft_email(self, customer_id: str, body: str) -> str:
        path = self.output_dir / f"{customer_id}-draft-email.txt"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def send_email(self, customer_id: str, body: str) -> str:
        path = self.output_dir / f"{customer_id}-sent-email.txt"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def issue_refund(self, customer_id: str, amount: int, reason: str) -> str:
        self.refunds.append(
            {"customer_id": customer_id, "amount": amount, "reason": reason}
        )
        return f"refund-{len(self.refunds):04d}"

    def export_customer_database(self) -> str:
        return "customers.csv"


def build_demo_supervisor(output_dir: Path) -> SecuritySupervisor:
    project_root = Path(__file__).resolve().parents[2]
    policy = Policy.from_file(
        project_root / "policies" / "examples" / "customer_support_policy.json"
    )
    controller = InMemoryAgentController()
    notifier = InMemoryNotifier()
    return SecuritySupervisor(
        policy_engine=PolicyEngine(
            policy,
            verifier_registry=VerifierRegistry(KeywordEvidenceVerifier()),
        ),
        controller=controller,
        audit_store=FileAuditStore(output_dir / "audit.jsonl"),
        approval_store=FileApprovalStore(output_dir / "approvals.jsonl"),
        notifier=notifier,
    )


def run_demo(output_dir: str | Path = "demo_output") -> DemoSummary:
    output_path = Path(output_dir)
    supervisor = build_demo_supervisor(output_path)
    tools = DemoSupportTools(output_path)
    controller = supervisor.controller
    agent_id = "support-agent-real-demo"
    bad_agent_id = "support-agent-data-export"
    hallucination_agent_id = "support-agent-hallucination"

    if isinstance(controller, InMemoryAgentController):
        controller.register(agent_id)
        controller.register(bad_agent_id)
        controller.register(hallucination_agent_id)

    read_ticket = supervisor.guard_tool(
        "read_ticket",
        tools.read_ticket,
        default_metadata={"agent_role": "support_manager"},
    )
    search_kb = supervisor.guard_tool(
        "search_knowledge_base",
        tools.search_knowledge_base,
        default_metadata={"agent_role": "support_manager"},
    )
    draft_email = supervisor.guard_tool(
        "draft_email",
        tools.draft_email,
        default_metadata={"agent_role": "support_manager"},
    )
    send_email = supervisor.guard_tool(
        "send_email",
        tools.send_email,
        default_metadata={"agent_role": "support_manager"},
    )
    issue_refund = supervisor.guard_tool(
        "issue_refund",
        tools.issue_refund,
        default_metadata={"agent_role": "support_manager"},
    )
    export_database = supervisor.guard_tool(
        "export_customer_database",
        tools.export_customer_database,
        default_metadata={"agent_role": "support_manager"},
    )

    steps: list[DemoStep] = []

    ticket = read_ticket("ticket-1842", agent_id=agent_id, task_id="ticket-1842")
    steps.append(
        DemoStep(
            "read ticket",
            "allowed",
            f"{ticket['customer_id']} reported {ticket['subject']}",
        )
    )

    article = search_kb(
        "refund duplicate charge",
        agent_id=agent_id,
        task_id="ticket-1842",
    )
    steps.append(DemoStep("search knowledge base", "allowed", article))

    draft_path = draft_email(
        ticket["customer_id"],
        "We verified the duplicate charge and are preparing a refund.",
        agent_id=agent_id,
        task_id="ticket-1842",
    )
    steps.append(DemoStep("draft email", "allowed", draft_path))

    try:
        send_email(
            ticket["customer_id"],
            "Your duplicate charge refund is being reviewed.",
            agent_id=agent_id,
            task_id="ticket-1842",
        )
    except HumanApprovalRequired as approval:
        request_id = _approval_id(approval)
        steps.append(DemoStep("send email", "approval_required", request_id))
        supervisor.approve_request(
            request_id,
            reviewer="support-lead@example.com",
            reason="Customer-facing update is accurate.",
        )
        sent_path = send_email.execute_approved(request_id)
        steps.append(DemoStep("send email after approval", "executed", sent_path))

    try:
        issue_refund(
            ticket["customer_id"],
            ticket["amount"],
            reason="Duplicate annual subscription charge",
            agent_id=agent_id,
            task_id="ticket-1842",
        )
    except HumanApprovalRequired as approval:
        request_id = _approval_id(approval)
        steps.append(
            DemoStep(
                "issue high-value refund",
                "approval_required",
                f"{request_id} for ${ticket['amount']}",
            )
        )
        supervisor.approve_request(
            request_id,
            reviewer="finance@example.com",
            reason="Invoice and payment processor evidence verified.",
        )
        refund_id = issue_refund.execute_approved(request_id)
        steps.append(DemoStep("issue refund after approval", "executed", refund_id))

    try:
        export_database(
            agent_id=bad_agent_id,
            task_id="ticket-1842",
        )
    except ToolCallBlocked as blocked:
        steps.append(
            DemoStep(
                "export customer database",
                "blocked_and_agent_stopped",
                blocked.decision.summary,
            )
        )

    unsupported_decision = supervisor.observe(
        AgentEvent(
            agent_id=hallucination_agent_id,
            task_id="ticket-1842",
            event_type=EventType.RESULT,
            content="Completed. This customer has never disputed a charge before.",
            metadata={
                "claims": ["This customer has never disputed a charge before."],
                "sources": [],
                "artifacts": [],
            },
        )
    )
    steps.append(
        DemoStep(
            "unsupported factual claim",
            unsupported_decision.decision_type.value,
            unsupported_decision.summary,
        )
    )

    _write_summary_artifact(output_path, steps)

    agent_states = {}
    if isinstance(controller, InMemoryAgentController):
        agent_states = {
            agent: state.value
            for agent, state in sorted(controller.states.items())
        }

    notifications = ()
    if isinstance(supervisor.notifier, InMemoryNotifier):
        notifications = tuple(supervisor.notifier.events)

    return DemoSummary(
        output_dir=str(output_path),
        steps=tuple(steps),
        audit_path=str(output_path / "audit.jsonl"),
        approvals_path=str(output_path / "approvals.jsonl"),
        agent_states=agent_states,
        notifications=notifications,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentient-real-demo")
    parser.add_argument("--output-dir", default="demo_output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = run_demo(args.output_dir)
    if args.json:
        print(json.dumps(_summary_to_dict(summary), indent=2, sort_keys=True))
        return 0

    print("Sentient Real Demo")
    print("==================")
    for index, step in enumerate(summary.steps, start=1):
        print(f"{index}. {step.name}: {step.outcome}")
        print(f"   {step.detail}")
    print("\nArtifacts")
    print(f"- audit log: {summary.audit_path}")
    print(f"- approvals: {summary.approvals_path}")
    print(f"- output dir: {summary.output_dir}")
    print("\nAgent states")
    for agent_id, state in summary.agent_states.items():
        print(f"- {agent_id}: {state}")
    return 0


def _approval_id(error: HumanApprovalRequired) -> str:
    if error.approval_request is None:
        raise RuntimeError("Approval decision did not create an approval request.")
    return error.approval_request.request_id


def _write_summary_artifact(output_dir: Path, steps: list[DemoStep]) -> None:
    payload = {"steps": [asdict(step) for step in steps]}
    (output_dir / "demo-summary.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def _summary_to_dict(summary: DemoSummary) -> dict[str, Any]:
    return {
        "output_dir": summary.output_dir,
        "steps": [asdict(step) for step in summary.steps],
        "audit_path": summary.audit_path,
        "approvals_path": summary.approvals_path,
        "agent_states": summary.agent_states,
        "notifications": list(summary.notifications),
    }


if __name__ == "__main__":
    raise SystemExit(main())
