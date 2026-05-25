from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Protocol

from .models import (
    AgentEvent,
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    PolicyViolation,
)
from .audit_integrity import hash_record
from .redaction import redact_sensitive_data


class AuditStore(Protocol):
    def append_decision(self, event: AgentEvent, decision: Decision, timestamp: str) -> None:
        raise NotImplementedError


class ApprovalStore(Protocol):
    def create(self, approval_request: ApprovalRequest) -> None:
        raise NotImplementedError

    def get(self, request_id: str) -> ApprovalRequest | None:
        raise NotImplementedError

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        raise NotImplementedError

    def update(
        self,
        request_id: str,
        *,
        status: ApprovalStatus,
        reviewed_at: str | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> ApprovalRequest:
        raise NotImplementedError


class FileAuditStore:
    def __init__(self, path: str | Path, *, redact: bool = True) -> None:
        self.path = Path(path)
        self.redact = redact

    def append_decision(self, event: AgentEvent, decision: Decision, timestamp: str) -> None:
        _append_jsonl(
            self.path,
            {
                "timestamp": timestamp,
                "event": _event_to_dict(event, redact=self.redact),
                "decision": _decision_to_dict(decision, redact=self.redact),
            },
        )


class HashChainedAuditStore:
    def __init__(self, path: str | Path, *, redact: bool = True) -> None:
        self.path = Path(path)
        self.redact = redact

    def append_decision(self, event: AgentEvent, decision: Decision, timestamp: str) -> None:
        record = {
            "timestamp": timestamp,
            "event": _event_to_dict(event, redact=self.redact),
            "decision": _decision_to_dict(decision, redact=self.redact),
        }
        previous_hash = self._last_hash()
        record_hash = hash_record(record, previous_hash)
        _append_jsonl(
            self.path,
            {
                "previous_hash": previous_hash,
                "record_hash": record_hash,
                "record": record,
            },
        )

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "GENESIS"
        last_hash = "GENESIS"
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_hash = json.loads(line)["record_hash"]
        return last_hash


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    def create(self, approval_request: ApprovalRequest) -> None:
        self._requests[approval_request.request_id] = approval_request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        requests = list(self._requests.values())
        if status is not None:
            requests = [request for request in requests if request.status == status]
        return requests

    def update(
        self,
        request_id: str,
        *,
        status: ApprovalStatus,
        reviewed_at: str | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> ApprovalRequest:
        approval_request = self._requests.get(request_id)
        if approval_request is None:
            raise KeyError(f"Unknown approval request: {request_id}")

        updated = replace(
            approval_request,
            status=status,
            reviewed_at=reviewed_at or approval_request.reviewed_at,
            reviewer=reviewer or approval_request.reviewer,
            review_reason=review_reason or approval_request.review_reason,
        )
        self._requests[request_id] = updated
        return updated


class FileApprovalStore(InMemoryApprovalStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__()
        self._load()

    def create(self, approval_request: ApprovalRequest) -> None:
        super().create(approval_request)
        _append_jsonl(
            self.path,
            {
                "event": "created",
                "approval_request": _approval_to_dict(approval_request),
            },
        )

    def update(
        self,
        request_id: str,
        *,
        status: ApprovalStatus,
        reviewed_at: str | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> ApprovalRequest:
        updated = super().update(
            request_id,
            status=status,
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_reason=review_reason,
        )
        _append_jsonl(
            self.path,
            {
                "event": "updated",
                "approval_request": _approval_to_dict(updated),
            },
        )
        return updated

    def _load(self) -> None:
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                data = record.get("approval_request", record)
                approval_request = _approval_from_dict(data)
                self._requests[approval_request.request_id] = approval_request


class SQLiteAuditStore:
    def __init__(self, path: str | Path, *, redact: bool = True) -> None:
        self.path = Path(path)
        self.redact = redact
        self._init_db()

    def append_decision(self, event: AgentEvent, decision: Decision, timestamp: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO audit_records (timestamp, agent_id, event_json, decision_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    timestamp,
                    event.agent_id,
                    json.dumps(
                        _event_to_dict(event, redact=self.redact),
                        default=_json_default,
                    ),
                    json.dumps(
                        _decision_to_dict(decision, redact=self.redact),
                        default=_json_default,
                    ),
                ),
            )
            connection.commit()

    def list_records(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT timestamp, event_json, decision_json FROM audit_records ORDER BY id"
            ).fetchall()
        return [
            {
                "timestamp": row[0],
                "event": json.loads(row[1]),
                "decision": json.loads(row[2]),
            }
            for row in rows
        ]

    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class SQLiteApprovalStore(InMemoryApprovalStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__()
        self._init_db()
        self._load()

    def create(self, approval_request: ApprovalRequest) -> None:
        super().create(approval_request)
        self._upsert(approval_request)

    def update(
        self,
        request_id: str,
        *,
        status: ApprovalStatus,
        reviewed_at: str | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> ApprovalRequest:
        updated = super().update(
            request_id,
            status=status,
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_reason=review_reason,
        )
        self._upsert(updated)
        return updated

    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    approval_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _load(self) -> None:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT approval_json FROM approval_requests"
            ).fetchall()
        for row in rows:
            approval_request = _approval_from_dict(json.loads(row[0]))
            self._requests[approval_request.request_id] = approval_request

    def _upsert(self, approval_request: ApprovalRequest) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO approval_requests (request_id, approval_json)
                VALUES (?, ?)
                ON CONFLICT(request_id) DO UPDATE SET approval_json = excluded.approval_json
                """,
                (
                    approval_request.request_id,
                    json.dumps(_approval_to_dict(approval_request), default=_json_default),
                ),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class PostgresAuditStore:
    def __init__(self, dsn: str, *, redact: bool = True) -> None:
        self.dsn = dsn
        self.redact = redact
        self._init_db()

    def append_decision(self, event: AgentEvent, decision: Decision, timestamp: str) -> None:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_records (timestamp, agent_id, event_json, decision_json)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        timestamp,
                        event.agent_id,
                        json.dumps(
                            _event_to_dict(event, redact=self.redact),
                            default=_json_default,
                        ),
                        json.dumps(
                            _decision_to_dict(decision, redact=self.redact),
                            default=_json_default,
                        ),
                    ),
                )
            connection.commit()

    def list_records(self) -> list[dict[str, Any]]:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT timestamp, event_json, decision_json FROM audit_records ORDER BY id"
                )
                rows = cursor.fetchall()
        return [
            {
                "timestamp": str(row[0]),
                "event": json.loads(row[1]) if isinstance(row[1], str) else row[1],
                "decision": json.loads(row[2]) if isinstance(row[2], str) else row[2],
            }
            for row in rows
        ]

    def _init_db(self) -> None:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_records (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        event_json JSONB NOT NULL,
                        decision_json JSONB NOT NULL
                    )
                    """
                )
            connection.commit()


class PostgresApprovalStore(InMemoryApprovalStore):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        super().__init__()
        self._init_db()
        self._load()

    def create(self, approval_request: ApprovalRequest) -> None:
        super().create(approval_request)
        self._upsert(approval_request)

    def update(
        self,
        request_id: str,
        *,
        status: ApprovalStatus,
        reviewed_at: str | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> ApprovalRequest:
        updated = super().update(
            request_id,
            status=status,
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_reason=review_reason,
        )
        self._upsert(updated)
        return updated

    def _init_db(self) -> None:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS approval_requests (
                        request_id TEXT PRIMARY KEY,
                        approval_json JSONB NOT NULL
                    )
                    """
                )
            connection.commit()

    def _load(self) -> None:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT approval_json FROM approval_requests")
                rows = cursor.fetchall()
        for row in rows:
            data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            approval_request = _approval_from_dict(data)
            self._requests[approval_request.request_id] = approval_request

    def _upsert(self, approval_request: ApprovalRequest) -> None:
        with closing(_connect_postgres(self.dsn)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO approval_requests (request_id, approval_json)
                    VALUES (%s, %s)
                    ON CONFLICT(request_id) DO UPDATE
                    SET approval_json = excluded.approval_json
                    """,
                    (
                        approval_request.request_id,
                        json.dumps(_approval_to_dict(approval_request), default=_json_default),
                    ),
                )
            connection.commit()


def _connect_postgres(dsn: str):
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "Postgres stores require the optional 'psycopg' package. "
            "Install it with: python3 -m pip install psycopg[binary]"
        ) from error
    return psycopg.connect(dsn)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=_json_default, sort_keys=True))
        handle.write("\n")


def _json_default(value: Any) -> str:
    return repr(value)


def _event_to_dict(event: AgentEvent, *, redact: bool = False) -> dict[str, Any]:
    data = {
        "agent_id": event.agent_id,
        "event_type": event.event_type.value,
        "content": event.content,
        "task_id": event.task_id,
        "metadata": event.metadata,
    }
    return redact_sensitive_data(data) if redact else data


def _decision_to_dict(decision: Decision, *, redact: bool = False) -> dict[str, Any]:
    data = {
        "agent_id": decision.agent_id,
        "allowed": decision.allowed,
        "decision_type": decision.decision_type.value,
        "enforcement_mode": decision.enforcement_mode.value,
        "enforced": decision.enforced,
        "summary": decision.summary,
        "violations": [
            _violation_to_dict(violation)
            for violation in decision.violations
        ],
    }
    return redact_sensitive_data(data) if redact else data


def _violation_to_dict(violation: PolicyViolation) -> dict[str, Any]:
    return {
        "rule_id": violation.rule_id,
        "description": violation.description,
        "severity": violation.severity.value,
        "action": violation.action,
        "evidence": violation.evidence,
    }


def _approval_to_dict(approval_request: ApprovalRequest) -> dict[str, Any]:
    data = asdict(approval_request)
    data["status"] = approval_request.status.value
    data["assigned_reviewers"] = list(approval_request.assigned_reviewers)
    return data


def _approval_from_dict(data: dict[str, Any]) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=data["request_id"],
        agent_id=data["agent_id"],
        tool_name=data["tool_name"],
        decision_summary=data["decision_summary"],
        status=ApprovalStatus(data["status"]),
        created_at=data["created_at"],
        task_id=data.get("task_id"),
        tool_args=data.get("tool_args", {}),
        metadata=data.get("metadata", {}),
        assigned_reviewers=tuple(data.get("assigned_reviewers", ())),
        expires_at=data.get("expires_at"),
        reviewed_at=data.get("reviewed_at"),
        reviewer=data.get("reviewer"),
        review_reason=data.get("review_reason"),
    )
