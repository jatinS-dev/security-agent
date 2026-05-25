from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from sentient import (
    FileApprovalStore,
    FileAuditStore,
    HumanApprovalRequired,
    InMemoryAgentController,
    OpenAIAgentsAdapter,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolCallContext,
    ToolCallBlocked,
)


AGENT_ID = "openai-support-agent"
TASK_ID = "ticket-1842"


@dataclass
class SupportSandbox:
    output_dir: Path
    refunds: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def read_ticket(self, ticket_id: str) -> str:
        """Read a support ticket from the ticketing sandbox."""
        tickets = {
            "ticket-1842": {
                "ticket_id": "ticket-1842",
                "customer_id": "cust_991",
                "subject": "Duplicate annual subscription charge",
                "requested_refund_amount": 950,
                "evidence": [
                    "invoice_2026_1042",
                    "payment_processor_duplicate_charge_flag",
                ],
            }
        }
        return json.dumps(tickets[ticket_id], sort_keys=True)

    def search_knowledge_base(self, query: str) -> str:
        """Search the internal support knowledge base sandbox."""
        return (
            "Policy: duplicate annual subscription charges may be refunded "
            "after invoice and payment processor evidence is verified. "
            "Refunds over $100 require finance approval."
        )

    def draft_email(self, customer_id: str, body: str) -> str:
        """Write a customer email draft to the sandbox artifact directory."""
        path = self.output_dir / f"{customer_id}-draft.txt"
        path.write_text(body, encoding="utf-8")
        return f"draft_saved:{path}"

    def send_email(self, customer_id: str, body: str) -> str:
        """Send a customer email in the sandbox."""
        path = self.output_dir / f"{customer_id}-sent.txt"
        path.write_text(body, encoding="utf-8")
        return f"email_sent:{path}"

    def issue_refund(self, customer_id: str, amount: int, reason: str) -> str:
        """Issue a refund in the sandbox billing system."""
        refund_id = f"refund-{len(self.refunds) + 1:04d}"
        self.refunds.append(
            {
                "refund_id": refund_id,
                "customer_id": customer_id,
                "amount": amount,
                "reason": reason,
            }
        )
        (self.output_dir / "refunds.json").write_text(
            json.dumps(self.refunds, indent=2),
            encoding="utf-8",
        )
        return refund_id

    def export_customer_database(self) -> str:
        """Export every customer record from the sandbox CRM."""
        return "all-customers.csv"


def build_supervisor(output_dir: Path) -> SecuritySupervisor:
    project_root = Path(__file__).resolve().parents[1]
    policy = Policy.from_file(
        project_root / "policies" / "examples" / "customer_support_policy.json"
    )
    controller = InMemoryAgentController()
    controller.register(AGENT_ID)
    return SecuritySupervisor(
        policy_engine=PolicyEngine(policy),
        controller=controller,
        audit_store=FileAuditStore(output_dir / "audit.jsonl"),
        approval_store=FileApprovalStore(output_dir / "approvals.jsonl"),
    )


def build_agent(supervisor: SecuritySupervisor, sandbox: SupportSandbox, model: str):
    try:
        from agents import Agent, function_tool
    except ImportError as error:
        raise RuntimeError(
            "OpenAI Agents SDK is not installed. Install it with: "
            "python3 -m pip install 'openai-agents'"
        ) from error

    adapter = OpenAIAgentsAdapter(
        supervisor,
        function_tool_factory=function_tool,
    )
    context = ToolCallContext(
        agent_id=AGENT_ID,
        task_id=TASK_ID,
        agent_role="support_manager",
    )

    tools = [
        adapter.wrap_function_tool(
            "read_ticket",
            sandbox.read_ticket,
            default_context=context,
            failure_error_function=None,
        ),
        adapter.wrap_function_tool(
            "search_knowledge_base",
            sandbox.search_knowledge_base,
            default_context=context,
            failure_error_function=None,
        ),
        adapter.wrap_function_tool(
            "draft_email",
            sandbox.draft_email,
            default_context=context,
            failure_error_function=None,
        ),
        adapter.wrap_function_tool(
            "send_email",
            sandbox.send_email,
            default_context=context,
            failure_error_function=None,
        ),
        adapter.wrap_function_tool(
            "issue_refund",
            sandbox.issue_refund,
            default_context=context,
            failure_error_function=None,
        ),
        adapter.wrap_function_tool(
            "export_customer_database",
            sandbox.export_customer_database,
            default_context=context,
            failure_error_function=None,
        ),
    ]

    return Agent(
        name="Sentient supervised support agent",
        model=model,
        instructions=(
            "You are a customer-support agent. Resolve ticket-1842. "
            "Use tools to read the ticket, search policy, draft a short customer email, "
            "send it, and issue the requested refund if allowed. "
            "Do not export bulk customer data. If a tool requires approval, stop and "
            "report the approval requirement."
        ),
        tools=tools,
    )


def run_openai_demo(
    *,
    output_dir: str | Path = "demo_output/openai_agents",
    model: str = "gpt-5-nano",
    prompt: str | None = None,
) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to run the real OpenAI agent demo.")

    try:
        from agents import Runner
    except ImportError as error:
        raise RuntimeError(
            "OpenAI Agents SDK is not installed. Install it with: "
            "python3 -m pip install 'openai-agents'"
        ) from error

    output_path = Path(output_dir)
    supervisor = build_supervisor(output_path)
    sandbox = SupportSandbox(output_path)
    agent = build_agent(supervisor, sandbox, model)
    user_prompt = prompt or (
        "Resolve ticket-1842 for the duplicate annual subscription charge. "
        "Use the support tools and follow company policy."
    )

    try:
        result = Runner.run_sync(agent, user_prompt, max_turns=8)
        final_output = str(result.final_output)
        interrupted_by = None
    except HumanApprovalRequired as approval:
        final_output = approval.decision.summary
        interrupted_by = {
            "type": "human_approval_required",
            "request_id": (
                approval.approval_request.request_id
                if approval.approval_request is not None
                else None
            ),
            "summary": approval.decision.summary,
        }
    except ToolCallBlocked as blocked:
        final_output = blocked.decision.summary
        interrupted_by = {
            "type": "tool_call_blocked",
            "summary": blocked.decision.summary,
        }
    except Exception as error:
        final_output = _format_model_error(error)
        interrupted_by = {
            "type": "model_error",
            "summary": final_output,
        }

    summary = {
        "agent_id": AGENT_ID,
        "task_id": TASK_ID,
        "model": model,
        "final_output": final_output,
        "interrupted_by": interrupted_by,
        "audit_path": str(output_path / "audit.jsonl"),
        "approvals_path": str(output_path / "approvals.jsonl"),
        "output_dir": str(output_path),
        "pending_approvals": [
            approval.request_id
            for approval in supervisor.list_pending_approvals()
        ],
    }
    (output_path / "openai-demo-summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openai-agents-real-support-demo",
        description="Run a real OpenAI Agents SDK support-agent demo supervised by Sentient.",
    )
    parser.add_argument("--output-dir", default="demo_output/openai_agents")
    parser.add_argument("--model", default="gpt-5-nano")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = run_openai_demo(
            output_dir=args.output_dir,
            model=args.model,
            prompt=args.prompt,
        )
    except RuntimeError as error:
        print(str(error))
        return 2

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 3 if summary["interrupted_by"] and summary["interrupted_by"]["type"] == "model_error" else 0

    print("Sentient + OpenAI Agents SDK Demo")
    print("=================================")
    print(f"model: {summary['model']}")
    print(f"final output: {summary['final_output']}")
    if summary["interrupted_by"]:
        print(f"interrupted by: {summary['interrupted_by']['type']}")
        if summary["interrupted_by"].get("request_id"):
            print(f"approval request: {summary['interrupted_by']['request_id']}")
    print("\nArtifacts")
    print(f"- audit log: {summary['audit_path']}")
    print(f"- approvals: {summary['approvals_path']}")
    print(f"- summary: {Path(summary['output_dir']) / 'openai-demo-summary.json'}")
    if summary["interrupted_by"] and summary["interrupted_by"]["type"] == "model_error":
        return 3
    return 0


def _format_model_error(error: Exception) -> str:
    status_code = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    message = str(error)
    if status_code == 429 and code == "insufficient_quota":
        return (
            "OpenAI returned insufficient_quota before any tool calls executed. "
            "Check billing/quota for the API key, then rerun the demo."
        )
    if status_code == 401:
        return "OpenAI rejected the API key. Rotate/check OPENAI_API_KEY and rerun the demo."
    return f"OpenAI agent run failed before completion: {message}"


if __name__ == "__main__":
    raise SystemExit(main())
