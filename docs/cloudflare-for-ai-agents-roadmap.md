# Cloudflare-Style Roadmap For AI Agents

This note captures the product direction we discussed: a security layer for production AI agents that works like Cloudflare works for websites.

Cloudflare sits between users and websites to make traffic faster, safer, and more reliable. Our equivalent sits between AI agents and tools, users, data, or production systems.

Positioning:

```text
Cloudflare protects websites before traffic reaches the origin.
Research Guard protects production AI systems before agent actions reach tools, users, data, or infrastructure.
```

## Core Analogy

| Cloudflare Concept | AI Agent Security Equivalent |
| --- | --- |
| Reverse proxy | Runtime tool proxy for agent actions |
| WAF | AI agent firewall |
| Bot detection | Rogue agent detection |
| Rate limiting | Agent/tool action limits |
| CDN/cache | Safe response and policy/context cache |
| DNS | Agent registry and routing map |
| SSL/TLS | Signed/authenticated agent requests |
| Traffic logs | Agent action audit logs |
| Firewall rules | Agent policy/risk rules |
| Rule simulation | Shadow mode |

## Features To Build Later

### 1. Reverse Proxy For Agent Tools

Agents should not call sensitive tools directly. They call our gateway first.

Flow:

```text
AI Agent -> Research Guard -> Business Tool/API
```

Research Guard decides:

- allow
- block
- require approval
- shadow log only

Current status:

- Started via `/v1/tool-call`
- Runtime tool proxy exists
- Shadow/enforce modes exist

### 2. AI Agent Firewall

Cloudflare has WAF rules for web requests. We need firewall rules for agent actions.

Examples:

- block participant/customer data export
- block secrets in generated output
- block PII in broad reports
- block destructive tools
- block unsupported factual claims
- block wrong-role tool usage
- block prompt-injection outcomes

Future CLI idea:

```bash
research-guard rules add \
  --if tool=export_participant_list \
  --then block
```

### 3. Rogue Agent Detection

Detect when an agent starts behaving abnormally.

Signals:

- too many tool calls in a short time
- repeated blocked attempts
- repeated approval-required actions
- unusual tool sequence
- attempting tools outside task scope
- accessing too many records
- switching from read-only tools to mutation tools unexpectedly

Possible actions:

- pause agent
- stop agent
- require human review
- reduce allowed tool set
- alert owner

### 4. Agent Rate Limiting

Add Cloudflare-style rate limits for agent actions.

Examples:

```json
{
  "tool_rate_limits": {
    "send_email": "10/minute",
    "issue_refund": "5/hour",
    "export_participant_list": "0/hour",
    "send_incentive": "20/day"
  }
}
```

Useful dimensions:

- per agent
- per tool
- per tenant
- per task/session
- per environment
- per customer/participant/account

Decisions:

- allow within limit
- require approval near threshold
- block over threshold
- stop agent after repeated violations

### 5. Safe Response / Context Cache

For low-risk read-only calls, cache safe results.

Possible cache targets:

- policy lookups
- context graph retrieval
- approved knowledge snippets
- repeated read-only tool calls
- verifier results for same claim/evidence pair

Benefits:

- lower latency
- lower cost
- less load on customer systems
- more consistent decisions

Important guardrail:

- never cache sensitive raw data unless tenant policy allows it

### 6. Agent Registry And Routing Map

Cloudflare DNS maps domains to origins. We need a trusted map of agents and where they are allowed to operate.

Agent registry fields:

- agent ID
- role
- owner/team
- allowed tools
- allowed tasks
- allowed environments
- risk level
- tenant/workspace
- runtime/framework

Decisions:

- unknown agents blocked or shadowed
- wrong-role requests blocked
- task boundary escapes blocked

Current status:

- Basic agent registry exists

### 7. Signed Requests / Trust Layer

Cloudflare secures traffic with TLS and certificates. We need authenticated agent traffic.

Features:

- API keys
- HMAC signatures
- tenant IDs
- request timestamps
- replay protection
- signed tool calls
- route-level auth requirements

Current status:

- API keys and HMAC support exist
- More production hardening can be added

### 8. Agent Traffic / Action Summary

Like Cloudflare traffic analytics, provide agent action summaries.

Future command:

```bash
research-guard traffic summary \
  --audit logs/audit.jsonl
```

Useful output:

- total actions
- top tools
- top blocked rules
- top agents by risky behavior
- approval volume
- shadow-mode would-have blocks
- enforce-mode blocks
- actions by environment
- actions by tenant

Current status:

- Pilot report exists
- Audit export exists

### 9. Fail-Open / Fail-Closed Tool Routes

If the security layer is unavailable, customers need per-tool behavior.

Examples:

```json
{
  "tool_name": "send_incentive",
  "failure_mode": "fail_closed"
}
```

Suggested defaults:

- critical mutation tools: fail closed
- financial actions: fail closed
- data export: fail closed
- read-only low-risk tools: fail open or degrade
- internal search: fail open with audit warning

### 10. Rule Simulation And Shadow Mode

Cloudflare lets teams test rules before enforcing. We should keep investing in shadow mode.

Shadow mode should show:

- would-have blocked
- would-have required approval
- what rule matched
- which tool/action was forwarded anyway
- recommended enforcement candidates

Current status:

- Shadow mode exists
- Pilot report exists

## Recommended Next Build Order

1. **Agent Rate Limiting**
   - Per-agent/per-tool limits
   - Audit records for rate-limit hits
   - Tests for allow/block/approval thresholds

2. **Rogue Agent Detection**
   - Stop or pause agents after repeated violations
   - Track bursty tool behavior
   - Add owner alert metadata

3. **Traffic Summary CLI**
   - Summarize audit logs by agent, tool, rule, tenant, and mode
   - Useful for customer demos and pilots

4. **Fail-Open / Fail-Closed Route Config**
   - Add route-level failure behavior to tool proxy
   - Make critical actions fail closed by default

5. **Rule Builder CLI**
   - Add simple firewall-rule style commands
   - Generate policy JSON safely

## Product Message

Short version:

```text
Research Guard is a Cloudflare-style protection layer for production AI agents.
It sits in front of agent actions, applies company policy, blocks risky behavior, requires approval when needed, and gives teams an audit trail.
```

Longer version:

```text
Companies are giving AI agents access to tools, data, and workflows. Research Guard gives those companies a control plane: a reverse proxy, firewall, rate limiter, approval gate, and audit layer for AI agent actions.
```
