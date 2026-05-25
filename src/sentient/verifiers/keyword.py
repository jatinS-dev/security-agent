from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import VerificationResult


DEFAULT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "no",
        "not",
        "of",
        "on",
        "or",
        "package",
        "the",
        "to",
        "was",
        "were",
        "with",
    }
)


@dataclass(frozen=True)
class KeywordEvidenceVerifier:
    min_overlap_ratio: float = 0.5
    stopwords: frozenset[str] = field(default_factory=lambda: DEFAULT_STOPWORDS)

    def verify(self, claim: str, evidence_text: str) -> VerificationResult:
        claim_terms = _tokens(claim, self.stopwords)
        if not claim_terms:
            return VerificationResult(
                supported=True,
                reason="Claim has no meaningful terms to verify.",
            )

        evidence_terms = _tokens(evidence_text, self.stopwords)
        matched = tuple(sorted(claim_terms & evidence_terms))
        missing = tuple(sorted(claim_terms - evidence_terms))
        overlap_ratio = len(matched) / len(claim_terms)

        if overlap_ratio >= self.min_overlap_ratio:
            return VerificationResult(
                supported=True,
                reason=f"Evidence matched {overlap_ratio:.0%} of claim terms.",
                matched_terms=matched,
                missing_terms=missing,
            )

        return VerificationResult(
            supported=False,
            reason=f"Evidence matched only {overlap_ratio:.0%} of claim terms.",
            matched_terms=matched,
            missing_terms=missing,
        )


def _tokens(text: str, stopwords: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in re.findall(r"[a-zA-Z0-9_]+", text.lower())
        if len(token) > 2 and token not in stopwords
    )

