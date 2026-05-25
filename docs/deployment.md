# Sentient Deployment

Sentient can run as a Python library inside an agent runtime or as an HTTP service that every agent calls before it executes risky work.

## Local Service

```bash
PYTHONPATH=src python3 -m sentient.api \
  --policy policies/default_policy.json \
  --api-key test-key \
  --rate-limit-per-minute 120
```

Health checks:

```bash
curl http://127.0.0.1:8080/v1/health -H "X-API-Key: test-key"
curl http://127.0.0.1:8080/v1/ready -H "X-API-Key: test-key"
curl http://127.0.0.1:8080/metrics -H "X-API-Key: test-key"
```

## Tenant-Aware Service

Create tenant-scoped API keys:

```bash
sentient keys issue --tenant-id acme --scope decide --scope audit:read
```

Run with a tenant registry and hashed key store:

```bash
sentient serve \
  --policy policies/default_policy.json \
  --tenant-registry tenants.example.json \
  --api-key-store logs/api_keys.jsonl
```

Tenant-routed requests include both headers:

```bash
curl -X POST http://127.0.0.1:8080/v1/decide \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SENTIENT_API_KEY" \
  -H "X-Tenant-ID: acme" \
  -d '{"event":{"agent_id":"agent-1","event_type":"message","content":"hello"}}'
```

Each tenant can use separate policy, audit, approval, and registry files.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

The API is exposed on port `8080`. JSONL audit and approval stores are persisted in `./logs`.

## Policy Promotion

Publish tested policy versions:

```bash
sentient policy test --policy policies/default_policy.json --all scenarios
sentient policy publish --policy policies/default_policy.json --author security@example.com
sentient policy versions
```

Activate and roll back versions:

```bash
sentient policy activate --policy-id default-supervisor-policy --version 0.1.0
sentient policy active --policy-id default-supervisor-policy
sentient policy rollback --policy-id default-supervisor-policy
```

Compare policy files before promotion:

```bash
sentient policy compare --left policies/default_policy.json --right policies/examples/devops_policy.json
```

## Postgres Stores

JSONL is the default local store. For production persistence, install the optional Postgres dependency:

```bash
python3 -m pip install "sentient[postgres]"
```

Then instantiate the stores in your service bootstrap:

```python
from sentient import PostgresApprovalStore, PostgresAuditStore

audit_store = PostgresAuditStore("postgresql://sentient:sentient@localhost:5432/sentient")
approval_store = PostgresApprovalStore("postgresql://sentient:sentient@localhost:5432/sentient")
```

## Notifications

Use `SlackWebhookNotifier`, `EmailNotifier`, `WebhookNotifier`, or your own `Notifier` implementation to alert humans when agents are stopped or approvals are requested.

## Approval Worker

`ApprovalExecutionWorker` executes approved requests against a registered tool map and marks approvals as executed. Keep this worker in the same trust boundary as the real tools.

## Audit Redaction

Audit stores redact obvious secret fields such as `api_key`, `authorization`, `password`, `secret`, and `token` by default before writing records. Keep raw secrets out of event metadata whenever possible; redaction is a final guardrail, not a data handling strategy.
