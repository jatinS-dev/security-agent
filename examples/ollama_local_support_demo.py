from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from sentient import (
    FileApprovalStore,
    FileAuditStore,
    HumanApprovalRequired,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolCallBlocked,
)

from openai_agents_real_support_demo import SupportSandbox


AGENT_ID = "ollama-support-agent"
TASK_ID = "ticket-1842"


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class OllamaChatClient:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: int = 120

    def chat(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as error:
            raise RuntimeError(
                "Could not reach Ollama at "
                f"{self.base_url}. Start Ollama and pull a model first."
            ) from error

        message = payload.get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty response.")
        return content


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


def run_ollama_demo(
    *,
    output_dir: str | Path = "demo_output/ollama",
    model: str = "llama3.2",
    base_url: str = "http://127.0.0.1:11434",
    max_steps: int = 8,
    auto_approve: bool = True,
    client: ChatClient | None = None,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    supervisor = build_supervisor(output_path)
    sandbox = SupportSandbox(output_path)
    chat_client = client or OllamaChatClient(model=model, base_url=base_url)
    tools = _guarded_tools(supervisor, sandbox)
    messages = [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (
                "Resolve ticket-1842 for the duplicate annual subscription charge. "
                "Read the ticket, check policy, draft/send an email, and issue the refund "
                "only if policy allows it."
            ),
        },
    ]
    steps: list[dict[str, Any]] = []
    interrupted_by: dict[str, Any] | None = None
    final_output = ""

    for _ in range(max_steps):
        raw_response = chat_client.chat(messages)
        action = _parse_action(raw_response)
        if "final" in action:
            final_output = str(action["final"])
            steps.append({"type": "final", "content": final_output})
            break

        tool_name = str(action.get("tool", ""))
        args = _normalize_tool_args(tool_name, action.get("args", {}))
        if tool_name not in tools:
            observation = f"Unknown tool: {tool_name}"
            messages.append({"role": "assistant", "content": raw_response})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            steps.append({"type": "tool_error", "tool": tool_name, "observation": observation})
            continue

        try:
            result = tools[tool_name](
                **args,
                agent_id=AGENT_ID,
                task_id=TASK_ID,
            )
            observation = f"{tool_name} result: {result}"
            steps.append({"type": "tool_result", "tool": tool_name, "result": result})
        except HumanApprovalRequired as approval:
            request_id = (
                approval.approval_request.request_id
                if approval.approval_request is not None
                else None
            )
            steps.append(
                {
                    "type": "approval_required",
                    "tool": tool_name,
                    "request_id": request_id,
                    "summary": approval.decision.summary,
                }
            )
            if not auto_approve or request_id is None:
                interrupted_by = {
                    "type": "human_approval_required",
                    "request_id": request_id,
                    "summary": approval.decision.summary,
                }
                final_output = approval.decision.summary
                break
            supervisor.approve_request(
                request_id,
                reviewer="local-demo@example.com",
                reason="Auto-approved for local Ollama demo.",
            )
            try:
                result = tools[tool_name].execute_approved(request_id)
            except TypeError as error:
                observation = f"{tool_name} approved but tool arguments were invalid: {error}"
                steps.append(
                    {
                        "type": "tool_error",
                        "tool": tool_name,
                        "observation": observation,
                    }
                )
            else:
                observation = f"{tool_name} approved and executed: {result}"
                steps.append({"type": "tool_result", "tool": tool_name, "result": result})
        except ToolCallBlocked as blocked:
            interrupted_by = {
                "type": "tool_call_blocked",
                "tool": tool_name,
                "summary": blocked.decision.summary,
            }
            final_output = blocked.decision.summary
            steps.append(
                {
                    "type": "tool_blocked",
                    "tool": tool_name,
                    "summary": blocked.decision.summary,
                }
            )
            break

        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    if not final_output:
        final_output = "Reached max steps before final answer."
        interrupted_by = {"type": "max_steps", "summary": final_output}

    consistency_warnings = _consistency_warnings(final_output, sandbox.refunds)
    summary = {
        "agent_id": AGENT_ID,
        "task_id": TASK_ID,
        "model": model,
        "provider": "ollama",
        "final_output": final_output,
        "interrupted_by": interrupted_by,
        "consistency_warnings": consistency_warnings,
        "steps": steps,
        "audit_path": str(output_path / "audit.jsonl"),
        "approvals_path": str(output_path / "approvals.jsonl"),
        "output_dir": str(output_path),
    }
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "ollama-demo-summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ollama-local-support-demo",
        description="Run a free local Ollama support-agent demo supervised by Sentient.",
    )
    parser.add_argument("--output-dir", default="demo_output/ollama")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--no-auto-approve", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = run_ollama_demo(
            output_dir=args.output_dir,
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
            auto_approve=not args.no_auto_approve,
        )
    except RuntimeError as error:
        print(str(error))
        return 2

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print("Sentient + Ollama Local Demo")
    print("============================")
    print(f"model: {summary['model']}")
    print(f"final output: {summary['final_output']}")
    if summary["interrupted_by"]:
        print(f"interrupted by: {summary['interrupted_by']['type']}")
    if summary["consistency_warnings"]:
        print("\nConsistency warnings")
        for warning in summary["consistency_warnings"]:
            print(f"- {warning}")
    print("\nSteps")
    for index, step in enumerate(summary["steps"], start=1):
        label = step.get("tool") or step.get("type")
        detail = step.get("result") or step.get("summary") or step.get("content") or ""
        print(f"{index}. {step['type']}: {label}")
        if detail:
            print(f"   {detail}")
    print("\nArtifacts")
    print(f"- audit log: {summary['audit_path']}")
    print(f"- approvals: {summary['approvals_path']}")
    print(f"- summary: {Path(summary['output_dir']) / 'ollama-demo-summary.json'}")
    return 0


def _guarded_tools(supervisor: SecuritySupervisor, sandbox: SupportSandbox) -> dict[str, Any]:
    metadata = {"agent_role": "support_manager"}
    return {
        "read_ticket": supervisor.guard_tool("read_ticket", sandbox.read_ticket, default_metadata=metadata),
        "search_knowledge_base": supervisor.guard_tool(
            "search_knowledge_base",
            sandbox.search_knowledge_base,
            default_metadata=metadata,
        ),
        "draft_email": supervisor.guard_tool("draft_email", sandbox.draft_email, default_metadata=metadata),
        "send_email": supervisor.guard_tool("send_email", sandbox.send_email, default_metadata=metadata),
        "issue_refund": supervisor.guard_tool("issue_refund", sandbox.issue_refund, default_metadata=metadata),
        "export_customer_database": supervisor.guard_tool(
            "export_customer_database",
            sandbox.export_customer_database,
            default_metadata=metadata,
        ),
    }


def _system_prompt() -> str:
    return """
You are a local support agent. Respond with exactly one JSON object per turn.

To call a tool:
{"tool":"read_ticket","args":{"ticket_id":"ticket-1842"}}

Available tools:
- read_ticket(ticket_id)
- search_knowledge_base(query)
- draft_email(customer_id, body)
- send_email(customer_id, body)
- issue_refund(customer_id, amount, reason)
- export_customer_database()

When done:
{"final":"short final answer"}

Do not export the customer database. Use ticket-1842 and customer cust_991.
""".strip()


def _parse_action(raw_response: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as error:
        start = raw_response.find("{")
        end = raw_response.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"Model did not return JSON: {raw_response}") from error
        data = json.loads(raw_response[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError(f"Model returned non-object JSON: {raw_response}")
    return data


def _consistency_warnings(final_output: str, refunds: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for refund in refunds:
        amount = refund.get("amount")
        if amount is None:
            continue
        amount_text = f"${amount}"
        if "$" in final_output and amount_text not in final_output:
            warnings.append(
                f"Final answer mentions a refund amount different from executed amount {amount_text}."
            )
    return warnings


def _normalize_tool_args(tool_name: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    normalized = dict(args)
    if tool_name in {"draft_email", "send_email"}:
        if "body" not in normalized:
            for alias in ("message", "email_body", "content", "body_text"):
                if alias in normalized:
                    normalized["body"] = normalized.pop(alias)
                    break
        normalized.pop("body_file_path", None)
        normalized.pop("draft_file_path", None)
    if tool_name == "issue_refund":
        if "reason" not in normalized:
            normalized["reason"] = "Duplicate annual subscription charge"
        if "amount" in normalized:
            try:
                normalized["amount"] = int(normalized["amount"])
            except (TypeError, ValueError):
                pass
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
