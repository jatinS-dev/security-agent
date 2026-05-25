from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from .controller import AgentController
from .models import (
    AgentEvent,
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    DecisionType,
    EnforcementMode,
)
from .policy import PolicyEngine
from .sdk import GuardedTool
from .stores import ApprovalStore, AuditStore, InMemoryApprovalStore
from .notifications import Notifier


@dataclass
class AuditRecord:
    timestamp: str
    event: AgentEvent
    decision: Decision


@dataclass
class SecuritySupervisor:
    policy_engine: PolicyEngine
    controller: AgentController
    enforcement_mode: EnforcementMode | str = EnforcementMode.ENFORCE
    audit_log: list[AuditRecord] = field(default_factory=list)
    audit_store: AuditStore | None = None
    approval_store: ApprovalStore = field(default_factory=InMemoryApprovalStore)
    notifier: Notifier | None = None

    def assign_task(self, agent_id: str, task_id: str) -> None:
        self.policy_engine.assign_task(agent_id, task_id)

    def guard_tool(
        self,
        tool_name: str,
        tool: Callable[..., Any],
        *,
        default_metadata: dict[str, Any] | None = None,
    ) -> GuardedTool:
        return GuardedTool(
            supervisor=self,
            tool_name=tool_name,
            tool=tool,
            default_metadata=default_metadata or {},
        )

    def observe(self, event: AgentEvent) -> Decision:
        violations = self.policy_engine.evaluate(event)
        enforcement_mode = EnforcementMode(self.enforcement_mode)
        decision_type = DecisionType.ALLOW
        if any(violation.action == "require_human_approval" for violation in violations):
            decision_type = DecisionType.REQUIRE_HUMAN_APPROVAL
        if any(
            violation.action == "stop_agent"
            or violation.severity.value in {"high", "critical"}
            for violation in violations
        ):
            decision_type = DecisionType.BLOCK

        decision = Decision(
            agent_id=event.agent_id,
            allowed=not violations,
            violations=violations,
            decision_type=decision_type,
            enforcement_mode=enforcement_mode,
            enforced=enforcement_mode == EnforcementMode.ENFORCE,
        )
        timestamp = _utc_now()
        self.audit_log.append(
            AuditRecord(
                timestamp=timestamp,
                event=event,
                decision=decision,
            )
        )
        if self.audit_store is not None:
            self.audit_store.append_decision(event, decision, timestamp)

        if decision.enforced and decision.should_stop_agent:
            self.controller.stop_agent(event.agent_id, decision.summary)
            self._notify("agent_stopped", {"agent_id": event.agent_id, "reason": decision.summary})

        return decision

    def create_approval_request(
        self,
        event: AgentEvent,
        decision: Decision,
    ) -> ApprovalRequest:
        tool_name = str(event.metadata.get("tool_name", "")).strip()
        reviewers = self.policy_engine.policy.approval_routes.get(tool_name, ())
        expires_at = None
        if self.policy_engine.policy.approval_expiration_minutes is not None:
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(minutes=self.policy_engine.policy.approval_expiration_minutes)
            ).isoformat()
        approval_request = ApprovalRequest(
            request_id=str(uuid4()),
            agent_id=event.agent_id,
            task_id=event.task_id,
            tool_name=tool_name,
            tool_args=event.metadata.get("tool_args", {}),
            metadata={
                key: value
                for key, value in event.metadata.items()
                if key not in {"tool_args"}
            },
            assigned_reviewers=reviewers,
            expires_at=expires_at,
            decision_summary=decision.summary,
            status=ApprovalStatus.PENDING,
            created_at=_utc_now(),
        )
        self.approval_store.create(approval_request)
        self._notify(
            "approval_requested",
            {
                "request_id": approval_request.request_id,
                "agent_id": approval_request.agent_id,
                "tool_name": approval_request.tool_name,
                "reviewers": list(approval_request.assigned_reviewers),
            },
        )
        return approval_request

    def list_pending_approvals(self) -> list[ApprovalRequest]:
        return self.approval_store.list(ApprovalStatus.PENDING)

    def approve_request(
        self,
        request_id: str,
        *,
        reviewer: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        approval = self.approval_store.update(
            request_id,
            status=ApprovalStatus.APPROVED,
            reviewed_at=_utc_now(),
            reviewer=reviewer,
            review_reason=reason,
        )
        self._notify("approval_approved", {"request_id": request_id, "reviewer": reviewer})
        return approval

    def reject_request(
        self,
        request_id: str,
        *,
        reviewer: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        approval = self.approval_store.update(
            request_id,
            status=ApprovalStatus.REJECTED,
            reviewed_at=_utc_now(),
            reviewer=reviewer,
            review_reason=reason,
        )
        self._notify("approval_rejected", {"request_id": request_id, "reviewer": reviewer})
        return approval

    def mark_request_executed(self, request_id: str) -> ApprovalRequest:
        return self.approval_store.update(
            request_id,
            status=ApprovalStatus.EXECUTED,
            reviewed_at=_utc_now(),
        )

    def pause_agent(self, agent_id: str, reason: str) -> None:
        self.controller.pause_agent(agent_id, reason)
        self._notify("agent_paused", {"agent_id": agent_id, "reason": reason})

    def resume_agent(self, agent_id: str, reason: str = "") -> None:
        self.controller.resume_agent(agent_id, reason)
        self._notify("agent_resumed", {"agent_id": agent_id, "reason": reason})

    def _notify(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.notifier is not None:
            self.notifier.notify(event_type, payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
