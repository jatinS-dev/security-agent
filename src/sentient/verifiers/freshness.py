from __future__ import annotations

from datetime import datetime, timezone

from .base import VerificationResult


def verify_source_freshness(
    source_timestamp: str,
    *,
    max_age_days: int,
    now: datetime | None = None,
) -> VerificationResult:
    current = now or datetime.now(timezone.utc)
    source_time = datetime.fromisoformat(source_timestamp)
    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=timezone.utc)
    age_days = (current - source_time).days
    if age_days <= max_age_days:
        return VerificationResult(
            supported=True,
            reason=f"Source is {age_days} days old.",
        )
    return VerificationResult(
        supported=False,
        reason=f"Source is {age_days} days old; max is {max_age_days}.",
    )

