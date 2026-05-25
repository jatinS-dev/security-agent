from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sentient.risk_showcase import run_risk_showcase


class RiskShowcaseTests(unittest.TestCase):
    def test_risk_showcase_exercises_concerning_cases(self) -> None:
        with TemporaryDirectory() as temp_dir:
            summary = run_risk_showcase(temp_dir)

            cases = {case["name"]: case for case in summary["cases"]}
            self.assertEqual(
                cases["Prompt injection asks for bulk customer export"]["sentient_response"],
                "blocked_and_agent_stopped",
            )
            self.assertEqual(
                cases["Wrong-role refund attempt"]["sentient_response"],
                "blocked_and_agent_stopped",
            )
            self.assertEqual(
                cases["Large refund attempt"]["sentient_response"],
                "requires_human_approval",
            )
            self.assertEqual(cases["Secret leakage in agent message"]["sentient_response"], "block")
            self.assertEqual(cases["Payment card disclosure"]["sentient_response"], "block")
            self.assertEqual(cases["Unsupported factual claim"]["sentient_response"], "block")
            self.assertEqual(
                cases["Completion claim without artifacts"]["sentient_response"],
                "block",
            )

            audit_path = Path(summary["audit_path"])
            self.assertTrue(audit_path.exists())
            audit_text = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-demo_1234567890abcdefSECRET", audit_text)
            self.assertIn("[REDACTED]", audit_text)

            records = [
                json.loads(line)
                for line in audit_text.splitlines()
                if line.strip()
            ]
            decision_types = {record["decision"]["decision_type"] for record in records}
            self.assertIn("block", decision_types)
            self.assertIn("require_human_approval", decision_types)


if __name__ == "__main__":
    unittest.main()
