from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .agent_registry import AgentRegistry
from .llm import LLMBrain, LLMRuleProposal
from .models import AgentEvent, EventType, PolicyViolation, Severity
from .policy import (
    Policy,
    PolicyEngine,
    _extract_evidence_text,
    _extract_numeric_tool_arg,
)
from .verifiers import KeywordEvidenceVerifier, VerificationResult, VerifierRegistry


SUPPORTED_DOCUMENT_SUFFIXES = {".md", ".txt", ".json"}


@dataclass(frozen=True)
class ContextDocument:
    id: str
    path: str
    title: str
    format: str
    content_hash: str
    ingested_at: str


@dataclass(frozen=True)
class ContextChunk:
    id: str
    document_id: str
    path: str
    heading: str
    text: str
    ordinal: int


@dataclass(frozen=True)
class ContextNode:
    id: str
    type: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextEdge:
    id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextRule:
    id: str
    tenant_id: str
    rule_type: str
    status: str
    description: str
    severity: Severity
    action: str
    source_document_id: str
    source_chunk_id: str
    tool_name: str | None = None
    max_amount: float | None = None
    allowed_roles: tuple[str, ...] = ()
    pattern: str | None = None
    created_at: str = ""
    activated_at: str | None = None
    rejected_at: str | None = None
    reviewed_by: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextRule":
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            rule_type=data["rule_type"],
            status=data.get("status", "draft"),
            description=data["description"],
            severity=Severity(data.get("severity", Severity.HIGH.value)),
            action=data.get("action", "stop_agent"),
            source_document_id=data["source_document_id"],
            source_chunk_id=data["source_chunk_id"],
            tool_name=data.get("tool_name"),
            max_amount=data.get("max_amount"),
            allowed_roles=tuple(data.get("allowed_roles", ())),
            pattern=data.get("pattern"),
            created_at=data.get("created_at", ""),
            activated_at=data.get("activated_at"),
            rejected_at=data.get("rejected_at"),
            reviewed_by=data.get("reviewed_by"),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["allowed_roles"] = list(self.allowed_roles)
        return data


@dataclass(frozen=True)
class ContextMatch:
    chunk: ContextChunk
    document: ContextDocument | None
    score: int


@dataclass(frozen=True)
class IngestResult:
    documents_added: int
    chunks_added: int
    rules_proposed: int


class ContextGraph:
    """Local, inspectable graph store for one company's policy context."""

    def __init__(self, store: str | Path, tenant_id: str) -> None:
        self.store = Path(store)
        self.tenant_id = tenant_id

    @property
    def graph_path(self) -> Path:
        return self.store / self.tenant_id / "context_graph.json"

    def ingest_path(
        self,
        source: str | Path,
        *,
        llm_brain: LLMBrain | None = None,
    ) -> IngestResult:
        source_path = Path(source)
        files = _document_files(source_path)
        if not files:
            raise ValueError(f"No supported documents found in {source_path}")

        state = self._load_state()
        existing_paths = {document["path"] for document in state["documents"]}
        added_documents = 0
        added_chunks = 0
        added_rules = 0

        for file_path in files:
            chunks = _load_document_chunks(file_path)
            if not chunks:
                continue
            content = file_path.read_text(encoding="utf-8")
            content_hash = _sha256(content)
            document_id = _stable_id("doc", self.tenant_id, str(file_path.resolve()), content_hash)
            document = ContextDocument(
                id=document_id,
                path=str(file_path),
                title=_document_title(file_path, chunks),
                format=file_path.suffix.lower().lstrip("."),
                content_hash=content_hash,
                ingested_at=_utc_now(),
            )

            if document.path in existing_paths:
                state = _without_document(state, document.path)

            state["documents"].append(asdict(document))
            state["nodes"].append(
                asdict(
                    ContextNode(
                        id=document.id,
                        type="Document",
                        label=document.title,
                        properties={"path": document.path, "format": document.format},
                    )
                )
            )
            added_documents += 1

            for ordinal, chunk_data in enumerate(chunks):
                chunk_text = _normalize_whitespace(chunk_data["text"])
                if not chunk_text:
                    continue
                chunk_id = _stable_id("chunk", document.id, str(ordinal), chunk_text)
                chunk = ContextChunk(
                    id=chunk_id,
                    document_id=document.id,
                    path=document.path,
                    heading=chunk_data["heading"],
                    text=chunk_text,
                    ordinal=ordinal,
                )
                state["chunks"].append(asdict(chunk))
                state["nodes"].append(
                    asdict(
                        ContextNode(
                            id=chunk.id,
                            type="Section",
                            label=chunk.heading or document.title,
                            properties={"document_id": document.id, "ordinal": ordinal},
                        )
                    )
                )
                state["edges"].append(
                    asdict(
                        ContextEdge(
                            id=_stable_id("edge", document.id, chunk.id, "contains"),
                            source=document.id,
                            target=chunk.id,
                            type="contains",
                        )
                    )
                )
                added_chunks += 1
                added_rules += self._append_rule_proposals(
                    state,
                    document,
                    chunk,
                    llm_brain=llm_brain,
                )

        state["updated_at"] = _utc_now()
        self._save_state(state)
        return IngestResult(
            documents_added=added_documents,
            chunks_added=added_chunks,
            rules_proposed=added_rules,
        )

    def query(self, query: str, *, limit: int = 5) -> list[ContextMatch]:
        terms = _terms(query)
        if not terms:
            return []
        state = self._load_state()
        documents = {
            item["id"]: ContextDocument(**item)
            for item in state["documents"]
        }
        matches: list[ContextMatch] = []
        for chunk_data in state["chunks"]:
            chunk = ContextChunk(**chunk_data)
            haystack = f"{chunk.heading} {chunk.text}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                matches.append(
                    ContextMatch(
                        chunk=chunk,
                        document=documents.get(chunk.document_id),
                        score=score,
                    )
                )
        matches.sort(key=lambda match: (-match.score, match.chunk.path, match.chunk.ordinal))
        return matches[:limit]

    def propose_rules(self) -> list[ContextRule]:
        state = self._load_state()
        return [ContextRule.from_dict(rule) for rule in state["rules"] if rule.get("status") == "draft"]

    def rules(self, *, status: str | None = None) -> list[ContextRule]:
        state = self._load_state()
        rules = [ContextRule.from_dict(rule) for rule in state["rules"]]
        if status and status != "all":
            rules = [rule for rule in rules if rule.status == status]
        return rules

    def active_rules(self) -> list[ContextRule]:
        return self.rules(status="active")

    def activate_rule(self, rule_id: str, *, reviewed_by: str | None = None) -> ContextRule:
        return self._update_rule_status(rule_id, "active", reviewed_by=reviewed_by)

    def reject_rule(self, rule_id: str, *, reviewed_by: str | None = None) -> ContextRule:
        return self._update_rule_status(rule_id, "rejected", reviewed_by=reviewed_by)

    def to_dict(self) -> dict[str, Any]:
        return self._load_state()

    def source_for_rule(self, rule: ContextRule) -> tuple[ContextDocument | None, ContextChunk | None]:
        state = self._load_state()
        document = next(
            (ContextDocument(**item) for item in state["documents"] if item["id"] == rule.source_document_id),
            None,
        )
        chunk = next(
            (ContextChunk(**item) for item in state["chunks"] if item["id"] == rule.source_chunk_id),
            None,
        )
        return document, chunk

    def _append_rule_proposals(
        self,
        state: dict[str, Any],
        document: ContextDocument,
        chunk: ContextChunk,
        *,
        llm_brain: LLMBrain | None = None,
    ) -> int:
        existing_ids = {rule["id"] for rule in state["rules"]}
        added = 0
        proposals = list(_extract_rule_proposals(self.tenant_id, document, chunk))
        if llm_brain is not None:
            proposals.extend(_llm_rule_proposals(self.tenant_id, document, chunk, llm_brain))
        for proposal in proposals:
            if proposal.id in existing_ids:
                continue
            existing_ids.add(proposal.id)
            state["rules"].append(proposal.to_dict())
            added += 1
            state["nodes"].append(
                asdict(
                    ContextNode(
                        id=proposal.id,
                        type="PolicyRule",
                        label=proposal.description,
                        properties={
                            "status": proposal.status,
                            "rule_type": proposal.rule_type,
                            "tool_name": proposal.tool_name,
                        },
                    )
                )
            )
            state["edges"].append(
                asdict(
                    ContextEdge(
                        id=_stable_id("edge", proposal.id, chunk.id, "derived_from"),
                        source=proposal.id,
                        target=chunk.id,
                        type="derived_from",
                    )
                )
            )
            if proposal.tool_name:
                tool_id = _stable_id("tool", proposal.tool_name)
                state["nodes"].append(
                    asdict(
                        ContextNode(
                            id=tool_id,
                            type="Tool",
                            label=proposal.tool_name,
                        )
                    )
                )
                state["edges"].append(
                    asdict(
                        ContextEdge(
                            id=_stable_id("edge", proposal.id, tool_id, "applies_to"),
                            source=proposal.id,
                            target=tool_id,
                            type="applies_to",
                        )
                    )
                )
        return added

    def _update_rule_status(
        self,
        rule_id: str,
        status: str,
        *,
        reviewed_by: str | None,
    ) -> ContextRule:
        if status not in {"active", "rejected"}:
            raise ValueError(f"Unsupported rule status: {status}")
        state = self._load_state()
        reviewed_at_key = "activated_at" if status == "active" else "rejected_at"
        for rule in state["rules"]:
            if rule["id"] == rule_id:
                rule["status"] = status
                rule[reviewed_at_key] = _utc_now()
                rule["reviewed_by"] = reviewed_by
                self._save_state(state)
                return ContextRule.from_dict(rule)
        raise KeyError(f"Unknown context rule: {rule_id}")

    def _load_state(self) -> dict[str, Any]:
        if not self.graph_path.exists():
            return {
                "tenant_id": self.tenant_id,
                "documents": [],
                "chunks": [],
                "nodes": [],
                "edges": [],
                "rules": [],
                "updated_at": None,
            }
        with self.graph_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        state.setdefault("tenant_id", self.tenant_id)
        state.setdefault("documents", [])
        state.setdefault("chunks", [])
        state.setdefault("nodes", [])
        state.setdefault("edges", [])
        state.setdefault("rules", [])
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        state["tenant_id"] = self.tenant_id
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        with self.graph_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")


class ContextGraphEvidenceVerifier:
    def __init__(self, context_graph: ContextGraph, *, min_score: int = 2) -> None:
        self.context_graph = context_graph
        self.min_score = min_score
        self.keyword_verifier = KeywordEvidenceVerifier()

    def verify(self, claim: str, evidence_text: str = "") -> VerificationResult:
        if evidence_text:
            return self.keyword_verifier.verify(claim, evidence_text)
        matches = self.context_graph.query(claim, limit=3)
        if not matches:
            return VerificationResult(
                supported=False,
                reason="No matching company context found.",
                missing_terms=tuple(_terms(claim)),
            )
        evidence = "\n".join(match.chunk.text for match in matches)
        result = self.keyword_verifier.verify(claim, evidence)
        if result.supported or matches[0].score >= self.min_score:
            return VerificationResult(
                supported=True,
                reason=f"Supported by company context chunk {matches[0].chunk.id}.",
                matched_terms=result.matched_terms or tuple(_terms(claim)),
            )
        return result


@dataclass
class ContextAwarePolicyEngine(PolicyEngine):
    context_graph: ContextGraph | None = None
    llm_brain: LLMBrain | None = None

    def __init__(
        self,
        policy: Policy,
        *,
        context_graph: ContextGraph | None = None,
        llm_brain: LLMBrain | None = None,
        task_assignments: dict[str, str] | None = None,
        verifier_registry: VerifierRegistry | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        super().__init__(
            policy=policy,
            task_assignments=task_assignments or {},
            verifier_registry=verifier_registry,
            agent_registry=agent_registry,
        )
        self.context_graph = context_graph
        self.llm_brain = llm_brain

    def evaluate(self, event: AgentEvent) -> tuple[PolicyViolation, ...]:
        violations = list(super().evaluate(event))
        violations.extend(self._check_context_rules(event))
        violations.extend(self._check_llm_risk(event, tuple(violations)))
        return tuple(violations)

    def _check_evidence_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        if self.context_graph is None:
            return super()._check_evidence_rules(event)

        violations: list[PolicyViolation] = []
        claims = event.metadata.get("claims", [])
        sources = event.metadata.get("sources", [])
        artifacts = event.metadata.get("artifacts", [])
        evidence_text = _extract_evidence_text(event.metadata)

        if (
            self.policy.factual_claims_require_sources
            and event.event_type in {EventType.MESSAGE, EventType.RESULT}
            and claims
        ):
            if sources:
                violations.extend(super()._check_evidence_rules(event))
            else:
                for claim in claims:
                    matches = self.context_graph.query(str(claim), limit=3)
                    if not matches or matches[0].score < 2:
                        violations.append(
                            PolicyViolation(
                                rule_id="unsupported-claim",
                                description=(
                                    "Agent made factual claims without sources or company context support."
                                ),
                                severity=Severity.HIGH,
                                action="stop_agent",
                                evidence=str(claim),
                            )
                        )
                        continue
                    if evidence_text:
                        verifier = self.verifier_registry or VerifierRegistry(
                            KeywordEvidenceVerifier()
                        )
                        result = verifier.verify(str(claim), evidence_text)
                    else:
                        result = ContextGraphEvidenceVerifier(self.context_graph).verify(
                            str(claim),
                            "",
                        )
                    if not result.supported:
                        violations.append(
                            PolicyViolation(
                                rule_id="claim-not-supported-by-context",
                                description="Claim was not supported by company context graph.",
                                severity=Severity.HIGH,
                                action="stop_agent",
                                evidence=(
                                    f"claim={claim}; source={_format_match(matches[0])}; "
                                    f"reason={result.reason}"
                                ),
                            )
                        )
        else:
            violations.extend(super()._check_evidence_rules(event))

        completion_words = ("done", "completed", "fixed", "implemented", "finished")
        claims_completion = any(word in event.content.lower() for word in completion_words)
        if (
            self.policy.completion_requires_artifacts
            and event.event_type == EventType.RESULT
            and claims_completion
            and not artifacts
        ):
            if not any(violation.rule_id == "completion-without-artifacts" for violation in violations):
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

    def _check_context_rules(self, event: AgentEvent) -> list[PolicyViolation]:
        if self.context_graph is None:
            return []
        violations: list[PolicyViolation] = []
        for rule in self.context_graph.active_rules():
            if rule.rule_type == "blocked_tool":
                violation = self._blocked_tool_violation(event, rule)
            elif rule.rule_type == "tool_requires_approval":
                violation = self._approval_tool_violation(event, rule)
            elif rule.rule_type == "amount_requires_approval":
                violation = self._amount_violation(event, rule)
            elif rule.rule_type == "role_requirement":
                violation = self._role_violation(event, rule)
            elif rule.rule_type == "blocked_content":
                violation = self._blocked_content_violation(event, rule)
            else:
                violation = None
            if violation is not None:
                violations.append(violation)
        return violations

    def _blocked_tool_violation(
        self,
        event: AgentEvent,
        rule: ContextRule,
    ) -> PolicyViolation | None:
        if event.event_type != EventType.TOOL_CALL or _event_tool_name(event) != rule.tool_name:
            return None
        return self._context_violation(rule)

    def _approval_tool_violation(
        self,
        event: AgentEvent,
        rule: ContextRule,
    ) -> PolicyViolation | None:
        if event.event_type != EventType.TOOL_CALL or _event_tool_name(event) != rule.tool_name:
            return None
        return self._context_violation(rule)

    def _amount_violation(
        self,
        event: AgentEvent,
        rule: ContextRule,
    ) -> PolicyViolation | None:
        if event.event_type != EventType.TOOL_CALL or _event_tool_name(event) != rule.tool_name:
            return None
        amount = _extract_numeric_tool_arg(event, "amount")
        if amount is None or rule.max_amount is None or amount <= rule.max_amount:
            return None
        return self._context_violation(
            rule,
            evidence_prefix=f"amount={amount:g}, max={rule.max_amount:g}",
        )

    def _role_violation(
        self,
        event: AgentEvent,
        rule: ContextRule,
    ) -> PolicyViolation | None:
        if event.event_type != EventType.TOOL_CALL or _event_tool_name(event) != rule.tool_name:
            return None
        agent_role = self._agent_role(event)
        if agent_role in rule.allowed_roles:
            return None
        return self._context_violation(
            rule,
            evidence_prefix=f"role={agent_role or 'missing'}, allowed={list(rule.allowed_roles)}",
        )

    def _blocked_content_violation(
        self,
        event: AgentEvent,
        rule: ContextRule,
    ) -> PolicyViolation | None:
        if not rule.pattern:
            return None
        search_text = " ".join(
            [
                event.content,
                str(event.metadata.get("command", "")),
                str(event.metadata.get("tool_args", "")),
            ]
        )
        if not re.search(rule.pattern, search_text, re.IGNORECASE):
            return None
        return self._context_violation(rule, evidence_prefix=f"pattern={rule.pattern}")

    def _context_violation(
        self,
        rule: ContextRule,
        *,
        evidence_prefix: str | None = None,
    ) -> PolicyViolation:
        document, chunk = (
            self.context_graph.source_for_rule(rule)
            if self.context_graph is not None
            else (None, None)
        )
        source = _format_source(document, chunk)
        evidence = source
        if evidence_prefix:
            evidence = f"{evidence_prefix}; {source}"
        return PolicyViolation(
            rule_id=f"context:{rule.id}",
            description=f"{rule.description} Source: {source}.",
            severity=rule.severity,
            action=rule.action,
            evidence=evidence,
        )

    def _check_llm_risk(
        self,
        event: AgentEvent,
        deterministic_violations: tuple[PolicyViolation, ...],
    ) -> list[PolicyViolation]:
        if self.llm_brain is None:
            return []

        context_matches = []
        if self.context_graph is not None:
            query_text = " ".join(
                [
                    event.content,
                    str(event.metadata.get("tool_name", "")),
                    str(event.metadata.get("tool_args", "")),
                    " ".join(str(claim) for claim in event.metadata.get("claims", [])),
                ]
            )
            context_matches = [
                {
                    "score": match.score,
                    "document_path": match.document.path if match.document else match.chunk.path,
                    "heading": match.chunk.heading,
                    "text": match.chunk.text,
                }
                for match in self.context_graph.query(query_text, limit=3)
            ]
            active_rules = [rule.to_dict() for rule in self.context_graph.active_rules()]
        else:
            active_rules = []

        assessment = self.llm_brain.assess_event(
            event=event,
            deterministic_violations=deterministic_violations,
            context_matches=context_matches,
            active_rules=active_rules,
        )
        violation = assessment.to_violation()
        return [violation] if violation is not None else []


def _document_files(source_path: Path) -> list[Path]:
    if source_path.is_file():
        files = [source_path]
    else:
        files = [path for path in source_path.rglob("*") if path.is_file()]
    return sorted(path for path in files if path.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES)


def _load_document_chunks(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _markdown_chunks(path.read_text(encoding="utf-8"), path.stem)
    if suffix == ".txt":
        return _text_chunks(path.read_text(encoding="utf-8"), path.stem)
    if suffix == ".json":
        return _json_chunks(json.loads(path.read_text(encoding="utf-8")), path.stem)
    return []


def _markdown_chunks(text: str, fallback_heading: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    heading = fallback_heading
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if match:
            _flush_section(chunks, heading, lines)
            heading = match.group(1).strip()
            lines = []
        else:
            lines.append(line)
    _flush_section(chunks, heading, lines)
    return chunks or _text_chunks(text, fallback_heading)


def _text_chunks(text: str, fallback_heading: str) -> list[dict[str, str]]:
    paragraphs = [
        _normalize_whitespace(part)
        for part in re.split(r"\n\s*\n", text)
        if _normalize_whitespace(part)
    ]
    return [{"heading": fallback_heading, "text": paragraph} for paragraph in paragraphs]


def _json_chunks(data: Any, fallback_heading: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, (*path, str(key)))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)))
            return
        heading = ".".join(path) if path else fallback_heading
        chunks.append({"heading": heading, "text": f"{heading}: {value}"})

    walk(data, ())
    if not chunks:
        chunks.append({"heading": fallback_heading, "text": json.dumps(data, sort_keys=True)})
    return chunks


def _flush_section(chunks: list[dict[str, str]], heading: str, lines: list[str]) -> None:
    text = "\n".join(lines).strip()
    if not text:
        return
    for paragraph in _text_chunks(text, heading):
        chunks.append(paragraph)


def _document_title(path: Path, chunks: list[dict[str, str]]) -> str:
    if chunks and chunks[0]["heading"]:
        return chunks[0]["heading"]
    return path.stem.replace("_", " ").replace("-", " ").title()


def _extract_rule_proposals(
    tenant_id: str,
    document: ContextDocument,
    chunk: ContextChunk,
) -> Iterable[ContextRule]:
    text = chunk.text
    lower = text.lower()
    tool_name = _tool_name_from_text(lower)
    created_at = _utc_now()

    amount = _extract_amount_limit(lower)
    if tool_name == "issue_refund" and amount is not None and _mentions_approval(lower):
        yield _rule(
            tenant_id,
            "amount_requires_approval",
            f"Refunds above ${amount:g} require human approval.",
            "require_human_approval",
            Severity.MEDIUM,
            document,
            chunk,
            tool_name=tool_name,
            max_amount=amount,
            created_at=created_at,
        )

    if tool_name and _mentions_block(lower):
        yield _rule(
            tenant_id,
            "blocked_tool",
            f"Company policy blocks {tool_name}.",
            "stop_agent",
            Severity.CRITICAL,
            document,
            chunk,
            tool_name=tool_name,
            created_at=created_at,
        )

    if tool_name and amount is None and _mentions_approval(lower):
        yield _rule(
            tenant_id,
            "tool_requires_approval",
            f"Company policy requires approval for {tool_name}.",
            "require_human_approval",
            Severity.MEDIUM,
            document,
            chunk,
            tool_name=tool_name,
            created_at=created_at,
        )

    roles = _extract_roles(lower)
    if tool_name and roles:
        yield _rule(
            tenant_id,
            "role_requirement",
            f"Only {', '.join(roles)} may use {tool_name}.",
            "stop_agent",
            Severity.HIGH,
            document,
            chunk,
            tool_name=tool_name,
            allowed_roles=roles,
            created_at=created_at,
        )

    blocked_pattern = _blocked_content_pattern(lower)
    if blocked_pattern:
        yield _rule(
            tenant_id,
            "blocked_content",
            "Company policy blocks disclosure of protected data.",
            "stop_agent",
            Severity.HIGH,
            document,
            chunk,
            pattern=blocked_pattern,
            created_at=created_at,
        )


def _llm_rule_proposals(
    tenant_id: str,
    document: ContextDocument,
    chunk: ContextChunk,
    llm_brain: LLMBrain,
) -> Iterable[ContextRule]:
    proposals = llm_brain.extract_rules_from_text(
        tenant_id=tenant_id,
        document_title=document.title,
        chunk_heading=chunk.heading,
        text=chunk.text,
    )
    created_at = _utc_now()
    for proposal in proposals:
        yield _rule_from_llm_proposal(
            tenant_id,
            document,
            chunk,
            proposal,
            created_at=created_at,
        )


def _rule_from_llm_proposal(
    tenant_id: str,
    document: ContextDocument,
    chunk: ContextChunk,
    proposal: LLMRuleProposal,
    *,
    created_at: str,
) -> ContextRule:
    rule_id = _stable_id(
        "ctxrule",
        tenant_id,
        "llm",
        proposal.rule_type,
        proposal.description,
        proposal.tool_name or "",
        str(proposal.max_amount or ""),
        ",".join(proposal.allowed_roles),
        proposal.pattern or "",
        chunk.id,
    )
    return ContextRule(
        id=rule_id,
        tenant_id=tenant_id,
        rule_type=proposal.rule_type,
        status="draft",
        description=proposal.description,
        severity=proposal.severity,
        action=proposal.action,
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        tool_name=proposal.tool_name,
        max_amount=proposal.max_amount,
        allowed_roles=proposal.allowed_roles,
        pattern=proposal.pattern,
        created_at=created_at,
    )


def _rule(
    tenant_id: str,
    rule_type: str,
    description: str,
    action: str,
    severity: Severity,
    document: ContextDocument,
    chunk: ContextChunk,
    *,
    tool_name: str | None = None,
    max_amount: float | None = None,
    allowed_roles: tuple[str, ...] = (),
    pattern: str | None = None,
    created_at: str,
) -> ContextRule:
    rule_id = _stable_id(
        "ctxrule",
        tenant_id,
        rule_type,
        description,
        tool_name or "",
        str(max_amount or ""),
        ",".join(allowed_roles),
        pattern or "",
        chunk.id,
    )
    return ContextRule(
        id=rule_id,
        tenant_id=tenant_id,
        rule_type=rule_type,
        status="draft",
        description=description,
        severity=severity,
        action=action,
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        tool_name=tool_name,
        max_amount=max_amount,
        allowed_roles=allowed_roles,
        pattern=pattern,
        created_at=created_at,
    )


def _tool_name_from_text(lower: str) -> str | None:
    mappings = (
        ("export_customer_database", ("export customer database", "customer database export", "export the customer database")),
        ("deploy_production", ("deploy production", "production deploy", "deploy to production")),
        ("issue_refund", ("refund", "payment adjustment", "customer credit")),
        ("send_email", ("send email", "email customer", "customer email")),
    )
    for tool_name, phrases in mappings:
        if any(phrase in lower for phrase in phrases):
            return tool_name
    return None


def _extract_amount_limit(lower: str) -> float | None:
    patterns = (
        r"(?:refunds?|payment adjustments?|customer credits?).{0,60}(?:above|over|exceed(?:s|ing)?|greater than|more than)\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)",
        r"\$?([0-9][0-9,]*(?:\.[0-9]+)?).{0,60}(?:refund|payment adjustment|customer credit).{0,60}(?:approval|approved|review)",
    )
    for pattern in patterns:
        match = re.search(pattern, lower, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _mentions_approval(lower: str) -> bool:
    return any(word in lower for word in ("approval", "approved", "reviewed", "human review", "manager review"))


def _mentions_block(lower: str) -> bool:
    return any(
        phrase in lower
        for phrase in (
            "must not",
            "not allowed",
            "forbidden",
            "prohibited",
            "never",
            "cannot",
            "blocked",
        )
    )


def _extract_roles(lower: str) -> tuple[str, ...]:
    if "only" not in lower and "must be" not in lower:
        return ()
    roles = []
    role_map = {
        "finance manager": "finance_manager",
        "support manager": "support_manager",
        "security reviewer": "security_reviewer",
        "admin": "admin",
        "administrator": "admin",
    }
    for phrase, role in role_map.items():
        if phrase in lower:
            roles.append(role)
    return tuple(dict.fromkeys(roles))


def _blocked_content_pattern(lower: str) -> str | None:
    protected_terms = []
    if "payment card" in lower or "card number" in lower:
        protected_terms.append(r"(?:payment card|card number|4[0-9]{12}(?:[0-9]{3})?)")
    if "api key" in lower or "secret" in lower:
        protected_terms.append(r"(?:api key|secret|sk-[A-Za-z0-9_-]+)")
    if "customer database" in lower and _mentions_block(lower):
        protected_terms.append(r"customer database")
    if not protected_terms:
        return None
    return "|".join(protected_terms)


def _event_tool_name(event: AgentEvent) -> str:
    return str(event.metadata.get("tool_name", "")).strip()


def _without_document(state: dict[str, Any], document_path: str) -> dict[str, Any]:
    document_ids = {
        document["id"]
        for document in state["documents"]
        if document["path"] == document_path
    }
    chunk_ids = {
        chunk["id"]
        for chunk in state["chunks"]
        if chunk["document_id"] in document_ids
    }
    rule_ids = {
        rule["id"]
        for rule in state["rules"]
        if rule["source_document_id"] in document_ids
    }
    state["documents"] = [
        document
        for document in state["documents"]
        if document["id"] not in document_ids
    ]
    state["chunks"] = [
        chunk
        for chunk in state["chunks"]
        if chunk["id"] not in chunk_ids
    ]
    state["rules"] = [
        rule
        for rule in state["rules"]
        if rule["source_document_id"] not in document_ids
    ]
    removed_ids = document_ids | chunk_ids | rule_ids
    state["nodes"] = [
        node
        for node in state["nodes"]
        if node["id"] not in removed_ids
    ]
    state["edges"] = [
        edge
        for edge in state["edges"]
        if edge["source"] not in removed_ids and edge["target"] not in removed_ids
    ]
    return state


def _format_match(match: ContextMatch) -> str:
    return _format_source(match.document, match.chunk)


def _format_source(
    document: ContextDocument | None,
    chunk: ContextChunk | None,
) -> str:
    if document is None and chunk is None:
        return "unknown source"
    path = document.path if document else chunk.path if chunk else ""
    heading = chunk.heading if chunk else ""
    chunk_id = chunk.id if chunk else ""
    return f"{path}#{heading} ({chunk_id})"


def _terms(text: str) -> tuple[str, ...]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "has",
        "are",
        "was",
        "were",
        "not",
        "but",
        "before",
        "after",
        "customer",
        "agent",
    }
    return tuple(
        term
        for term in re.findall(r"[a-zA-Z0-9_]+", text.lower())
        if len(term) > 2 and term not in stopwords
    )


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
