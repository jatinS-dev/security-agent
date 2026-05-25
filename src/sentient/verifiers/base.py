from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class VerificationResult:
    supported: bool
    reason: str
    matched_terms: tuple[str, ...] = ()
    missing_terms: tuple[str, ...] = ()


class EvidenceVerifier(Protocol):
    def verify(self, claim: str, evidence_text: str) -> VerificationResult:
        raise NotImplementedError

