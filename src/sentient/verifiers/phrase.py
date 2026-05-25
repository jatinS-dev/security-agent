from __future__ import annotations

from dataclasses import dataclass

from .base import VerificationResult


@dataclass(frozen=True)
class PhraseEvidenceVerifier:
    """Requires the normalized claim phrase to appear in evidence text."""

    def verify(self, claim: str, evidence_text: str) -> VerificationResult:
        normalized_claim = _normalize(claim)
        normalized_evidence = _normalize(evidence_text)
        if normalized_claim and normalized_claim in normalized_evidence:
            return VerificationResult(
                supported=True,
                reason="Evidence contains the claim phrase.",
                matched_terms=(normalized_claim,),
            )
        return VerificationResult(
            supported=False,
            reason="Evidence does not contain the claim phrase.",
            missing_terms=(normalized_claim,),
        )


def _normalize(value: str) -> str:
    return " ".join(value.lower().split()).strip(" .")

