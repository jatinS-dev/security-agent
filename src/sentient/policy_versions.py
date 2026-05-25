from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyVersionRecord:
    policy_id: str
    version: str
    path: str
    created_at: str
    author: str | None = None
    test_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ActivePolicyRecord:
    policy_id: str
    version: str
    path: str
    activated_at: str
    activated_by: str | None = None
    previous_version: str | None = None
    previous_path: str | None = None


class FilePolicyVersionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.active_path = self.path.with_suffix(self.path.suffix + ".active.json")

    def publish(self, record: PolicyVersionRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True))
            handle.write("\n")

    def list(self, policy_id: str | None = None) -> list[PolicyVersionRecord]:
        if not self.path.exists():
            return []
        records: list[PolicyVersionRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)
                if policy_id is None or data["policy_id"] == policy_id:
                    records.append(PolicyVersionRecord(**data))
        return records

    def latest(self, policy_id: str) -> PolicyVersionRecord | None:
        records = self.list(policy_id)
        return records[-1] if records else None

    def activate(
        self,
        policy_id: str,
        version: str,
        *,
        activated_by: str | None = None,
    ) -> ActivePolicyRecord:
        target = self._find(policy_id, version)
        if target is None:
            raise KeyError(f"Unknown policy version: {policy_id}@{version}")

        previous = self.active(policy_id)
        active = ActivePolicyRecord(
            policy_id=target.policy_id,
            version=target.version,
            path=target.path,
            activated_at=datetime.now(timezone.utc).isoformat(),
            activated_by=activated_by,
            previous_version=previous.version if previous else None,
            previous_path=previous.path if previous else None,
        )
        state = self._load_active_state()
        state[policy_id] = asdict(active)
        self._write_active_state(state)
        return active

    def active(self, policy_id: str) -> ActivePolicyRecord | None:
        data = self._load_active_state().get(policy_id)
        return ActivePolicyRecord(**data) if data else None

    def rollback(
        self,
        policy_id: str,
        *,
        activated_by: str | None = None,
    ) -> ActivePolicyRecord:
        current = self.active(policy_id)
        if current is None:
            raise ValueError(f"No active policy for {policy_id}")
        if current.previous_version is None:
            raise ValueError(f"No previous policy version for {policy_id}")
        return self.activate(
            policy_id,
            current.previous_version,
            activated_by=activated_by,
        )

    def _find(self, policy_id: str, version: str) -> PolicyVersionRecord | None:
        for record in self.list(policy_id):
            if record.version == version:
                return record
        return None

    def _load_active_state(self) -> dict[str, dict[str, Any]]:
        if not self.active_path.exists():
            return {}
        with self.active_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid active policy state: {self.active_path}")
        return data

    def _write_active_state(self, state: dict[str, dict[str, Any]]) -> None:
        self.active_path.parent.mkdir(parents=True, exist_ok=True)
        with self.active_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")


def compare_policy_files(left_path: str | Path, right_path: str | Path) -> dict[str, Any]:
    left = _load_policy(left_path)
    right = _load_policy(right_path)
    left_keys = set(left)
    right_keys = set(right)
    changed = {
        key: {"left": left[key], "right": right[key]}
        for key in sorted(left_keys & right_keys)
        if left[key] != right[key]
    }
    return {
        "left": str(left_path),
        "right": str(right_path),
        "added": sorted(right_keys - left_keys),
        "removed": sorted(left_keys - right_keys),
        "changed": changed,
    }


def _load_policy(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Policy must be a JSON object: {path}")
    return data
