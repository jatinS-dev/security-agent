from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sentient.cli import main
from sentient.models import ApprovalRequest, ApprovalStatus
from sentient.stores import FileApprovalStore


class CliTests(unittest.TestCase):
    def test_lists_pending_approvals(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "approvals.jsonl"
            store = FileApprovalStore(store_path)
            store.create(_approval("req-1", ApprovalStatus.PENDING))
            store.create(_approval("req-2", ApprovalStatus.APPROVED))

            result, stdout, stderr = _run_cli(
                "approvals",
                "list",
                "--store",
                str(store_path),
            )

            self.assertEqual(result, 0)
            self.assertIn("req-1", stdout)
            self.assertNotIn("req-2", stdout)
            self.assertEqual(stderr, "")

    def test_approves_request(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "approvals.jsonl"
            store = FileApprovalStore(store_path)
            store.create(_approval("req-1", ApprovalStatus.PENDING))

            result, stdout, stderr = _run_cli(
                "approvals",
                "approve",
                "req-1",
                "--store",
                str(store_path),
                "--reviewer",
                "security@example.com",
                "--reason",
                "Looks good.",
            )

            reloaded = FileApprovalStore(store_path).get("req-1")
            self.assertEqual(result, 0)
            self.assertIn("approved: req-1", stdout)
            self.assertEqual(stderr, "")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.status, ApprovalStatus.APPROVED)
            self.assertEqual(reloaded.reviewer, "security@example.com")
            self.assertEqual(reloaded.review_reason, "Looks good.")

    def test_rejects_request_as_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "approvals.jsonl"
            store = FileApprovalStore(store_path)
            store.create(_approval("req-1", ApprovalStatus.PENDING))

            result, stdout, stderr = _run_cli(
                "approvals",
                "reject",
                "req-1",
                "--store",
                str(store_path),
                "--reviewer",
                "security@example.com",
                "--json",
            )

            payload = json.loads(stdout)
            self.assertEqual(result, 0)
            self.assertEqual(payload["request_id"], "req-1")
            self.assertEqual(payload["status"], ApprovalStatus.REJECTED.value)
            self.assertEqual(stderr, "")

    def test_audit_tail_outputs_recent_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            _append_jsonl(audit_path, {"timestamp": "1", "event": {"agent_id": "a1"}, "decision": {"decision_type": "allow", "summary": "allowed"}})
            _append_jsonl(audit_path, {"timestamp": "2", "event": {"agent_id": "a2"}, "decision": {"decision_type": "block", "summary": "blocked"}})

            result, stdout, stderr = _run_cli(
                "audit",
                "tail",
                "--store",
                str(audit_path),
                "--lines",
                "1",
            )

            self.assertEqual(result, 0)
            self.assertNotIn("a1", stdout)
            self.assertIn("a2", stdout)
            self.assertEqual(stderr, "")

    def test_audit_export_csv_filters_shadow_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            _append_jsonl(
                audit_path,
                {
                    "timestamp": "2026-05-25T00:00:00+00:00",
                    "event": {"agent_id": "a1", "event_type": "tool_call", "task_id": "t1"},
                    "decision": {
                        "decision_type": "allow",
                        "enforcement_mode": "enforce",
                        "enforced": True,
                        "summary": "allowed",
                        "violations": [],
                    },
                },
            )
            _append_jsonl(
                audit_path,
                {
                    "timestamp": "2026-05-25T00:01:00+00:00",
                    "event": {"agent_id": "a2", "event_type": "tool_call", "task_id": "t2"},
                    "decision": {
                        "decision_type": "block",
                        "enforcement_mode": "shadow",
                        "enforced": False,
                        "summary": "blocked-tool",
                        "violations": [{"rule_id": "blocked-tool"}],
                    },
                },
            )

            result, stdout, stderr = _run_cli(
                "audit",
                "export",
                "--store",
                str(audit_path),
                "--format",
                "csv",
                "--enforcement-mode",
                "shadow",
            )

            self.assertEqual(result, 0)
            self.assertIn("timestamp,agent_id,task_id,event_type,decision_type", stdout)
            self.assertIn("a2", stdout)
            self.assertIn("blocked-tool", stdout)
            self.assertNotIn("a1", stdout)
            self.assertEqual(stderr, "")

    def test_audit_export_json_normalizes_hash_chained_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.hash.jsonl"
            _append_jsonl(
                audit_path,
                {
                    "previous_hash": "GENESIS",
                    "record_hash": "hash-1",
                    "record": {
                        "timestamp": "2026-05-25T00:00:00+00:00",
                        "event": {"agent_id": "a1", "event_type": "result"},
                        "decision": {
                            "decision_type": "block",
                            "enforcement_mode": "enforce",
                            "enforced": True,
                            "summary": "blocked",
                            "violations": [],
                        },
                    },
                },
            )

            result, stdout, stderr = _run_cli(
                "audit",
                "export",
                "--store",
                str(audit_path),
                "--decision-type",
                "block",
            )

            payload = json.loads(stdout)
            self.assertEqual(result, 0)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["event"]["agent_id"], "a1")
            self.assertNotIn("record_hash", payload[0])
            self.assertEqual(stderr, "")

    def test_eval_run_reports_scenario_suite(self) -> None:
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "eval-report.json"

            result, stdout, stderr = _run_cli(
                "eval",
                "run",
                "--policy",
                "policies/default_policy.json",
                "--suite",
                "scenarios",
                "--no-llm-brain",
                "--output",
                str(report_path),
            )

            self.assertEqual(result, 0)
            self.assertIn("Sentient Eval", stdout)
            self.assertIn("failed=0", stdout)
            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(payload["total"], 7)
            self.assertEqual(payload["failed"], 0)
            self.assertEqual(stderr, "")

    def test_pilot_report_generates_markdown_from_shadow_audit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            approvals_path = Path(temp_dir) / "approvals.jsonl"
            report_path = Path(temp_dir) / "pilot-report.md"
            _append_jsonl(
                audit_path,
                {
                    "timestamp": "2026-05-25T00:00:00+00:00",
                    "event": {
                        "agent_id": "support-agent",
                        "event_type": "tool_call",
                        "task_id": "ticket-1",
                        "metadata": {"tool_name": "read_ticket"},
                    },
                    "decision": {
                        "decision_type": "allow",
                        "enforcement_mode": "shadow",
                        "enforced": False,
                        "summary": "allowed",
                        "violations": [],
                    },
                },
            )
            _append_jsonl(
                audit_path,
                {
                    "timestamp": "2026-05-25T00:01:00+00:00",
                    "event": {
                        "agent_id": "support-agent",
                        "event_type": "tool_call",
                        "task_id": "ticket-1",
                        "metadata": {"tool_name": "export_customer_database"},
                    },
                    "decision": {
                        "decision_type": "block",
                        "enforcement_mode": "shadow",
                        "enforced": False,
                        "summary": "blocked-tool: Agent attempted to use blocked tool.",
                        "violations": [
                            {
                                "rule_id": "blocked-tool",
                                "description": "Agent attempted to use blocked tool.",
                                "severity": "critical",
                                "action": "stop_agent",
                                "evidence": "export_customer_database",
                            }
                        ],
                    },
                },
            )
            FileApprovalStore(approvals_path).create(_approval("req-1", ApprovalStatus.PENDING))

            result, stdout, stderr = _run_cli(
                "pilot",
                "report",
                "--audit",
                str(audit_path),
                "--approvals",
                str(approvals_path),
                "--output",
                str(report_path),
            )

            report = report_path.read_text(encoding="utf-8")
            self.assertEqual(result, 0)
            self.assertIn("REPORT", stdout)
            self.assertIn("# Sentient Pilot Report", report)
            self.assertIn("Total monitored actions: **2**", report)
            self.assertIn("Would-have blocked in shadow mode: **1**", report)
            self.assertIn("blocked-tool", report)
            self.assertIn("export_customer_database", report)
            self.assertIn("Approval Queue", report)
            self.assertEqual(stderr, "")

    def test_pilot_report_prints_to_stdout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            _append_jsonl(
                audit_path,
                {
                    "timestamp": "2026-05-25T00:00:00+00:00",
                    "event": {"agent_id": "a1", "event_type": "result"},
                    "decision": {
                        "decision_type": "block",
                        "enforcement_mode": "shadow",
                        "enforced": False,
                        "summary": "unsupported-claim",
                        "violations": [{"rule_id": "unsupported-claim", "description": "No source."}],
                    },
                },
            )

            result, stdout, stderr = _run_cli(
                "pilot",
                "report",
                "--audit",
                str(audit_path),
                "--approvals",
                str(Path(temp_dir) / "missing-approvals.jsonl"),
            )

            self.assertEqual(result, 0)
            self.assertIn("# Sentient Pilot Report", stdout)
            self.assertIn("unsupported-claim", stdout)
            self.assertEqual(stderr, "")

    def test_serve_help_exposes_enforcement_mode(self) -> None:
        result, stdout, stderr = _run_cli("serve", "--help")

        self.assertEqual(result, 0)
        self.assertIn("--enforcement-mode", stdout)
        self.assertIn("shadow", stdout)
        self.assertEqual(stderr, "")


def _approval(request_id: str, status: ApprovalStatus) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=request_id,
        agent_id="agent-1",
        task_id="task-1",
        tool_name="issue_refund",
        tool_args={"kwargs": {"amount": 950}},
        metadata={"agent_role": "support_manager"},
        decision_summary="amount requires approval",
        status=status,
        created_at="2026-05-21T00:00:00+00:00",
    )


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            result = main(argv)
        except SystemExit as error:
            result = int(error.code or 0)
    return result, stdout.getvalue(), stderr.getvalue()


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record))
        handle.write("\n")


if __name__ == "__main__":
    unittest.main()
