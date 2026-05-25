from __future__ import annotations

import unittest

from sentient import (
    AgentEvent,
    DecisionType,
    EventType,
    InMemoryAgentController,
    KeywordEvidenceVerifier,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    VerifierRegistry,
)


class VerifierTests(unittest.TestCase):
    def test_keyword_verifier_supports_matching_evidence(self) -> None:
        verifier = KeywordEvidenceVerifier(min_overlap_ratio=0.5)

        result = verifier.verify(
            "Dependency has no critical vulnerabilities",
            "Security scan: dependency has zero critical vulnerabilities.",
        )

        self.assertTrue(result.supported)
        self.assertIn("critical", result.matched_terms)
        self.assertIn("vulnerabilities", result.matched_terms)

    def test_keyword_verifier_rejects_unrelated_evidence(self) -> None:
        verifier = KeywordEvidenceVerifier(min_overlap_ratio=0.5)

        result = verifier.verify(
            "Dependency has no critical vulnerabilities",
            "The documentation was updated yesterday.",
        )

        self.assertFalse(result.supported)
        self.assertIn("critical", result.missing_terms)
        self.assertIn("vulnerabilities", result.missing_terms)

    def test_supervisor_allows_supported_claim_with_evidence_text(self) -> None:
        supervisor = _build_supervisor_with_verifier()

        decision = supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="Dependency has no critical vulnerabilities.",
                metadata={
                    "claims": ["Dependency has no critical vulnerabilities."],
                    "sources": ["scan.json"],
                    "evidence_text": "Scan result: dependency has zero critical vulnerabilities.",
                    "artifacts": ["scan.json"],
                },
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.decision_type, DecisionType.ALLOW)

    def test_supervisor_blocks_claim_with_unrelated_evidence_text(self) -> None:
        supervisor = _build_supervisor_with_verifier()

        decision = supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="Dependency has no critical vulnerabilities.",
                metadata={
                    "claims": ["Dependency has no critical vulnerabilities."],
                    "sources": ["notes.md"],
                    "evidence_text": "The README was updated yesterday.",
                    "artifacts": ["notes.md"],
                },
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision_type, DecisionType.BLOCK)
        self.assertEqual(
            decision.violations[0].rule_id,
            "claim-not-supported-by-evidence",
        )

    def test_supervisor_blocks_sources_without_evidence_text(self) -> None:
        supervisor = _build_supervisor_with_verifier()

        decision = supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="Dependency has no critical vulnerabilities.",
                metadata={
                    "claims": ["Dependency has no critical vulnerabilities."],
                    "sources": ["scan.json"],
                    "artifacts": ["scan.json"],
                },
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.violations[0].rule_id, "missing-evidence-text")


def _build_supervisor_with_verifier() -> SecuritySupervisor:
    policy = Policy.from_dict(
        {
            "name": "verifier policy",
            "version": "test",
            "completion_requires_artifacts": True,
            "factual_claims_require_sources": True,
        }
    )
    controller = InMemoryAgentController()
    controller.register("agent-1")
    return SecuritySupervisor(
        policy_engine=PolicyEngine(
            policy,
            verifier_registry=VerifierRegistry(
                KeywordEvidenceVerifier(min_overlap_ratio=0.5)
            ),
        ),
        controller=controller,
    )


if __name__ == "__main__":
    unittest.main()

