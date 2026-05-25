from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_registry import AgentRegistry
from .models import AgentEvent, EventType, PolicyViolation, Severity
from .verifiers import VerifierRegistry


@dataclass(frozen=True)
class PatternRule:
    id: str
    description: str
    severity: Severity
    action: str
    pattern: re.Pattern[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatternRule":
        return cls(
            id=data["id"],
            description=data["description"],
            severity=Severity(data.get("severity", Severity.HIGH)),
            action=data.get("action", "stop_agent"),
            pattern=re.compile(data["pattern"], re.IGNORECASE),
        )


@dataclass(frozen=True)
class Policy:
    name: str
    version: str
    allowed_tools: frozenset[str] = frozenset()
    blocked_tool_names: frozenset[str] = frozenset()
    tools_requiring_approval: frozenset[str] = frozenset()
    max_autonomous_amounts: dict[str, float] = field(default_factory=dict)
    tool_role_requirements: dict[str, frozenset[str]] = field(default_factory=dict)
    approval_tool_environments: dict[str, frozenset[str]] = field(default_factory=dict)
    blocked_tool_environments: dict[str, frozenset[str]] = field(default_factory=dict)
    approval_routes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    approval_expiration_minutes: int | None = None
    blocked_content_patterns: tuple[PatternRule, ...] = ()
    completion_requires_artifacts: bool = True
    factual_claims_require_sources: bool = True

    @classmethod
    def from_file(cls, path: str | Path) -> "Policy":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        return cls(
            name=data.get("name", "Supervisor Policy"),
            version=data.get("version", "0.0.0"),
            allowed_tools=frozenset(data.get("allowed_tools", [])),
            blocked_tool_names=frozenset(data.get("blocked_tool_names", [])),
            tools_requiring_approval=frozenset(
                data.get("tools_requiring_approval", [])
            ),
            max_autonomous_amounts={
                str(tool_name): float(amount)
                for tool_name, amount in data.get("max_autonomous_amounts", {}).items()
            },
            tool_role_requirements=_string_set_map(
                data.get("tool_role_requirements", {})
            ),
            approval_tool_environments=_string_set_map(
                data.get("approval_tool_environments", {})
            ),
            blocked_tool_environments=_string_set_map(
                data.get("blocked_tool_environments", {})
            ),
            approval_routes={
                str(tool_name): tuple(str(item) for item in reviewers)
                for tool_name, reviewers in data.get("approval_routes", {}).items()
            },
            approval_expiration_minutes=data.get("approval_expiration_minutes"),
            blocked_content_patterns=tuple(
                PatternRule.from_dict(rule)
                for rule in data.get("blocked_content_patterns", [])
            ),
            completion_requires_artifacts=bool(
                data.get("completion_requires_artifacts", True)
            ),
            factual_claims_require_sources=bool(
                data.get("factual_claims_require_sources", True)
            ),
        )


@dataclass
class PolicyEngine:
    policy: Policy
    task_assignments: dict[str, str] = field(default_factory=dict)
    verifier_registry: VerifierRegistry | None = None
    agent_registry: AgentRegistry | None = None

    def assign_task(self, agent_id: str, task_id: str) -> None:
        self.task_assignments[agent_id] = task_id

    def evaluate(self, event: AgentEvent) -> tuple[PolicyViolation, ...]:
        violations: list[PolicyViolation] = []
        violations.extend(self._check_agent_registry(event))
        violations.extend(self._check_task_boundary(event))
        violations.extend(self._check_tool_policy(event))
        violations.extend(self._check_role_rules(event))
        violations.extend(self._check_environment_rules(event))
        violations.extend(self._check_approval_rules(event))
        violations.extend(self._check_blocked_content(event))
        violations.extend(self._check_evidence_rules(event))
        return tuple(violations)

    def _check_agent_registry(self, event: AgentEvent) -> list[PolicyViolation]:
        if self.agent_registry is None:
            return []

        profile = self.agent_registry.get(event.agent_id)
        if profile is None:
            return [
                PolicyViolation(
                    rule_id="unknown-agent",
                    description="Agent is not registered.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=event.agent_id,
                )
            ]

        event_role = str(event.metadata.get("agent_role", "")).strip()
        if event_role and event_role != profile.role:
            return [
                PolicyViolation(
                    rule_id="agent-role-mismatch",
                    description="Agent role does not match registry.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=f"event={event_role}, registry={profile.role}",
                )
            ]

        if (
            event.task_id
            and profile.allowed_tasks
            and event.task_id not in profile.allowed_tasks
        ):
            return [
                PolicyViolation(
                    rule_id="task-not-allowed-for-agent",
                    description="Agent attempted a task outside its registry allowlist.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=f"task={event.task_id}, allowed={sorted(profile.allowed_tasks)}",
                )
            ]

        return []

    def _check_task_boundary(self, event: AgentEvent) -> list[PolicyViolation]:
        assigned_task = self.task_assignments.get(event.agent_id)
        if not assigned_task or not event.task_id:
            return []
        if assigned_task == event.task_id:
            return []
        return [
            PolicyViolation(
                rule_id="wrong-task",
                description="Agent attempted to operate outside its assigned task.",
                severity=Severity.HIGH,
                action="stop_agent",
                evidence=f"assigned={assigned_task}, event={event.task_id}",
            )
        ]

    def _check_tool_policy(self, event: AgentEvent) -> list[PolicyViolation]:
        if event.event_type != EventType.TOOL_CALL:
            return []

        tool_name = str(event.metadata.get("tool_name", "")).strip()
        if not tool_name:
            return [
                PolicyViolation(
                    rule_id="missing-tool-name",
                    description="Tool call did not include a tool name.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=event.content,
                )
            ]

        if tool_name in self.policy.blocked_tool_names:
            return [
                PolicyViolation(
                    rule_id="blocked-tool",
                    description=f"Agent attempted to use blocked tool: {tool_name}.",
                    severity=Severity.CRITICAL,
                    action="stop_agent",
                    evidence=tool_name,
                )
            ]

        if self.policy.allowed_tools and tool_name not in self.policy.allowed_tools:
            return [
                PolicyViolation(
                    rule_id="unapproved-tool",
                    description=f"Agent attempted to use unapproved tool: {tool_name}.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=tool_name,
                )
            ]

        return []

    def _check_role_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        if event.event_type != EventType.TOOL_CALL:
            return []

        tool_name = str(event.metadata.get("tool_name", "")).strip()
        allowed_roles = self.policy.tool_role_requirements.get(tool_name)
        if not tool_name or not allowed_roles:
            return []

        agent_role = self._agent_role(event)
        if agent_role in allowed_roles:
            return []

        role_evidence = agent_role or "missing"
        return [
            PolicyViolation(
                rule_id="role-not-authorized",
                description=(
                    f"Agent role is not authorized to use tool {tool_name}."
                ),
                severity=Severity.HIGH,
                action="stop_agent",
                evidence=f"role={role_evidence}, allowed={sorted(allowed_roles)}",
            )
        ]

    def _agent_role(self, event: AgentEvent) -> str:
        event_role = str(event.metadata.get("agent_role", "")).strip()
        if event_role:
            return event_role
        if self.agent_registry is None:
            return ""
        profile = self.agent_registry.get(event.agent_id)
        return profile.role if profile else ""

    def _check_environment_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        if event.event_type != EventType.TOOL_CALL:
            return []

        tool_name = str(event.metadata.get("tool_name", "")).strip()
        if not tool_name:
            return []

        environment = _extract_tool_arg(event, "environment")
        if environment is None:
            environment = event.metadata.get("environment")
        if environment is None:
            return []

        environment_name = str(environment).strip()
        blocked_environments = self.policy.blocked_tool_environments.get(tool_name)
        if blocked_environments and environment_name in blocked_environments:
            return [
                PolicyViolation(
                    rule_id="blocked-environment",
                    description=(
                        f"Tool {tool_name} cannot be used in {environment_name}."
                    ),
                    severity=Severity.CRITICAL,
                    action="stop_agent",
                    evidence=f"tool={tool_name}, environment={environment_name}",
                )
            ]

        approval_environments = self.policy.approval_tool_environments.get(tool_name)
        if approval_environments and environment_name in approval_environments:
            return [
                PolicyViolation(
                    rule_id="environment-requires-approval",
                    description=(
                        f"Tool {tool_name} requires human approval in {environment_name}."
                    ),
                    severity=Severity.MEDIUM,
                    action="require_human_approval",
                    evidence=f"tool={tool_name}, environment={environment_name}",
                )
            ]

        return []

    def _check_approval_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        if event.event_type != EventType.TOOL_CALL:
            return []

        tool_name = str(event.metadata.get("tool_name", "")).strip()
        if not tool_name:
            return []

        violations: list[PolicyViolation] = []
        if tool_name in self.policy.tools_requiring_approval:
            violations.append(
                PolicyViolation(
                    rule_id="tool-requires-approval",
                    description=f"Tool requires human approval before execution: {tool_name}.",
                    severity=Severity.MEDIUM,
                    action="require_human_approval",
                    evidence=tool_name,
                )
            )

        max_amount = self.policy.max_autonomous_amounts.get(tool_name)
        amount = _extract_numeric_tool_arg(event, "amount")
        if max_amount is not None and amount is not None and amount > max_amount:
            violations.append(
                PolicyViolation(
                    rule_id="amount-requires-approval",
                    description=(
                        f"Tool call amount {amount:g} exceeds autonomous "
                        f"limit {max_amount:g} for {tool_name}."
                    ),
                    severity=Severity.MEDIUM,
                    action="require_human_approval",
                    evidence=f"amount={amount:g}, max={max_amount:g}",
                )
            )

        return violations

    def _check_blocked_content(self, event: AgentEvent) -> list[PolicyViolation]:
        violations: list[PolicyViolation] = []
        search_text = " ".join(
            [
                event.content,
                str(event.metadata.get("command", "")),
                str(event.metadata.get("tool_args", "")),
            ]
        )

        for rule in self.policy.blocked_content_patterns:
            match = rule.pattern.search(search_text)
            if match:
                violations.append(
                    PolicyViolation(
                        rule_id=rule.id,
                        description=rule.description,
                        severity=rule.severity,
                        action=rule.action,
                        evidence=match.group(0),
                    )
                )
        return violations

    def _check_evidence_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        violations: list[PolicyViolation] = []
        claims = event.metadata.get("claims", [])
        sources = event.metadata.get("sources", [])
        artifacts = event.metadata.get("artifacts", [])
        evidence_text = _extract_evidence_text(event.metadata)

        if (
            self.policy.factual_claims_require_sources
            and event.event_type in {EventType.MESSAGE, EventType.RESULT}
            and claims
            and not sources
        ):
            violations.append(
                PolicyViolation(
                    rule_id="unsupported-claim",
                    description="Agent made factual claims without sources or verifier evidence.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=str(claims[:3]),
                )
            )

        if (
            self.policy.factual_claims_require_sources
            and self.verifier_registry is not None
            and event.event_type in {EventType.MESSAGE, EventType.RESULT}
            and claims
            and sources
        ):
            if not evidence_text:
                violations.append(
                    PolicyViolation(
                        rule_id="missing-evidence-text",
                        description=(
                            "Agent provided sources but no evidence text for verifier checks."
                        ),
                        severity=Severity.HIGH,
                        action="stop_agent",
                        evidence=str(sources[:3]),
                    )
                )
            else:
                for claim in claims:
                    result = self.verifier_registry.verify(str(claim), evidence_text)
                    if not result.supported:
                        violations.append(
                            PolicyViolation(
                                rule_id="claim-not-supported-by-evidence",
                                description="Claim was not supported by provided evidence text.",
                                severity=Severity.HIGH,
                                action="stop_agent",
                                evidence=(
                                    f"claim={claim}; reason={result.reason}; "
                                    f"missing={list(result.missing_terms)}"
                                ),
                            )
                        )

        completion_words = ("done", "completed", "fixed", "implemented", "finished")
        claims_completion = any(word in event.content.lower() for word in completion_words)
        if (
            self.policy.completion_requires_artifacts
            and event.event_type == EventType.RESULT
            and claims_completion
            and not artifacts
        ):
            violations.append(
                PolicyViolation(
                    rule_id="completion-without-artifacts",
                    description="Agent claimed completion without providing artifacts.",
                    severity=Severity.HIGH,
                    action="stop_agent",
                    evidence=event.content,
                )
            )

        return violations


def _string_set_map(data: dict[str, Any]) -> dict[str, frozenset[str]]:
    return {
        str(key): frozenset(str(item) for item in value)
        for key, value in data.items()
    }


def _extract_tool_arg(event: AgentEvent, key: str) -> Any | None:
    tool_args = event.metadata.get("tool_args", {})
    if not isinstance(tool_args, dict):
        return None

    for container_key in ("bound_args", "kwargs"):
        container = tool_args.get(container_key)
        if isinstance(container, dict) and key in container:
            return container[key]

    if key in tool_args:
        return tool_args[key]

    return None


def _extract_numeric_tool_arg(event: AgentEvent, key: str) -> float | None:
    candidates = [_extract_tool_arg(event, key)]

    for candidate in candidates:
        try:
            if candidate is None:
                continue
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _extract_evidence_text(metadata: dict[str, Any]) -> str:
    evidence_text = metadata.get("evidence_text", "")
    if isinstance(evidence_text, str):
        return evidence_text
    if isinstance(evidence_text, list):
        return "\n".join(str(item) for item in evidence_text)
    if isinstance(evidence_text, dict):
        return "\n".join(str(item) for item in evidence_text.values())
    return str(evidence_text) if evidence_text else ""
