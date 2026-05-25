from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sentient import (
    AgentEvent,
    ContextAwarePolicyEngine,
    ContextGraph,
    EventType,
    FileAuditStore,
    InMemoryAgentController,
    LLMBrain,
    Policy,
    SecuritySupervisor,
    StaticLLMClient,
)
from sentient.cli import main as cli_main


class ContextGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        (self.docs / "refund_policy.md").write_text(
            "\n".join(
                [
                    "# Refund Policy",
                    "",
                    "Refunds above $100 require manager approval before execution.",
                    "",
                    "Only support managers may issue refunds for duplicate subscription charges.",
                    "",
                    "Duplicate subscription charges can be refunded after invoice and payment processor verification.",
                ]
            ),
            encoding="utf-8",
        )
        (self.docs / "data_policy.md").write_text(
            "\n".join(
                [
                    "# Data Policy",
                    "",
                    "Agents must not export customer database records.",
                    "",
                    "Payment card numbers and API keys must not be disclosed in customer messages.",
                ]
            ),
            encoding="utf-8",
        )
        (self.docs / "security.json").write_text(
            json.dumps(
                {
                    "production": {
                        "deploy": "Production deploys require approval from an admin before execution."
                    }
                }
            ),
            encoding="utf-8",
        )
        self.graph = ContextGraph(self.root / "context", "acme")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ingest_creates_graph_and_draft_rules(self) -> None:
        result = self.graph.ingest_path(self.docs)

        self.assertEqual(result.documents_added, 3)
        self.assertGreaterEqual(result.chunks_added, 5)
        self.assertGreaterEqual(result.rules_proposed, 4)
        graph_data = self.graph.to_dict()
        self.assertGreaterEqual(len(graph_data["nodes"]), result.documents_added)
        self.assertGreaterEqual(len(graph_data["edges"]), result.chunks_added)
        self.assertTrue(any(rule.rule_type == "amount_requires_approval" for rule in self.graph.propose_rules()))

    def test_query_returns_source_chunks(self) -> None:
        self.graph.ingest_path(self.docs)

        matches = self.graph.query("refund approval limit")

        self.assertTrue(matches)
        self.assertIn("Refund", matches[0].chunk.text)
        self.assertEqual(matches[0].document.title, "Refund Policy")

    def test_draft_rules_do_not_affect_policy_decisions(self) -> None:
        self.graph.ingest_path(self.docs)
        engine = ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph)

        violations = engine.evaluate(self._refund_event(950, role="support_manager"))

        self.assertEqual(violations, ())

    def test_active_amount_rule_requires_approval(self) -> None:
        self.graph.ingest_path(self.docs)
        self._activate("amount_requires_approval")
        engine = ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph)

        violations = engine.evaluate(self._refund_event(950, role="support_manager"))

        self.assertEqual(violations[0].action, "require_human_approval")
        self.assertIn("Source:", violations[0].description)
        self.assertIn("refund_policy.md", violations[0].evidence)

    def test_active_blocked_tool_rule_blocks(self) -> None:
        self.graph.ingest_path(self.docs)
        self._activate("blocked_tool")
        engine = ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph)

        violations = engine.evaluate(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="Export customer records",
                metadata={"tool_name": "export_customer_database"},
            )
        )

        self.assertEqual(violations[0].action, "stop_agent")
        self.assertEqual(violations[0].severity.value, "critical")

    def test_active_role_rule_blocks_wrong_role(self) -> None:
        self.graph.ingest_path(self.docs)
        self._activate("role_requirement")
        engine = ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph)

        violations = engine.evaluate(self._refund_event(50, role="support_agent"))

        self.assertEqual(violations[0].action, "stop_agent")
        self.assertIn("role=support_agent", violations[0].evidence)

    def test_context_supports_claims_and_blocks_unsupported_claims(self) -> None:
        self.graph.ingest_path(self.docs)
        engine = ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph)

        supported = engine.evaluate(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="Duplicate charges can be refunded after verification.",
                metadata={
                    "claims": [
                        "Duplicate subscription charges can be refunded after invoice and payment processor verification."
                    ]
                },
            )
        )
        unsupported = engine.evaluate(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.RESULT,
                content="The customer has never disputed a payment before.",
                metadata={"claims": ["The customer has never disputed a payment before."]},
            )
        )

        self.assertEqual(supported, ())
        self.assertEqual(unsupported[0].rule_id, "unsupported-claim")

    def test_tenant_isolation(self) -> None:
        self.graph.ingest_path(self.docs)
        other_graph = ContextGraph(self.root / "context", "other")

        self.assertTrue(self.graph.query("refund approval"))
        self.assertEqual(other_graph.query("refund approval"), [])

    def test_audit_records_include_context_source(self) -> None:
        self.graph.ingest_path(self.docs)
        self._activate("amount_requires_approval")
        audit_path = self.root / "audit.jsonl"
        supervisor = SecuritySupervisor(
            policy_engine=ContextAwarePolicyEngine(self._base_policy(), context_graph=self.graph),
            controller=InMemoryAgentController(),
            audit_store=FileAuditStore(audit_path, redact=False),
        )

        supervisor.observe(self._refund_event(950, role="support_manager"))

        record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
        violation = record["decision"]["violations"][0]
        self.assertTrue(violation["rule_id"].startswith("context:"))
        self.assertIn("refund_policy.md", violation["evidence"])

    def test_cli_context_flow(self) -> None:
        store = self.root / "cli-context"

        self.assertEqual(
            cli_main(
                [
                    "context",
                    "ingest",
                    "--tenant-id",
                    "acme",
                    "--source",
                    str(self.docs),
                    "--store",
                    str(store),
                    "--no-llm-brain",
                ]
            ),
            0,
        )
        graph = ContextGraph(store, "acme")
        amount_rule = next(rule for rule in graph.rules(status="draft") if rule.rule_type == "amount_requires_approval")
        self.assertEqual(
            cli_main(
                [
                    "context",
                    "rules",
                    "activate",
                    "--tenant-id",
                    "acme",
                    "--store",
                    str(store),
                    "--rule-id",
                    amount_rule.id,
                ]
            ),
            0,
        )
        self.assertTrue(graph.rules(status="active"))

    def test_llm_brain_adds_draft_rules_from_unstructured_docs(self) -> None:
        (self.docs / "messy_sop.txt").write_text(
            "When a support bot composes outbound customer email, a human must review it first.",
            encoding="utf-8",
        )
        brain = LLMBrain(
            StaticLLMClient(
                [
                    {"rules": []},
                    {"rules": []},
                    {"rules": []},
                    {
                        "rules": [
                            {
                                "rule_type": "tool_requires_approval",
                                "description": "Outbound customer email requires review.",
                                "severity": "medium",
                                "action": "require_human_approval",
                                "tool_name": "send_email",
                            }
                        ]
                    },
                ]
            )
        )

        self.graph.ingest_path(self.docs, llm_brain=brain)

        self.assertTrue(
            any(
                rule.tool_name == "send_email"
                and rule.rule_type == "tool_requires_approval"
                and rule.status == "draft"
                for rule in self.graph.rules(status="draft")
            )
        )

    def test_llm_brain_escalates_complex_risk_event(self) -> None:
        brain = LLMBrain(
            StaticLLMClient(
                [
                    {
                        "verdict": "require_human_approval",
                        "severity": "medium",
                        "confidence": 0.91,
                        "reason": "The agent is asking to bypass normal refund review.",
                        "evidence": "content asks for exception and no source is provided",
                    }
                ]
            )
        )
        engine = ContextAwarePolicyEngine(self._base_policy(), llm_brain=brain)

        violations = engine.evaluate(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.MESSAGE,
                content="I can skip the refund review this time because the customer is angry.",
            )
        )

        self.assertEqual(violations[0].rule_id, "llm-risk-assessment")
        self.assertEqual(violations[0].action, "require_human_approval")

    def test_llm_brain_low_confidence_is_advisory_only(self) -> None:
        brain = LLMBrain(
            StaticLLMClient(
                [
                    {
                        "verdict": "block",
                        "severity": "high",
                        "confidence": 0.20,
                        "reason": "weak signal",
                        "evidence": "weak evidence",
                    }
                ]
            )
        )
        engine = ContextAwarePolicyEngine(self._base_policy(), llm_brain=brain)

        violations = engine.evaluate(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.MESSAGE,
                content="Normal support update.",
            )
        )

        self.assertEqual(violations, ())

    def _activate(self, rule_type: str) -> None:
        rule = next(rule for rule in self.graph.rules(status="draft") if rule.rule_type == rule_type)
        self.graph.activate_rule(rule.id, reviewed_by="test")

    def _refund_event(self, amount: float, *, role: str) -> AgentEvent:
        return AgentEvent(
            agent_id="agent-1",
            event_type=EventType.TOOL_CALL,
            content="Issue refund",
            metadata={
                "tool_name": "issue_refund",
                "agent_role": role,
                "tool_args": {"amount": amount},
            },
        )

    def _base_policy(self) -> Policy:
        return Policy(
            name="Test",
            version="1.0.0",
            allowed_tools=frozenset(
                {
                    "issue_refund",
                    "export_customer_database",
                    "send_email",
                    "deploy_production",
                }
            ),
            completion_requires_artifacts=False,
            factual_claims_require_sources=True,
        )


if __name__ == "__main__":
    unittest.main()
