# First Customer Onboarding

This guide is the first-customer rollout path for Sentient. The goal is to route one production agent workflow through Sentient in shadow mode, prove the audit findings are useful, then move selected high-risk tools to enforcement.

For the engineering integration details, use `docs/customer-integration-guide.md`.

## 1. Define The Pilot

Start with one narrow workflow:

- one agent or agent team
- one production-like environment
- three to seven tools
- one approval owner group
- one clear business risk

Recommended first workflow:

```text
Support agent that reads tickets, sends customer email, issues refunds, and accesses customer data.
```

## 2. Collect Inputs

Use the templates in `docs/templates/`:

- `customer-intake-questionnaire.md`
- `tool-inventory.md`
- `policy-document-checklist.md`
- `pilot-rollout-plan.md`
- `success-criteria.md`

Required customer artifacts:

- agent runtime/framework
- tool list and schemas
- policy/SOP/security docs
- approval owners
- known risky scenarios
- preferred deployment path

## 3. Build The Customer Package

Create a package like `examples/first_customer/`:

```text
customer_policy.json
tool_routes.json
context_docs/
scenarios/
pilot_notes.md
```

Ingest company docs:

```bash
PYTHONPATH=src python3 -m sentient.cli context ingest \
  --tenant-id first-customer \
  --source examples/first_customer/context_docs \
  --store demo_output/first_customer/context
```

Review and activate rules:

```bash
PYTHONPATH=src python3 -m sentient.cli context rules list \
  --tenant-id first-customer \
  --store demo_output/first_customer/context \
  --status draft
```

## 4. Start In Shadow Mode

Shadow mode evaluates the real policy decision but does not block, stop, require approval, or prevent forwarding.

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --tool-routes examples/first_customer/tool_routes.json \
  --context-store demo_output/first_customer/context \
  --tenant-id first-customer \
  --enforcement-mode shadow
```

Agents call:

```text
POST /v1/tool-call
```

Sentient returns `shadow_executed` when it forwarded a call that would have been blocked or approval-gated in enforcement mode.

Detailed request/response examples are in `docs/customer-integration-guide.md`.

## 5. Review Findings

Review audit logs daily:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store logs/audit.jsonl \
  --lines 50
```

Export pilot findings for a customer report:

```bash
PYTHONPATH=src python3 -m sentient.cli audit export \
  --store logs/audit.jsonl \
  --format csv \
  --enforcement-mode shadow \
  --output demo_output/first_customer/shadow-findings.csv
```

Generate the customer-readable pilot report:

```bash
PYTHONPATH=src python3 -m sentient.cli pilot report \
  --audit logs/audit.jsonl \
  --approvals logs/approvals.jsonl \
  --output demo_output/first_customer/pilot-report.md
```

Replay the customer's scenario pack before changing enforcement:

```bash
PYTHONPATH=src python3 -m sentient.cli eval run \
  --policy examples/first_customer/customer_policy.json \
  --suite examples/first_customer/scenarios \
  --no-llm-brain \
  --output demo_output/first_customer/eval-report.json
```

Look for:

- would-have blocked actions
- would-have approval-required actions
- missing policy coverage
- noisy rules
- missing tool metadata
- missing reviewer routes

## 6. Move To Enforcement

Move only agreed high-risk tools first:

- refunds over customer limit
- email send
- customer data export
- production deploy
- payment or account mutation

Run enforce mode:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --tool-routes examples/first_customer/tool_routes.json \
  --context-store demo_output/first_customer/context \
  --tenant-id first-customer \
  --enforcement-mode enforce
```

## Pilot Success Criteria

A first customer pilot is successful when:

- 100% of selected tool calls route through Sentient
- blocked calls are never forwarded in enforce mode
- shadow mode produces useful would-have findings
- eval scenarios pass before switching selected routes to enforce
- approval-required calls produce reviewable approval requests
- audit records cite the relevant rule or context source
- customer agrees on at least three production rules to enforce
