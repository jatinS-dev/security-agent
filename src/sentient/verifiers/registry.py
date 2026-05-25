from __future__ import annotations

from dataclasses import dataclass, field

from .base import EvidenceVerifier, VerificationResult
from .keyword import KeywordEvidenceVerifier


@dataclass
class VerifierRegistry:
    default_verifier: EvidenceVerifier = field(default_factory=KeywordEvidenceVerifier)

    def verify(self, claim: str, evidence_text: str) -> VerificationResult:
        return self.default_verifier.verify(claim, evidence_text)

