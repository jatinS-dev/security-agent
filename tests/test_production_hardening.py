from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from sentient import (
    AgentEvent,
    ApiSecurityConfig,
    ApprovalExecutionWorker,
    ApprovalRequest,
    ApprovalStatus,
    EmailNotifier,
    EventType,
    FileApiKeyStore,
    FilePolicyVersionStore,
    FileTenantRegistry,
    HashChainedAuditStore,
    InMemoryApprovalStore,
    InMemoryAgentController,
    InMemoryNotifier,
    Policy,
    PolicyEngine,
    PolicyVersionRecord,
    SecuritySupervisor,
    SlackWebhookNotifier,
    TenantSupervisorRouter,
    redact_sensitive_data,
    sign_body,
    verify_source_freshness,
)
from sentient.api import make_handler
from sentient.cli import main
from sentient.stores import _connect_postgres
from sentient.security import InMemoryRateLimiter
from sentient.audit_integrity import verify_hash_chain


class ProductionHardeningTests(unittest.TestCase):
    def test_api_key_and_signature_are_required_when_configured(self) -> None:
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(Policy.from_dict({"name": "p", "version": "1"})),
            controller=InMemoryAgentController(),
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(
                supervisor,
                "logs/test-audit.jsonl",
                security=ApiSecurityConfig(api_key="secret", hmac_secret="hmac"),
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1/decide"
            body = json.dumps(
                {
                    "event": {
                        "agent_id": "agent-1",
                        "event_type": "message",
                        "content": "hello",
                    }
                }
            ).encode("utf-8")
            missing_auth = urllib.request.Request(url, data=body, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(missing_auth, timeout=5)
            self.assertEqual(raised.exception.code, 401)
            raised.exception.close()

            signed = urllib.request.Request(
                url,
                data=body,
                headers={
                    "X-API-Key": "secret",
                    "X-Signature": sign_body("hmac", body),
                },
                method="POST",
            )
            with urllib.request.urlopen(signed, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(payload["decision"]["decision_type"], "allow")

    def test_rate_limiter_blocks_after_limit(self) -> None:
        limiter = InMemoryRateLimiter(limit_per_minute=1)

        self.assertTrue(limiter.allow("agent", now=100))
        self.assertFalse(limiter.allow("agent", now=101))
        self.assertTrue(limiter.allow("agent", now=161))

    def test_hash_chained_audit_log_verifies_and_detects_tampering(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "audit.hash.jsonl"
            store = HashChainedAuditStore(path)
            supervisor = SecuritySupervisor(
                policy_engine=PolicyEngine(Policy.from_dict({"name": "p", "version": "1"})),
                controller=InMemoryAgentController(),
                audit_store=store,
            )
            supervisor.observe(
                AgentEvent(
                    agent_id="agent-1",
                    event_type=EventType.MESSAGE,
                    content="hello",
                )
            )

            ok, message = verify_hash_chain(path)
            self.assertTrue(ok, message)

            contents = path.read_text(encoding="utf-8").replace("hello", "goodbye")
            path.write_text(contents, encoding="utf-8")
            ok, message = verify_hash_chain(path)
            self.assertFalse(ok)
            self.assertIn("record hash mismatch", message)

    def test_policy_version_cli_publish_and_list(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "versions.jsonl"

            result, stdout, stderr = _run_cli(
                "policy",
                "publish",
                "--policy",
                "policies/default_policy.json",
                "--store",
                str(store_path),
                "--author",
                "security@example.com",
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("PUBLISHED", stdout)

            result, stdout, stderr = _run_cli(
                "policy",
                "versions",
                "--store",
                str(store_path),
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("security@example.com", stdout)

    def test_approval_routing_and_notifications(self) -> None:
        notifier = InMemoryNotifier()
        policy = Policy.from_dict(
            {
                "name": "approval",
                "version": "1",
                "allowed_tools": ["issue_refund"],
                "max_autonomous_amounts": {"issue_refund": 250},
                "approval_routes": {"issue_refund": ["finance@example.com"]},
                "approval_expiration_minutes": 15,
            }
        )
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(policy),
            controller=InMemoryAgentController(),
            notifier=notifier,
        )
        decision = supervisor.observe(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="refund",
                metadata={"tool_name": "issue_refund", "tool_args": {"amount": 950}},
            )
        )
        approval = supervisor.create_approval_request(
            AgentEvent(
                agent_id="agent-1",
                event_type=EventType.TOOL_CALL,
                content="refund",
                metadata={"tool_name": "issue_refund", "tool_args": {"amount": 950}},
            ),
            decision,
        )

        self.assertEqual(approval.assigned_reviewers, ("finance@example.com",))
        self.assertIsNotNone(approval.expires_at)
        self.assertEqual(notifier.events[-1]["event_type"], "approval_requested")

    def test_pause_resume_controls_notify(self) -> None:
        controller = InMemoryAgentController()
        controller.register("agent-1")
        notifier = InMemoryNotifier()
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(Policy.from_dict({"name": "p", "version": "1"})),
            controller=controller,
            notifier=notifier,
        )

        supervisor.pause_agent("agent-1", "review")
        supervisor.resume_agent("agent-1", "cleared")

        self.assertFalse(controller.is_paused("agent-1"))
        self.assertEqual(
            [event["event_type"] for event in notifier.events],
            ["agent_paused", "agent_resumed"],
        )

    def test_source_freshness_verifier(self) -> None:
        now = datetime.fromisoformat("2026-05-21T00:00:00+00:00")
        fresh = verify_source_freshness(
            "2026-05-20T00:00:00+00:00",
            max_age_days=3,
            now=now,
        )
        stale = verify_source_freshness(
            "2020-01-01T00:00:00+00:00",
            max_age_days=3,
            now=now,
        )

        self.assertTrue(fresh.supported)
        self.assertFalse(stale.supported)

    def test_policy_activation_and_rollback(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = FilePolicyVersionStore(Path(temp_dir) / "versions.jsonl")
            store.publish(
                PolicyVersionRecord(
                    policy_id="default",
                    version="1.0.0",
                    path="policies/v1.json",
                    created_at="2026-05-20T00:00:00+00:00",
                )
            )
            store.publish(
                PolicyVersionRecord(
                    policy_id="default",
                    version="1.1.0",
                    path="policies/v2.json",
                    created_at="2026-05-21T00:00:00+00:00",
                )
            )

            first = store.activate("default", "1.0.0", activated_by="security@example.com")
            second = store.activate("default", "1.1.0", activated_by="security@example.com")
            rolled_back = store.rollback("default", activated_by="security@example.com")

            self.assertEqual(first.version, "1.0.0")
            self.assertEqual(second.previous_version, "1.0.0")
            self.assertEqual(rolled_back.version, "1.0.0")

    def test_policy_activation_cli(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "versions.jsonl"
            store = FilePolicyVersionStore(store_path)
            store.publish(
                PolicyVersionRecord(
                    policy_id="default",
                    version="1.0.0",
                    path="policies/default_policy.json",
                    created_at="2026-05-21T00:00:00+00:00",
                )
            )

            result, stdout, stderr = _run_cli(
                "policy",
                "activate",
                "--store",
                str(store_path),
                "--policy-id",
                "default",
                "--version",
                "1.0.0",
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("ACTIVE default@1.0.0", stdout)

            result, stdout, stderr = _run_cli(
                "policy",
                "active",
                "--store",
                str(store_path),
                "--policy-id",
                "default",
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("default | 1.0.0", stdout)

    def test_approval_execution_worker_runs_approved_tools_once(self) -> None:
        store = InMemoryApprovalStore()
        store.create(
            ApprovalRequest(
                request_id="approval-1",
                agent_id="support-agent",
                tool_name="issue_refund",
                decision_summary="needs approval",
                status=ApprovalStatus.APPROVED,
                created_at="2026-05-21T00:00:00+00:00",
                tool_args={"customer_id": "cust_1", "amount": 50},
            )
        )
        worker = ApprovalExecutionWorker(
            store,
            tools={"issue_refund": lambda customer_id, amount: f"{customer_id}:{amount}"},
        )

        results = worker.run_once()

        self.assertEqual(results[0].result, "cust_1:50")
        self.assertEqual(store.get("approval-1").status, ApprovalStatus.EXECUTED)

    def test_slack_and_email_notifiers_format_payloads(self) -> None:
        with patch("sentient.notifications.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = None
            SlackWebhookNotifier("https://hooks.slack.test/example").notify(
                "agent_stopped",
                {"agent_id": "agent-1", "reason": "blocked tool"},
            )
            request = urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertIn("agent_stopped", payload["text"])
            self.assertIn("agent-1", payload["text"])

        smtp = Mock()
        smtp.__enter__ = Mock(return_value=smtp)
        smtp.__exit__ = Mock(return_value=None)
        with patch("sentient.notifications.smtplib.SMTP", return_value=smtp):
            EmailNotifier(
                host="smtp.test",
                port=587,
                sender="sentient@example.com",
                recipients=("security@example.com",),
            ).notify("approval_requested", {"request_id": "approval-1"})
            smtp.starttls.assert_called_once()
            smtp.send_message.assert_called_once()

    def test_api_ready_metrics_and_body_limit(self) -> None:
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(Policy.from_dict({"name": "p", "version": "1"})),
            controller=InMemoryAgentController(),
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(
                supervisor,
                "logs/test-audit.jsonl",
                security=ApiSecurityConfig(max_body_bytes=8),
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base}/v1/ready", timeout=5) as response:
                ready = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(f"{base}/metrics", timeout=5) as response:
                metrics = response.read().decode("utf-8")

            oversized = urllib.request.Request(
                f"{base}/v1/decide",
                data=b'{"too":"large"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(oversized, timeout=5)
            self.assertEqual(raised.exception.code, 413)
            raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(ready["status"], "ok")
        self.assertIn("sentient_requests_total", metrics)

    def test_postgres_store_dependency_error_is_actionable(self) -> None:
        real_import = __import__

        def import_without_psycopg(name, *args, **kwargs):
            if name == "psycopg":
                raise ImportError("missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_psycopg):
            with self.assertRaises(RuntimeError) as raised:
                _connect_postgres("postgresql://example")

        self.assertIn("python3 -m pip install psycopg[binary]", str(raised.exception))

    def test_file_api_key_store_issues_authenticates_and_revokes_keys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = FileApiKeyStore(Path(temp_dir) / "keys.jsonl")
            raw_key, record = store.issue(
                tenant_id="acme",
                scopes=("decide",),
                name="agent-runtime",
            )

            self.assertTrue(raw_key.startswith("sentient_sk_"))
            self.assertNotIn(raw_key, Path(temp_dir, "keys.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(
                store.authenticate(raw_key, tenant_id="acme", required_scope="decide"),
                record,
            )
            self.assertIsNone(
                store.authenticate(raw_key, tenant_id="acme", required_scope="audit:read")
            )

            store.revoke(record.key_id)
            self.assertIsNone(store.authenticate(raw_key, tenant_id="acme"))

    def test_keys_cli_issue_list_and_revoke(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "keys.jsonl"
            result, stdout, stderr = _run_cli(
                "keys",
                "issue",
                "--store",
                str(store_path),
                "--tenant-id",
                "acme",
                "--scope",
                "decide",
                "--name",
                "runtime",
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("ISSUED", stdout)
            key_id = stdout.split()[1]

            result, stdout, stderr = _run_cli("keys", "list", "--store", str(store_path))
            self.assertEqual(result, 0, stderr)
            self.assertIn("acme", stdout)

            result, stdout, stderr = _run_cli(
                "keys",
                "revoke",
                key_id,
                "--store",
                str(store_path),
            )
            self.assertEqual(result, 0, stderr)
            self.assertIn("REVOKED", stdout)

    def test_tenant_router_isolates_api_decisions_and_audit_logs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            registry_path = temp_path / "tenants.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {
                                "tenant_id": "acme",
                                "policy_path": "policies/default_policy.json",
                                "audit_path": str(temp_path / "acme-audit.jsonl"),
                                "approvals_path": str(temp_path / "acme-approvals.jsonl"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            key_store = FileApiKeyStore(temp_path / "keys.jsonl")
            raw_key, _ = key_store.issue(tenant_id="acme", scopes=("decide", "audit:read"))
            supervisor = SecuritySupervisor(
                policy_engine=PolicyEngine(Policy.from_dict({"name": "p", "version": "1"})),
                controller=InMemoryAgentController(),
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                make_handler(
                    supervisor,
                    temp_path / "default-audit.jsonl",
                    security=ApiSecurityConfig(api_key_store=key_store),
                    tenant_router=TenantSupervisorRouter(FileTenantRegistry(registry_path)),
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                body = json.dumps(
                    {
                        "event": {
                            "agent_id": "agent-1",
                            "event_type": "message",
                            "content": "hello",
                        }
                    }
                ).encode("utf-8")
                missing_tenant = urllib.request.Request(
                    f"{base}/v1/decide",
                    data=body,
                    headers={"X-API-Key": raw_key},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(missing_tenant, timeout=5)
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()

                request = urllib.request.Request(
                    f"{base}/v1/decide",
                    data=body,
                    headers={"X-API-Key": raw_key, "X-Tenant-ID": "acme"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            self.assertEqual(payload["decision"]["decision_type"], "allow")
            self.assertTrue((temp_path / "acme-audit.jsonl").exists())

    def test_redaction_masks_secrets_recursively(self) -> None:
        payload = {
            "api_key": "sentient_sk_abc1234567890SECRET",
            "nested": {
                "authorization": "Bearer real-token-value",
                "message": "use sk-abcdefghijklmnopqrstuvwxyz123456",
            },
        }

        redacted = redact_sensitive_data(payload)

        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["authorization"], "[REDACTED]")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted["nested"]["message"])
        self.assertEqual(
            redact_sensitive_data("card 4242 4242 4242 4242"),
            "card [REDACTED]",
        )


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = main(argv)
    return result, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
