from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import AgentEvent, PolicyViolation, Severity


class LLMClient(Protocol):
    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class LLMRuleProposal:
    rule_type: str
    description: str
    severity: Severity
    action: str
    tool_name: str | None = None
    max_amount: float | None = None
    allowed_roles: tuple[str, ...] = ()
    pattern: str | None = None


@dataclass(frozen=True)
class LLMRiskAssessment:
    verdict: str
    severity: Severity
    reason: str
    evidence: str
    confidence: float = 0.0

    def to_violation(self) -> PolicyViolation | None:
        if self.verdict == "allow":
            return None
        action = "stop_agent" if self.verdict == "block" else "require_human_approval"
        return PolicyViolation(
            rule_id="llm-risk-assessment",
            description=f"LLM risk brain flagged event: {self.reason}",
            severity=self.severity,
            action=action,
            evidence=f"confidence={self.confidence:.2f}; {self.evidence}",
        )


@dataclass
class OllamaLLMClient:
    model: str = "llama3.2"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 30.0

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url}. Start Ollama and pull {self.model} first."
            ) from error

        content = data.get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty response.")
        return _parse_json_object(content)


@dataclass
class LLMBrain:
    client: LLMClient
    min_confidence: float = 0.55
    max_context_chars: int = 5000

    def extract_rules_from_text(
        self,
        *,
        tenant_id: str,
        document_title: str,
        chunk_heading: str,
        text: str,
    ) -> list[LLMRuleProposal]:
        payload = self.client.complete_json(
            [
                {
                    "role": "system",
                    "content": _RULE_EXTRACTION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "tenant_id": tenant_id,
                            "document_title": document_title,
                            "chunk_heading": chunk_heading,
                            "text": text[: self.max_context_chars],
                        },
                        sort_keys=True,
                    ),
                },
            ]
        )
        rules = payload.get("rules", [])
        if not isinstance(rules, list):
            return []
        proposals: list[LLMRuleProposal] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            proposal = _llm_rule_from_dict(item)
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def assess_event(
        self,
        *,
        event: AgentEvent,
        deterministic_violations: tuple[PolicyViolation, ...] = (),
        context_matches: list[dict[str, Any]] | None = None,
        active_rules: list[dict[str, Any]] | None = None,
    ) -> LLMRiskAssessment:
        payload = self.client.complete_json(
            [
                {
                    "role": "system",
                    "content": _RISK_ASSESSMENT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "event": _event_to_dict(event),
                            "deterministic_violations": [
                                _violation_to_dict(violation)
                                for violation in deterministic_violations
                            ],
                            "context_matches": context_matches or [],
                            "active_rules": active_rules or [],
                        },
                        sort_keys=True,
                    )[: self.max_context_chars],
                },
            ]
        )
        assessment = _assessment_from_dict(payload)
        if assessment.confidence < self.min_confidence:
            return LLMRiskAssessment(
                verdict="allow",
                severity=Severity.LOW,
                reason="LLM risk confidence below enforcement threshold.",
                evidence=assessment.evidence,
                confidence=assessment.confidence,
            )
        return assessment


@dataclass
class StaticLLMClient:
    responses: list[dict[str, Any]] = field(default_factory=list)

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if not self.responses:
            return {}
        return self.responses.pop(0)


def build_llm_brain(
    provider: str | None,
    *,
    model: str = "llama3.2",
    base_url: str = "http://127.0.0.1:11434",
    min_confidence: float = 0.55,
) -> LLMBrain | None:
    if provider is None:
        provider = "ollama"
    if provider == "none":
        return None
    if provider == "ollama":
        return LLMBrain(
            OllamaLLMClient(model=model, base_url=base_url),
            min_confidence=min_confidence,
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _llm_rule_from_dict(data: dict[str, Any]) -> LLMRuleProposal | None:
    rule_type = str(data.get("rule_type", "")).strip()
    if rule_type not in {
        "blocked_tool",
        "tool_requires_approval",
        "amount_requires_approval",
        "role_requirement",
        "blocked_content",
    }:
        return None
    description = str(data.get("description", "")).strip()
    if not description:
        return None
    action = str(data.get("action", "")).strip()
    if action not in {"stop_agent", "require_human_approval"}:
        action = "stop_agent" if rule_type in {"blocked_tool", "role_requirement", "blocked_content"} else "require_human_approval"
    try:
        severity = Severity(str(data.get("severity", "high")).lower())
    except ValueError:
        severity = Severity.HIGH
    allowed_roles = data.get("allowed_roles", ())
    if not isinstance(allowed_roles, list):
        allowed_roles = []
    max_amount = data.get("max_amount")
    try:
        max_amount = float(max_amount) if max_amount is not None else None
    except (TypeError, ValueError):
        max_amount = None
    return LLMRuleProposal(
        rule_type=rule_type,
        description=description,
        severity=severity,
        action=action,
        tool_name=_optional_str(data.get("tool_name")),
        max_amount=max_amount,
        allowed_roles=tuple(str(role).strip() for role in allowed_roles if str(role).strip()),
        pattern=_optional_str(data.get("pattern")),
    )


def _assessment_from_dict(data: dict[str, Any]) -> LLMRiskAssessment:
    verdict = str(data.get("verdict", "allow")).strip().lower()
    if verdict not in {"allow", "require_human_approval", "block"}:
        verdict = "allow"
    try:
        severity = Severity(str(data.get("severity", "low")).lower())
    except ValueError:
        severity = Severity.LOW
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return LLMRiskAssessment(
        verdict=verdict,
        severity=severity,
        reason=str(data.get("reason", "")).strip() or "No reason provided.",
        evidence=str(data.get("evidence", "")).strip() or "No evidence provided.",
        confidence=max(0.0, min(confidence, 1.0)),
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"LLM did not return JSON: {text[:200]}")
        data = json.loads(stripped[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("LLM JSON response must be an object.")
    return data


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _event_to_dict(event: AgentEvent) -> dict[str, Any]:
    return {
        "agent_id": event.agent_id,
        "event_type": event.event_type.value,
        "content": event.content,
        "task_id": event.task_id,
        "metadata": event.metadata,
    }


def _violation_to_dict(violation: PolicyViolation) -> dict[str, Any]:
    return {
        "rule_id": violation.rule_id,
        "description": violation.description,
        "severity": violation.severity.value,
        "action": violation.action,
        "evidence": violation.evidence,
    }


_RULE_EXTRACTION_SYSTEM_PROMPT = """
You are Sentient's policy extraction brain. Extract only explicit, monitorable policy rules from company text.
Return JSON only with this shape:
{"rules":[{"rule_type":"blocked_tool|tool_requires_approval|amount_requires_approval|role_requirement|blocked_content","description":"...","severity":"low|medium|high|critical","action":"stop_agent|require_human_approval","tool_name":"optional_tool_name","max_amount":100,"allowed_roles":["role"],"pattern":"optional_regex"}]}
Do not invent rules. If the text is vague, return {"rules":[]}.
""".strip()


_RISK_ASSESSMENT_SYSTEM_PROMPT = """
You are Sentient's risk assessment brain. Review an AI agent event using company context and deterministic violations.
You can only escalate risk; deterministic policy remains authoritative.
Return JSON only with this shape:
{"verdict":"allow|require_human_approval|block","severity":"low|medium|high|critical","confidence":0.0,"reason":"short reason","evidence":"specific evidence from event/context"}
Use require_human_approval for ambiguous but concerning behavior. Use block for clear policy bypass, data exfiltration, unsafe irreversible action, or malicious instruction-following.
""".strip()
