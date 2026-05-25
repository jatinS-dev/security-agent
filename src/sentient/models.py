from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    PLAN = "plan"
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    RESULT = "result"
    ERROR = "error"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class DecisionType(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_HUMAN_APPROVAL = "require_human_approval"


class EnforcementMode(str, Enum):
    ENFORCE = "enforce"
    SHADOW = "shadow"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


@dataclass(frozen=True)
class AgentEvent:
    agent_id: str
    event_type: EventType
    content: str
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyViolation:
    rule_id: str
    description: str
    severity: Severity
    action: str
    evidence: str


@dataclass(frozen=True)
class Decision:
    agent_id: str
    allowed: bool
    violations: tuple[PolicyViolation, ...] = ()
    decision_type: DecisionType = DecisionType.ALLOW
    enforcement_mode: EnforcementMode = EnforcementMode.ENFORCE
    enforced: bool = True

    @property
    def should_stop_agent(self) -> bool:
        return any(
            violation.action == "stop_agent"
            or violation.severity in {Severity.HIGH, Severity.CRITICAL}
            for violation in self.violations
        )

    @property
    def requires_human_approval(self) -> bool:
        return self.decision_type == DecisionType.REQUIRE_HUMAN_APPROVAL

    @property
    def summary(self) -> str:
        if self.allowed:
            return "allowed"
        return "; ".join(
            f"{violation.rule_id}: {violation.description}"
            for violation in self.violations
        )


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    agent_id: str
    tool_name: str
    decision_summary: str
    status: ApprovalStatus
    created_at: str
    task_id: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    assigned_reviewers: tuple[str, ...] = ()
    expires_at: str | None = None
    reviewed_at: str | None = None
    reviewer: str | None = None
    review_reason: str | None = None
