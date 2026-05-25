# Security Agent

**Demo Video** [link](https://drive.google.com/file/d/1KdBNy72RxqKqGVmp_l_X9j4Z4ans3cfw/view?usp=drive_link)

A lightweight supervisor agent that watches other AI agents, checks their actions against policy, and stops an agent when it violates safety or task rules.

This project is intentionally provider-neutral. You can connect it to LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, custom workers, or any system that can stream agent events into Python.

## What It Does

- Watches agent events such as messages, tool calls, plans, and final results.
- Checks events against a policy file.
- Detects common risky behavior:
  - using blocked tools
  - running destructive commands
  - exposing secrets
  - making unsupported factual claims
  - claiming work is complete without evidence
- Stops only the violating agent through an `AgentController`.
- Keeps an audit log of every decision.
- Exports audit records for customer pilot reports.
- Generates Markdown pilot reports from shadow-mode findings.
- Tests policy files against scenario fixtures before deployment.
- Runs evaluation suites for first-customer attack and pilot scenarios.
- Exposes a small HTTP API for non-Python agent runtimes.
- Runs as a runtime tool proxy between production agents and production tools.
- Supports shadow mode for first-customer pilots before enforcing blocks.
- Promotes, activates, rolls back, and compares policy versions.
- Sends approval and stop notifications through webhook, Slack, email, or custom adapters.
- Runs approved tool calls through a trusted approval execution worker.
- Supports tenant-specific policies/logs and hashed API key records.
- Redacts obvious secrets from audit records by default.
- Ingests company policies into a reviewed context graph for source-backed monitoring.
- Uses a local LLM brain for messy policy extraction and complex risk assessment.

## MVP Strategy

The first product direction is documented in [docs/mvp-strategy.md](docs/mvp-strategy.md).

The short version: this project should become a policy enforcement layer that sits between production AI agents and the tools they use. The first MVP should monitor and control tool calls before execution, returning `ALLOW`, `BLOCK`, or `REQUIRE_HUMAN_APPROVAL`.

## Example Policies

Example policy files live in [policies/examples](policies/examples):

- `customer_support_policy.json`
- `devops_policy.json`
- `finance_ops_policy.json`

They show practical rules for blocked tools, approval-required tools, amount thresholds, role requirements, protected environments, and secret/data disclosure patterns.

## Project Layout

```text
.
├── docs/mvp-strategy.md
├── agents/example_registry.json
├── policies/default_policy.json
├── scenarios/
├── src/sentient/
│   ├── adapters/
│   │   ├── autogen.py
│   │   ├── base.py
│   │   ├── crewai.py
│   │   ├── langgraph.py
│   │   ├── openai_agents.py
│   │   └── python.py
│   ├── api.py
│   ├── cli.py
│   ├── controller.py
│   ├── demo.py
│   ├── models.py
│   ├── policy.py
│   ├── sdk.py
│   ├── stores.py
│   ├── supervisor.py
│   └── verifiers/
│       ├── base.py
│       ├── keyword.py
│       └── registry.py
└── tests/test_supervisor.py
```

## Run The Demo

```bash
python3 -m sentient.demo
```

If running from a fresh checkout without installing the package:

```bash
PYTHONPATH=src python3 -m sentient.demo
```

## Run The Real Demo

This is the best portfolio/live walkthrough demo. It simulates a customer-support agent handling a duplicate charge ticket while Sentient supervises real tool calls, approvals, blocked actions, audit logs, and agent stops.

```bash
PYTHONPATH=src python3 -m sentient.real_demo --output-dir demo_output
```

Then inspect what happened:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/approvals.jsonl --all
```

More detail is in [docs/real-demo.md](docs/real-demo.md).

## Run The OpenAI Agents SDK Demo

This demo uses a real OpenAI Agents SDK agent loop. The model decides which function tools to call, and Sentient enforces policy before those tools execute.

```bash
python3 -m pip install ".[openai]"
export OPENAI_API_KEY="..."

PYTHONPATH=src python3 examples/openai_agents_real_support_demo.py \
  --output-dir demo_output/openai_agents
```

Then inspect the Sentient records:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/openai_agents/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/openai_agents/approvals.jsonl --all
```

More detail is in [docs/openai-agents-demo.md](docs/openai-agents-demo.md).

## Run The Free Local Ollama Demo

This demo uses a local Ollama model, so it does not need an OpenAI key or paid API quota.

```bash
ollama pull llama3.2

PYTHONPATH=src python3 examples/ollama_local_support_demo.py \
  --model llama3.2 \
  --output-dir demo_output/ollama
```

Then inspect Sentient records:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/ollama/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/ollama/approvals.jsonl --all
```

More detail is in [docs/ollama-demo.md](docs/ollama-demo.md).

## Run The Real Local Risk-Agent Demo

This is the strongest live demo. A real local Ollama model receives attacker-style prompts, chooses tool calls or final answers, and Sentient blocks or escalates the risky behavior.

```bash
/Users/jatin.salve/homebrew/opt/ollama/bin/ollama serve
```

In another terminal:

```bash
PYTHONPATH=src python3 examples/ollama_risk_agent_demo.py \
  --model llama3.2 \
  --output-dir demo_output/ollama_risk
```

Then inspect:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/ollama_risk/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/ollama_risk/approvals.jsonl --all
```

More detail is in [docs/ollama-risk-agent-demo.md](docs/ollama-risk-agent-demo.md).

## Run The Full Live Demo

Use this for customer calls, portfolio walkthroughs, or investor demos. It runs the context graph demo, risk-agent demo, audit review, approvals review, pilot report generation, and scenario eval.

```bash
DEMO_PAUSE=1 ./scripts/live_demo.sh
```

Read from [docs/live-demo-script.md](docs/live-demo-script.md) while presenting.

## Run The Great Question Application Demo

This is a lightweight demo tailored to Great Question's application prompt. It shows an AI research assistant guarded against participant export, unsupported AI insights, participant PII leakage, and high incentive approvals.

```bash
DEMO_PAUSE=1 ./scripts/great_question_demo.sh
```

Read from [docs/great-question-demo-script.md](docs/great-question-demo-script.md) while presenting.

## Run The Risk Showcase

This demo deliberately triggers concerning agent behavior: prompt-injection-style data export, secret leakage, payment card leakage, wrong-role tool use, a large refund, unsupported claims, and fake completion.

```bash
PYTHONPATH=src python3 -m sentient.risk_showcase \
  --output-dir demo_output/risk_showcase
```

Then show the audit and approval queue:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/risk_showcase/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/risk_showcase/approvals.jsonl --all
```

More detail is in [docs/risk-showcase.md](docs/risk-showcase.md).

## Run The Context Graph Demo

This demo shows the new company-policy context flow: ingest docs, create draft graph rules, activate reviewed rules, and monitor risky agent actions with citations back to source policy documents.

```bash
PYTHONPATH=src python3 examples/context_graph_demo.py \
  --output-dir demo_output/context_graph
```

More detail is in [docs/context-graph.md](docs/context-graph.md).

## Run The Runtime Tool Proxy

The runtime tool proxy is how Sentient sits directly between agents and production tools. Agents call `/v1/tool-call`; Sentient blocks, asks for approval, or forwards the call to the configured upstream tool.

```bash
PYTHONPATH=src python3 examples/tool_proxy_demo.py
```

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy policies/examples/customer_support_policy.json \
  --tool-routes examples/tool_routes.example.json
```

More detail is in [docs/tool-proxy.md](docs/tool-proxy.md).

## First Customer Onboarding

Use shadow mode to pilot Sentient without disrupting production traffic:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --tool-routes examples/first_customer/tool_routes.json \
  --enforcement-mode shadow
```

The full rollout checklist is in [docs/customer-onboarding.md](docs/customer-onboarding.md). Templates live in [docs/templates](docs/templates), and a sample customer package lives in [examples/first_customer](examples/first_customer).

For customer engineering teams, the integration guide is in [docs/customer-integration-guide.md](docs/customer-integration-guide.md). It covers the runtime tool proxy, `/v1/decide`, Python SDK wrapper, payload shape, policy context ingestion, and shadow-to-enforce rollout.

## Run Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Operator CLI

The CLI reads the file-backed stores used by the supervisor.

```bash
PYTHONPATH=src python3 -m sentient.cli approvals list
PYTHONPATH=src python3 -m sentient.cli approvals approve <request_id> --reviewer security@example.com
PYTHONPATH=src python3 -m sentient.cli approvals reject <request_id> --reviewer security@example.com --reason "Missing evidence"
PYTHONPATH=src python3 -m sentient.cli audit tail
PYTHONPATH=src python3 -m sentient.cli audit show
PYTHONPATH=src python3 -m sentient.cli audit export --format csv --output demo_output/audit-export.csv
PYTHONPATH=src python3 -m sentient.cli pilot report --audit logs/audit.jsonl --approvals logs/approvals.jsonl --output demo_output/pilot-report.md
PYTHONPATH=src python3 -m sentient.cli audit verify --store logs/audit.hash.jsonl
PYTHONPATH=src python3 -m sentient.cli keys issue --tenant-id acme --scope decide --scope audit:read
PYTHONPATH=src python3 -m sentient.cli keys list
PYTHONPATH=src python3 -m sentient.cli keys revoke <key_id>
PYTHONPATH=src python3 -m sentient.cli context ingest --tenant-id acme --source examples/company_policy_pack --store demo_output/context
PYTHONPATH=src python3 -m sentient.cli context rules list --tenant-id acme --store demo_output/context --status draft
PYTHONPATH=src python3 -m sentient.cli context rules activate --tenant-id acme --store demo_output/context --rule-id <rule_id>
PYTHONPATH=src python3 -m sentient.cli context query --tenant-id acme --store demo_output/context "refund approval limit"
PYTHONPATH=src python3 -m sentient.cli policy validate --policy policies/default_policy.json
PYTHONPATH=src python3 -m sentient.cli policy test --policy policies/default_policy.json --all scenarios
PYTHONPATH=src python3 -m sentient.cli policy explain --policy policies/default_policy.json --scenario scenarios/refund_high_amount.json
PYTHONPATH=src python3 -m sentient.cli eval run --policy policies/default_policy.json --suite scenarios --no-llm-brain --output demo_output/eval-report.json
PYTHONPATH=src python3 -m sentient.cli policy publish --policy policies/default_policy.json --author security@example.com
PYTHONPATH=src python3 -m sentient.cli policy versions
PYTHONPATH=src python3 -m sentient.cli policy activate --policy-id default-supervisor-policy --version 0.1.0
PYTHONPATH=src python3 -m sentient.cli policy active --policy-id default-supervisor-policy
PYTHONPATH=src python3 -m sentient.cli policy rollback --policy-id default-supervisor-policy
PYTHONPATH=src python3 -m sentient.cli policy compare --left policies/default_policy.json --right policies/examples/devops_policy.json
PYTHONPATH=src python3 -m sentient.cli serve --policy policies/default_policy.json --tool-routes examples/tool_routes.example.json
```

After package installation, the same commands are available through:

```bash
sentient approvals list
sentient audit tail
sentient audit export --format csv --output audit-export.csv
sentient pilot report --audit logs/audit.jsonl --approvals logs/approvals.jsonl --output pilot-report.md
sentient policy test --policy policies/default_policy.json --all scenarios
sentient eval run --policy policies/default_policy.json --suite scenarios --no-llm-brain
```

## Scenario Library

Scenario files in [scenarios](scenarios) let you test a policy before production.

```json
{
  "name": "High refund requires approval",
  "event": {
    "agent_id": "support-agent-7",
    "task_id": "ticket-1842",
    "event_type": "tool_call",
    "content": "Issue refund",
    "metadata": {
      "tool_name": "issue_refund",
      "agent_role": "support_manager",
      "tool_args": {
        "amount": 950
      }
    }
  },
  "expected_decision": "require_human_approval"
}
```

## Agent Registry

The optional agent registry defines known agents, roles, owners, runtimes, risk levels, and task allowlists.

```bash
sentient policy test \
  --policy policies/default_policy.json \
  --all scenarios \
  --agent-registry agents/example_registry.json
```

## HTTP API

Run:

```bash
PYTHONPATH=src python3 -m sentient.api --policy policies/default_policy.json
```

Endpoints:

```text
GET  /v1/health
GET  /v1/ready
GET  /metrics
GET  /openapi.json
POST /v1/decide
GET  /v1/approvals
POST /v1/approvals/{request_id}/approve
POST /v1/approvals/{request_id}/reject
GET  /v1/audit
```

Optional API hardening:

```bash
sentient serve \
  --policy policies/default_policy.json \
  --api-key "$SECURITY_AGENT_API_KEY" \
  --hmac-secret "$SECURITY_AGENT_HMAC_SECRET" \
  --rate-limit-per-minute 120 \
  --max-body-bytes 1000000 \
  --api-key-store logs/api_keys.jsonl \
  --tenant-registry tenants.example.json
```

Clients send:

```text
X-API-Key: ...
X-Signature: HMAC-SHA256(body, secret)
X-Tenant-ID: acme
```

## How To Connect Real Agents

Send each agent action to the supervisor as an `AgentEvent`:

```python
from sentient import AgentEvent, EventType, SecuritySupervisor

decision = supervisor.observe(
    AgentEvent(
        agent_id="research-agent-1",
        event_type=EventType.RESULT,
        task_id="task-42",
        content="The dependency is safe to upgrade.",
        metadata={
            "claims": ["The dependency is safe to upgrade."],
            "sources": ["security-scan-report.json"],
            "artifacts": ["upgrade-pr.diff"],
        },
    )
)
```

If `decision.should_stop_agent` is true, the supervisor calls your controller's `stop_agent(agent_id, reason)` method.

## Guard A Tool Before Execution

The SDK wrapper lets you place the supervisor between an AI agent and a real tool function.

```python
from sentient import HumanApprovalRequired, ToolCallBlocked


def issue_refund(customer_id: str, amount: int) -> str:
    return f"Refunded {amount} to {customer_id}"


guarded_issue_refund = supervisor.guard_tool(
    "issue_refund",
    issue_refund,
    default_metadata={"agent_role": "support_manager"},
)

try:
    result = guarded_issue_refund(
        "cust_123",
        950,
        agent_id="support-agent-7",
        task_id="ticket-1842",
    )
except HumanApprovalRequired as approval:
    print(f"Needs approval: {approval.decision.summary}")
except ToolCallBlocked as blocked:
    print(f"Blocked: {blocked.decision.summary}")
```

The wrapped tool only executes when the supervisor returns `ALLOW`. If the decision is `BLOCK` or `REQUIRE_HUMAN_APPROVAL`, the original function is not called.

Policy checks can use:

- `agent_id`
- `task_id`
- `agent_role`
- tool name
- tool arguments such as `amount` or `environment`
- sources, claims, and artifacts supplied in metadata

## Human Approval Flow

When a guarded tool requires approval, the SDK creates an approval request and raises `HumanApprovalRequired`.

```python
try:
    guarded_issue_refund("cust_123", 950, agent_id="support-agent-7")
except HumanApprovalRequired as approval:
    request_id = approval.approval_request.request_id
    supervisor.approve_request(
        request_id,
        reviewer="security@example.com",
        reason="Escalated refund approved.",
    )
    result = guarded_issue_refund.execute_approved(request_id)
```

The supervisor also supports:

```python
supervisor.list_pending_approvals()
supervisor.reject_request(request_id, reviewer="security@example.com")
```

For persistence, pass file-backed stores:

```python
from sentient import FileApprovalStore, FileAuditStore

supervisor = SecuritySupervisor(
    policy_engine=engine,
    controller=controller,
    audit_store=FileAuditStore("logs/audit.jsonl"),
    approval_store=FileApprovalStore("logs/approvals.jsonl"),
)
```

## Framework Adapter Layer

Adapters help companies connect existing agent runtimes to the supervisor without changing the policy engine.

The first adapter is dependency-free and works with normal Python functions:

```python
from sentient import PythonRuntimeAdapter, ToolCallContext


adapter = PythonRuntimeAdapter(supervisor)


def issue_refund(customer_id: str, amount: int) -> str:
    return f"Refunded {amount} to {customer_id}"


guarded_refund = adapter.wrap_tool("issue_refund", issue_refund)

result = guarded_refund(
    "cust_123",
    100,
    security_context=ToolCallContext(
        agent_id="support-agent-7",
        task_id="ticket-1842",
        agent_role="support_manager",
    ),
)
```

The shared adapter contract is:

- `ToolCallContext`: framework-neutral agent/task/role metadata
- `AgentRuntimeAdapter`: protocol for runtime-specific adapters
- `PythonRuntimeAdapter`: plain Python implementation
- `LangGraphAdapter`, `CrewAIAdapter`, and `AutoGenAdapter`: dependency-light wrappers around the same contract

This gives us the shape needed for future OpenAI Agents SDK, LangGraph, CrewAI, and AutoGen adapters.

## OpenAI Agents SDK Adapter

The OpenAI Agents adapter is optional. It does not add the Agents SDK as a hard dependency, but if the SDK is installed it can wrap a guarded function with `agents.function_tool`.

```python
from sentient import OpenAIAgentsAdapter, ToolCallContext


adapter = OpenAIAgentsAdapter(supervisor)


def issue_refund(customer_id: str, amount: int) -> str:
    """Issue a customer refund."""
    return f"Refunded {amount} to {customer_id}"


guarded_refund_tool = adapter.wrap_function_tool(
    "issue_refund",
    issue_refund,
    default_context=ToolCallContext(
        agent_id="support-agent-7",
        task_id="ticket-1842",
        agent_role="support_manager",
    ),
)
```

For lower-level integrations, use `wrap_function(...)` to get a guarded callable without applying the OpenAI `function_tool` decorator.

## Evidence Verification

The verifier layer checks whether factual claims are supported by provided evidence text.

```python
from sentient import KeywordEvidenceVerifier, PolicyEngine, VerifierRegistry

engine = PolicyEngine(
    policy,
    verifier_registry=VerifierRegistry(
        KeywordEvidenceVerifier(min_overlap_ratio=0.5),
    ),
)
```

Agents should provide:

```python
metadata={
    "claims": ["Dependency has no critical vulnerabilities."],
    "sources": ["scan.json"],
    "evidence_text": "Scan result: dependency has zero critical vulnerabilities.",
    "artifacts": ["scan.json"],
}
```

If `claims` and `sources` are present but `evidence_text` is missing or unrelated, the supervisor blocks the claim.

## Storage

JSONL stores are the default for local development:

```python
FileAuditStore("logs/audit.jsonl")
FileApprovalStore("logs/approvals.jsonl")
```

SQLite stores are available when you want a single local database file:

```python
from sentient import SQLiteApprovalStore, SQLiteAuditStore

audit_store = SQLiteAuditStore("security-agent.db")
approval_store = SQLiteApprovalStore("security-agent.db")
```

Tamper-evident audit logs are available through `HashChainedAuditStore` and can be checked with:

```bash
sentient audit verify --store logs/audit.hash.jsonl
```

Postgres stores are optional for production deployments:

```bash
python3 -m pip install "sentient[postgres]"
```

```python
from sentient import PostgresApprovalStore, PostgresAuditStore

audit_store = PostgresAuditStore("postgresql://sentient:sentient@localhost:5432/sentient")
approval_store = PostgresApprovalStore("postgresql://sentient:sentient@localhost:5432/sentient")
```

## Deployment

Sentient can run as a containerized HTTP service:

```bash
cp .env.example .env
docker compose up --build
```

More deployment notes are in [docs/deployment.md](docs/deployment.md).

## Policy Promotion

Policy versions can be recorded, activated, inspected, rolled back, and compared:

```bash
sentient policy publish --policy policies/default_policy.json --author security@example.com
sentient policy activate --policy-id default-supervisor-policy --version 0.1.0
sentient policy active --policy-id default-supervisor-policy
sentient policy rollback --policy-id default-supervisor-policy
sentient policy compare --left policies/default_policy.json --right policies/examples/devops_policy.json
```

## API Keys And Tenants

Issue tenant-scoped keys:

```bash
sentient keys issue --tenant-id acme --scope decide --scope approvals:read
sentient keys list --tenant-id acme
sentient keys revoke <key_id>
```

The raw key is printed only when it is issued. The key store saves only a SHA-256 hash.

Tenant routing uses `X-Tenant-ID` and a registry like [tenants.example.json](tenants.example.json). Each tenant can point to its own policy, audit log, approval store, and agent registry.

```bash
sentient serve \
  --policy policies/default_policy.json \
  --tenant-registry tenants.example.json \
  --api-key-store logs/api_keys.jsonl
```

When tenant routing is enabled, tenant-specific endpoints such as `/v1/decide`, `/v1/audit`, and `/v1/approvals` require `X-Tenant-ID`.

## Approval Execution Worker

Approved requests can be executed by a trusted worker:

```python
from sentient import ApprovalExecutionWorker

worker = ApprovalExecutionWorker(
    supervisor.approval_store,
    tools={"issue_refund": issue_refund},
)
worker.run_once()
```

## Runtime Controls And Notifications

The supervisor supports `pause_agent(...)`, `resume_agent(...)`, and stop notifications. Notification hooks include:

- `InMemoryNotifier`
- `FileNotifier`
- `WebhookNotifier`
- `SlackWebhookNotifier`
- `EmailNotifier`

Approval routes can be configured per tool in policy:

```json
{
  "approval_routes": {
    "issue_refund": ["support-leads@example.com", "finance-ops@example.com"]
  },
  "approval_expiration_minutes": 60
}
```

## Important Design Note

Hallucination detection requires context. This supervisor can verify claims only when agents provide claims, sources, evidence text, task IDs, tool calls, and artifacts. The default keyword verifier is intentionally simple; for stronger protection, connect the verifier layer to your knowledge base, source documents, retrieval system, CI results, or domain-specific validators.
