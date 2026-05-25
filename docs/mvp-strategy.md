# MVP Strategy

## 1. Target Customer And First Use Case

### Target Customer

The first target customer is a company that is already running AI agents in production or preparing to do so soon.

These companies usually have agents that can:

- call internal tools or APIs
- read customer, employee, financial, legal, or operational data
- write to business systems such as CRM, ticketing, databases, billing, or support platforms
- send external messages such as emails, chat replies, reports, or customer updates
- trigger workflow actions such as refunds, account changes, approvals, deployments, or escalations

The buyer or internal champion is likely one of:

- Head of AI / AI platform lead
- Security engineering lead
- Platform engineering lead
- Compliance or risk leader
- CTO at an AI-heavy startup
- Engineering manager responsible for agentic automation

The most painful customer is not a company casually experimenting with chatbots. The strongest first customer is a company where agents can take actions that affect real systems, real users, real money, sensitive data, or regulated workflows.

### Core Problem

Production AI agents are gaining access to tools, data, and workflow permissions faster than companies are adding oversight.

The risk is not only that an agent gives a wrong answer. The bigger risk is that an agent:

- uses the wrong tool
- acts outside its assigned task
- touches data it should not access
- claims work is complete without evidence
- makes decisions from hallucinated or stale context
- sends messages or changes records without approval
- continues operating after it has clearly gone off track

Most teams have logs after something happens. Fewer teams have an independent control point that can inspect an action before it happens and block it when it violates policy.

### First Use Case

The first MVP use case should be:

> Monitor and control AI agent tool calls before execution.

This is narrower and stronger than trying to understand every token an agent produces. Tool calls are where risk becomes concrete. A tool call has a name, arguments, target system, data scope, and business consequence.

Examples:

- `send_email(customer_id, body)`
- `update_crm_record(account_id, fields)`
- `query_database(sql)`
- `issue_refund(customer_id, amount)`
- `create_jira_ticket(summary, priority)`
- `deploy_service(environment)`
- `delete_file(path)`

The security supervisor should sit between the agent and the tool.

```text
AI Agent
   |
   | requests tool call
   v
Sentient
   |
   | allow / block / require approval
   v
Business Tool Or API
```

### Why Tool-Call Monitoring Is The Best MVP

Tool-call monitoring is the best first use case because:

- It protects the moment where an agent moves from text to real action.
- It is easier to integrate than replacing a company's entire agent framework.
- It creates clear audit logs for security and compliance teams.
- It gives immediate value even before advanced hallucination detection exists.
- It can work across OpenAI Agents SDK, LangGraph, CrewAI, AutoGen, and custom agent runtimes.

### Initial Customer Profile

The first ideal customer profile:

- has 2 or more AI agents in internal or customer-facing workflows
- lets agents use tools or APIs
- cares about approval, compliance, data access, or production safety
- does not want to rebuild their agent stack
- needs auditability around what agents attempted and why actions were allowed or blocked

Good early verticals:

- customer support automation
- fintech operations
- healthcare administration
- insurance claims workflows
- legal operations
- internal IT automation
- sales and CRM automation
- software engineering agents with repository or deployment access

### MVP Boundary

The MVP should not try to solve every AI safety problem.

The first version should focus on:

- policy checks before tool execution
- action blocking
- human approval routing for risky actions
- audit logging
- basic evidence checks for completion or factual claims
- simple adapters for common agent runtimes

The first version should avoid:

- claiming perfect hallucination detection
- trying to replace all application authorization
- building a full enterprise governance suite
- supporting every agent framework on day one
- making autonomous policy decisions without explainable rules

### Clear MVP User Story

As an engineering or security team running production AI agents, I want to wrap each agent tool call with a security supervisor so that unsafe, out-of-scope, unsupported, or high-risk actions are blocked before they reach production systems.

### Example Scenario

A support agent wants to issue a refund:

```text
Agent: support-agent-7
Task: resolve-ticket-1842
Requested tool: issue_refund
Arguments:
  customer_id: cust_991
  amount: 950
  reason: "customer complaint"
Evidence:
  ticket_id: 1842
  policy_doc: none
```

The supervisor checks policy:

- Is `issue_refund` allowed for this agent?
- Is the refund amount below the autonomous approval threshold?
- Is the customer part of the assigned support ticket?
- Is there supporting evidence from the refund policy?
- Is human approval required?

Possible decision:

```text
REQUIRE_HUMAN_APPROVAL
Reason: Refund amount exceeds autonomous threshold of 250.
```

Another scenario:

```text
Agent: devops-agent-2
Task: investigate-staging-incident
Requested tool: deploy_service
Arguments:
  environment: production
  service: payments
```

Possible decision:

```text
BLOCK
Reason: Agent is assigned to staging incident but attempted production deployment.
```

## 2. Core Promise

### Product Promise

The core promise should be:

> Before an AI agent takes action, Sentient checks whether the action is allowed, in scope, evidence-backed, and safe enough to execute. If not, it blocks the action, stops the agent, or routes the action for human approval.

This promise is strong because it focuses on concrete intervention, not passive observation.

### Short Positioning

Sentient is a policy enforcement layer for production AI agents.

It sits between AI agents and the tools they use, inspecting every proposed action before execution. It helps companies prevent agents from taking unsafe actions, operating outside scope, hallucinating completion, or bypassing approval rules.

### One-Sentence Pitch

Sentient stops production AI agents from taking unsafe or unauthorized actions before they happen.

### Slightly Longer Pitch

Companies are giving AI agents access to tools, APIs, and sensitive data, but most teams only find out about bad actions after they happen. Sentient acts as an independent supervisor that checks each agent action against company policy, required evidence, task scope, and approval rules before the action reaches production systems.

### What The Product Must Do Well

The MVP must do these things extremely well:

- intercept tool calls before execution
- understand who the agent is and what task it is assigned
- evaluate action requests against explicit policy
- return a clear decision: `ALLOW`, `BLOCK`, or `REQUIRE_HUMAN_APPROVAL`
- stop or pause the violating agent when required
- produce an audit record with the reason and evidence

### What The Product Should Not Claim

The product should not claim:

- perfect hallucination detection
- full replacement for identity and access management
- complete regulatory compliance by itself
- universal understanding of every business process without customer policy data

The honest claim is stronger:

> We provide a programmable control point for AI agent actions, with policy enforcement, evidence requirements, approval gates, and auditability.

### Decision Types

The first version should support three decisions:

```text
ALLOW
The action is policy-compliant and can execute.

BLOCK
The action violates policy and must not execute.

REQUIRE_HUMAN_APPROVAL
The action may be valid but is too risky, expensive, sensitive, or irreversible for autonomous execution.
```

Later versions can add:

- `WARN`
- `RATE_LIMIT`
- `REDACT`
- `ESCALATE`
- `ALLOW_WITH_CONSTRAINTS`

### Trust Model

The supervisor should be independent from the worker agents.

Worker agents should not be trusted to self-police. They can provide their intent, claims, sources, artifacts, and proposed tool calls, but the security supervisor makes the final decision before execution.

The supervisor should assume:

- agents can be wrong
- agents can hallucinate
- agents can misunderstand task boundaries
- agents can overstate completion
- agents can be prompt-injected by retrieved or user-provided content
- agents can attempt actions that are technically possible but not allowed

### MVP Success Criteria

The MVP is successful if a developer can:

1. Wrap an agent tool call.
2. Define a policy for that tool.
3. Run a simulated risky action.
4. See the supervisor block or require approval before execution.
5. Review an audit log explaining the decision.

### Engineering Implication

The first engineering milestone is an SDK-style guard:

```python
guarded_send_email = supervisor.guard_tool(
    tool_name="send_email",
    tool=send_email,
    policy_context={
        "requires_approval": True,
        "allowed_agent_roles": ["support_manager"],
    },
)
```

Then agents call the guarded tool instead of the raw tool.

```python
guarded_send_email(
    agent_id="support-agent-7",
    task_id="ticket-1842",
    to="customer@example.com",
    body="Your refund has been approved."
)
```

The wrapper should:

1. Build an action event.
2. Ask the supervisor for a decision.
3. Execute the tool only if allowed.
4. Block or route approval if required.
5. Write an audit record.

This wrapper now exists as `SecuritySupervisor.guard_tool(...)`.

The next policy capabilities also exist:

- role-based tool requirements, such as only `support_manager` can call `issue_refund`
- amount thresholds, such as refunds above `250` require approval
- environment rules, such as production deploys requiring approval
- concrete example policies for customer support, DevOps, and finance operations

The first integration adapter also exists:

- `ToolCallContext` for framework-neutral agent/task/role context
- `PythonRuntimeAdapter` for ordinary Python tool functions
- `OpenAIAgentsAdapter` for optional OpenAI Agents SDK `function_tool` integration
- a base `AgentRuntimeAdapter` protocol for future framework-specific adapters

The first verifier layer also exists:

- `EvidenceVerifier` protocol for domain-specific verification
- `KeywordEvidenceVerifier` for transparent claim/evidence matching
- `PhraseEvidenceVerifier` for exact phrase support checks
- `VerifierRegistry` so `PolicyEngine` can reject claims that cite unrelated evidence

No-dashboard MVP surfaces now include:

- policy validation and scenario testing through the CLI
- a reusable scenario library
- an agent registry for known agents, roles, owners, runtimes, and task allowlists
- a standard-library HTTP API for non-Python runtimes
- JSONL and SQLite storage options
- dependency-light adapter shells for LangGraph, CrewAI, and AutoGen
