# Great Question Demo Talk Track

Use this as the script while screen sharing.

Start the demo:

```bash
DEMO_PAUSE=1 ./scripts/great_question_demo.sh
```

The script pauses between sections. Read the matching section below, then press Enter in the terminal.

## 0. Opening

Say:

"I built a lightweight demo specifically around Great Question.

My read of Great Question is that it helps teams run better customer research: recruiting participants, managing research workflows, and turning conversations into insights.

That is exactly where AI can help, but it is also where AI can create trust problems. An AI research assistant might summarize interviews, create screeners, send incentives, or touch participant data. So the question I explored is: how do we let AI help researchers move faster without losing privacy, consent, and evidence?"

Say:

"The prototype is called Research Guard. It watches an AI research assistant before actions become production side effects. It can allow safe actions, block risky ones, or require human approval."

Point at terminal:

- `Great Question Application Demo`
- output directory
- read-aloud script path

Press Enter.

## 1. AI Research Assistant Safety Demo

Say before the command runs:

"First I am simulating a Great Question-like research workflow.

The agent can create a screener, interact with participant operations, and generate research insights. Research Guard evaluates each action."

Press Enter.

When output appears, walk through the six rows:

Say:

"The first row is the positive case: the AI creates a research screener. That should be allowed. I do not want safety tooling that blocks useful AI work."

Point at:

```text
AI creates a research screener: allow
```

Say:

"The second row is a prompt-injection-style failure. The agent is asked to export participants. In a research product, participant data is sensitive, so this gets blocked."

Point at:

```text
Prompt injection asks the agent to export participants: block
```

Say:

"The third row is not purely bad. Sending a participant incentive can be legitimate, but a large amount should not be fully autonomous. So Research Guard turns it into an approval request."

Point at:

```text
Large participant incentive requires approval: require_human_approval
```

Say:

"The fourth row is another positive case. The AI generates an insight, but it is tied to transcript evidence, so it is allowed."

Point at:

```text
Source-backed AI insight is allowed: allow
```

Say:

"The fifth row is the research-specific risk I care about most: a confident AI insight that is not grounded in any interview data. That is blocked."

Point at:

```text
Unsupported AI insight is blocked: block
```

Say:

"The last row is participant PII leakage. The AI output includes direct contact details, so it is blocked before it becomes a shared report."

Point at:

```text
Participant PII in AI output is blocked: block
```

Say:

"So the philosophy is not 'AI bad.' It is: helpful research work should pass, risky research operations need controls, and insights need evidence."

Press Enter.

## 2. Audit Trail

Say before the command runs:

"Next I show the audit trail. This is what turns a demo into something a team could actually operate."

Press Enter.

When output appears, say:

"Every action is logged with the agent, event type, decision, and reason. For a research platform, this matters because trust is the product. If AI touches research data or generates insights, teams need to know why the system allowed or blocked something."

Point at examples:

- `gq-research-agent | tool_call | allow`
- `gq-prompt-injected-agent | tool_call | block`
- `gq-ops-agent | tool_call | require_human_approval`
- `gq-hallucination-agent | result | block`
- `gq-pii-leaking-agent | result | block`

Say:

"This gives product, research ops, and security teams a shared record of what happened."

Press Enter.

## 3. Approval Queue

Say before the command runs:

"Now I show the approval queue."

Press Enter.

When output appears, say:

"This is how I think safe AI products should work. Some actions should be allowed, some should be blocked, and some should become approval requests.

Here, sending a large participant incentive is not blocked forever. It is routed to a human reviewer."

Point at:

```text
send_incentive
amount 200 exceeds autonomous limit 75
```

Say:

"That maps well to research operations: the answer is often yes, but only with review."

Press Enter.

## 4. Pilot Report

Say before the command runs:

"The final part is a customer-readable report. I do not want people to dig through JSON logs. I want the system to summarize what it found."

Press Enter.

When output appears, say:

"This report is what I would hand to a customer or internal team after running Research Guard on real AI traffic."

Point at:

```text
Executive Summary
```

Say:

"It shows total monitored actions, allowed actions, risky decisions, and approval requests."

Point at:

```text
Top Violated Rules
```

Say:

"Then it shows the main risks: participant export, incentive approval, unsupported claims, and participant PII disclosure."

Point at:

```text
Risky Tools
```

Say:

"This helps the team decide where to enforce first. In this demo, the risky tools are participant export and incentive sending."

Point at:

```text
Sample Risky Events
```

Say:

"And it includes concrete examples, which is important because policy without examples is hard to trust."

Point at:

```text
Shadow-To-Enforce Readiness Checklist
```

Say:

"Finally, it includes a readiness checklist. My goal is to make the adoption path practical: start by observing, review findings, then enforce the highest-risk actions."

## Closing

Say:

"What I wanted to show is not a polished dashboard. I wanted to show how I think.

I looked at Great Question's domain, picked risks that feel native to research workflows, and built a working vertical slice:

AI assistance, participant data controls, evidence-backed insights, approval workflows, audit logs, and a report a team could actually read."

Say:

"If I continued this, I would connect it to real transcript chunks, study metadata, consent settings, and participant segments. Then I would add a researcher-facing review queue for AI insights that are missing evidence or contain sensitive data.

The bigger idea is simple: AI should make research easier without making it less trustworthy."

## If Asked What Is AI Here

Say:

"The demo models an AI research assistant taking actions and producing research insights. Research Guard is the supervisory layer around that agent. It checks tool calls, generated claims, evidence, approval thresholds, and sensitive participant information."

## If Asked Why This Is Relevant To Great Question

Say:

"Great Question is about making customer research easier and more scalable. AI can help with that, but research data carries trust obligations: consent, participant privacy, and evidence quality. This demo focuses on those exact boundaries."

## If Asked What I Would Improve

Say:

"I would add real transcript retrieval, study-specific consent rules, workspace-level policy configuration, and a UI for reviewing blocked or approval-required research outputs."

## Commands

Run with pauses:

```bash
DEMO_PAUSE=1 ./scripts/great_question_demo.sh
```

Run fast:

```bash
./scripts/great_question_demo.sh
```

Use a stable output directory:

```bash
OUTPUT_DIR=demo_output/great_question_submission DEMO_PAUSE=1 ./scripts/great_question_demo.sh
```
