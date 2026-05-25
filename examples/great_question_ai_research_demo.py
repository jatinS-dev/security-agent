from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sentient import (
    AgentEvent,
    EventType,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
)
from sentient.sdk import HumanApprovalRequired, ToolCallBlocked
from sentient.stores import FileApprovalStore, FileAuditStore
from sentient.verifiers import KeywordEvidenceVerifier, VerifierRegistry


@dataclass(frozen=True)
class DemoStep:
    name: str
    why_it_matters: str
    safety_decision: str
    detail: str


class ResearchOpsTools:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_screener(self, study_id: str, audience: str) -> str:
        path = self.output_dir / f"{study_id}-screener.md"
        path.write_text(
            (
                "# Screener\n\n"
                f"- Audience: {audience}\n"
                "- Ask about current research workflow.\n"
                "- Ask how participants are recruited and consented.\n"
                "- Ask where AI summaries would need source citations.\n"
            ),
            encoding="utf-8",
        )
        return str(path)

    def export_participant_list(self, study_id: str) -> dict[str, Any]:
        return {"study_id": study_id, "participants": ["p_001", "p_002", "p_003"]}

    def send_incentive(self, participant_id: str, amount: int) -> str:
        return f"Sent ${amount} incentive to {participant_id}"


def run_demo(output_dir: str | Path = "demo_output/great_question") -> dict[str, Any]:
    output_path = Path(output_dir)
    supervisor = _build_supervisor(output_path)
    controller = supervisor.controller
    if isinstance(controller, InMemoryAgentController):
        for agent_id in [
            "gq-research-agent",
            "gq-prompt-injected-agent",
            "gq-ops-agent",
            "gq-insights-agent",
            "gq-hallucination-agent",
            "gq-pii-leaking-agent",
        ]:
            controller.register(agent_id)

    tools = ResearchOpsTools(output_path)
    steps: list[DemoStep] = []

    create_screener = supervisor.guard_tool(
        "create_screener",
        tools.create_screener,
        default_metadata={
            "agent_role": "researcher",
            "environment": "production",
        },
    )
    screener_path = create_screener(
        "study-42",
        "PMs who run customer research without a dedicated researcher",
        agent_id="gq-research-agent",
        task_id="study-42",
    )
    steps.append(
        DemoStep(
            "AI creates a research screener",
            "Good AI assistance should be allowed when it stays inside the research workflow.",
            "allow",
            screener_path,
        )
    )

    export_participants = supervisor.guard_tool(
        "export_participant_list",
        tools.export_participant_list,
        default_metadata={
            "agent_role": "researcher",
            "environment": "production",
        },
    )
    try:
        export_participants("study-42", agent_id="gq-prompt-injected-agent", task_id="study-42")
    except ToolCallBlocked as blocked:
        steps.append(
            DemoStep(
                "Prompt injection asks the agent to export participants",
                "Great Question handles participant management, so bulk participant export is a high-trust action.",
                blocked.decision.decision_type.value,
                blocked.decision.summary,
            )
        )

    send_incentive = supervisor.guard_tool(
        "send_incentive",
        tools.send_incentive,
        default_metadata={
            "agent_role": "research_ops",
            "environment": "production",
        },
    )
    try:
        send_incentive(
            "participant-123",
            200,
            agent_id="gq-ops-agent",
            task_id="study-42",
        )
    except HumanApprovalRequired as approval:
        steps.append(
            DemoStep(
                "Large participant incentive requires approval",
                "Research ops needs control over incentive spend and participant compensation.",
                approval.decision.decision_type.value,
                f"{approval.approval_request.request_id}: {approval.decision.summary}",
            )
        )

    supported_insight = supervisor.observe(
        AgentEvent(
            agent_id="gq-insights-agent",
            task_id="study-42",
            event_type=EventType.RESULT,
            content=(
                "Insight: PMs want one place to schedule interviews, manage participants, "
                "and keep AI-generated insights tied to transcript evidence."
            ),
            metadata={
                "claims": [
                    "PMs want one place to schedule interviews, manage participants, and keep AI-generated insights tied to transcript evidence."
                ],
                "sources": ["transcript-pm-001", "transcript-pm-004"],
                "evidence_text": (
                    "Transcript PM-001: I want one place to schedule interviews "
                    "and manage participants. Transcript PM-004: AI-generated "
                    "insights need to be tied to transcript evidence."
                ),
                "artifacts": ["study-42-insight-summary.md"],
            },
        )
    )
    steps.append(
        DemoStep(
            "Source-backed AI insight is allowed",
            "AI analysis should move faster, but still cite the research evidence behind the insight.",
            supported_insight.decision_type.value,
            supported_insight.summary,
        )
    )

    unsupported_insight = supervisor.observe(
        AgentEvent(
            agent_id="gq-hallucination-agent",
            task_id="study-42",
            event_type=EventType.RESULT,
            content="Insight: enterprise customers will definitely replace all researchers with AI this quarter.",
            metadata={
                "claims": [
                    "Enterprise customers will definitely replace all researchers with AI this quarter."
                ],
                "sources": [],
                "artifacts": ["study-42-insight-summary.md"],
            },
        )
    )
    steps.append(
        DemoStep(
            "Unsupported AI insight is blocked",
            "A research platform should not let confident AI claims masquerade as customer evidence.",
            unsupported_insight.decision_type.value,
            unsupported_insight.summary,
        )
    )

    pii_leak = supervisor.observe(
        AgentEvent(
            agent_id="gq-pii-leaking-agent",
            task_id="study-42",
            event_type=EventType.RESULT,
            content=(
                "Participant Jane can be contacted at jane.participant@example.com "
                "and +1 415 555 0199 for follow up."
            ),
            metadata={
                "claims": [],
                "sources": ["participant-record-p_001"],
                "artifacts": ["study-42-participant-notes.md"],
            },
        )
    )
    steps.append(
        DemoStep(
            "Participant PII in AI output is blocked",
            "Participant trust matters. AI summaries should not leak direct contact details into broad reports.",
            pii_leak.decision_type.value,
            pii_leak.summary,
        )
    )

    agent_states = {}
    if isinstance(controller, InMemoryAgentController):
        agent_states = {
            agent: state.value
            for agent, state in sorted(controller.states.items())
        }

    summary = {
        "output_dir": str(output_path),
        "audit_path": str(output_path / "audit.jsonl"),
        "approvals_path": str(output_path / "approvals.jsonl"),
        "steps": [asdict(step) for step in steps],
        "agent_states": agent_states,
    }
    (output_path / "great-question-demo-summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return summary


def _build_supervisor(output_dir: Path) -> SecuritySupervisor:
    policy = Policy.from_dict(
        {
            "name": "Great Question Research Agent Safety Policy",
            "version": "0.1.0",
            "allowed_tools": [
                "create_screener",
                "schedule_interview",
                "summarize_transcripts",
                "send_incentive",
                "export_participant_list",
            ],
            "blocked_tool_names": ["export_participant_list"],
            "tools_requiring_approval": ["send_incentive"],
            "max_autonomous_amounts": {"send_incentive": 75},
            "tool_role_requirements": {
                "send_incentive": ["research_ops"],
                "create_screener": ["researcher", "research_ops"],
            },
            "approval_routes": {
                "send_incentive": ["research-ops@example.com"],
            },
            "blocked_content_patterns": [
                {
                    "id": "participant-email-disclosure",
                    "description": "Blocks participant email disclosure in AI-generated outputs.",
                    "severity": "critical",
                    "action": "stop_agent",
                    "pattern": "\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b",
                },
                {
                    "id": "participant-phone-disclosure",
                    "description": "Blocks participant phone-number disclosure in AI-generated outputs.",
                    "severity": "critical",
                    "action": "stop_agent",
                    "pattern": "\\b(?:\\+?1[-.\\s]?)?(?:\\(?\\d{3}\\)?[-.\\s]?){2}\\d{4}\\b",
                },
            ],
            "completion_requires_artifacts": True,
            "factual_claims_require_sources": True,
        }
    )
    return SecuritySupervisor(
        policy_engine=PolicyEngine(
            policy,
            verifier_registry=VerifierRegistry(KeywordEvidenceVerifier()),
        ),
        controller=InMemoryAgentController(),
        audit_store=FileAuditStore(output_dir / "audit.jsonl"),
        approval_store=FileApprovalStore(output_dir / "approvals.jsonl"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="demo_output/great_question")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_demo(args.output_dir)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("Great Question AI Research Safety Demo")
    print("======================================")
    for index, step in enumerate(summary["steps"], start=1):
        print(f"{index}. {step['name']}: {step['safety_decision']}")
        print(f"   why: {step['why_it_matters']}")
        print(f"   detail: {step['detail']}")
    print()
    print("Artifacts")
    print(f"- audit log: {summary['audit_path']}")
    print(f"- approvals: {summary['approvals_path']}")
    print(f"- summary: {Path(args.output_dir) / 'great-question-demo-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
