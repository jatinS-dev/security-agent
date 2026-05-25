from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    key_hash: str
    tenant_id: str
    scopes: tuple[str, ...]
    created_at: str
    name: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None


class FileApiKeyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def issue(
        self,
        *,
        tenant_id: str,
        scopes: tuple[str, ...] = ("*",),
        name: str | None = None,
        expires_at: str | None = None,
    ) -> tuple[str, ApiKeyRecord]:
        raw_key = f"sentient_sk_{secrets.token_urlsafe(32)}"
        record = ApiKeyRecord(
            key_id=secrets.token_hex(8),
            key_hash=hash_api_key(raw_key),
            tenant_id=tenant_id,
            scopes=scopes,
            created_at=_utc_now(),
            name=name,
            expires_at=expires_at,
        )
        self._write_records([*self.list(), record])
        return raw_key, record

    def list(self, tenant_id: str | None = None) -> list[ApiKeyRecord]:
        if not self.path.exists():
            return []
        records: list[ApiKeyRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)
                data["scopes"] = tuple(data.get("scopes", ()))
                record = ApiKeyRecord(**data)
                if tenant_id is None or record.tenant_id == tenant_id:
                    records.append(record)
        return records

    def revoke(self, key_id: str) -> ApiKeyRecord:
        records = self.list()
        updated_records: list[ApiKeyRecord] = []
        revoked: ApiKeyRecord | None = None
        for record in records:
            if record.key_id == key_id:
                revoked = ApiKeyRecord(
                    **{
                        **asdict(record),
                        "scopes": record.scopes,
                        "revoked_at": _utc_now(),
                    }
                )
                updated_records.append(revoked)
            else:
                updated_records.append(record)
        if revoked is None:
            raise KeyError(f"Unknown API key: {key_id}")
        self._write_records(updated_records)
        return revoked

    def authenticate(
        self,
        raw_key: str | None,
        *,
        tenant_id: str | None = None,
        required_scope: str | None = None,
        now: datetime | None = None,
    ) -> ApiKeyRecord | None:
        if not raw_key:
            return None
        key_hash = hash_api_key(raw_key)
        for record in self.list(tenant_id):
            if not hmac.compare_digest(record.key_hash, key_hash):
                continue
            if record.revoked_at is not None:
                return None
            if record.expires_at and _parse_time(record.expires_at) <= (now or datetime.now(timezone.utc)):
                return None
            if required_scope and "*" not in record.scopes and required_scope not in record.scopes:
                return None
            return record
        return None

    def _write_records(self, records: list[ApiKeyRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for record in records:
                data = asdict(record)
                data["scopes"] = list(record.scopes)
                handle.write(json.dumps(data, sort_keys=True))
                handle.write("\n")


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
