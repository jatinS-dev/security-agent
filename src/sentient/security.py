from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApiSecurityConfig:
    api_key: str | None = None
    hmac_secret: str | None = None
    rate_limit_per_minute: int | None = None
    max_body_bytes: int = 1_000_000
    api_key_store: Any | None = None


@dataclass
class InMemoryRateLimiter:
    limit_per_minute: int
    _hits: dict[str, list[float]] = field(default_factory=dict)

    def allow(self, identity: str, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        window_start = current - 60
        hits = [hit for hit in self._hits.get(identity, []) if hit >= window_start]
        if len(hits) >= self.limit_per_minute:
            self._hits[identity] = hits
            return False
        hits.append(current)
        self._hits[identity] = hits
        return True


def verify_api_key(expected: str | None, provided: str | None) -> bool:
    if expected is None:
        return True
    if provided is None:
        return False
    return hmac.compare_digest(expected, provided)


def sign_body(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str | None, body: bytes, signature: str | None) -> bool:
    if secret is None:
        return True
    if signature is None:
        return False
    expected = sign_body(secret, body)
    return hmac.compare_digest(expected, signature)
