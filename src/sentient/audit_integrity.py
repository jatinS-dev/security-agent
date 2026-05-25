from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def hash_record(record: dict[str, Any], previous_hash: str) -> str:
    payload = {
        "previous_hash": previous_hash,
        "record": record,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_hash_chain(path: str | Path) -> tuple[bool, str]:
    audit_path = Path(path)
    if not audit_path.exists():
        return True, "empty"
    previous_hash = "GENESIS"
    with audit_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            envelope = json.loads(line)
            record = envelope.get("record")
            expected_previous = envelope.get("previous_hash")
            record_hash = envelope.get("record_hash")
            if expected_previous != previous_hash:
                return False, f"line {index}: previous hash mismatch"
            expected_hash = hash_record(record, previous_hash)
            if record_hash != expected_hash:
                return False, f"line {index}: record hash mismatch"
            previous_hash = record_hash
    return True, "ok"

