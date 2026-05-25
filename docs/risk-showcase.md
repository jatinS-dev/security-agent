# Risk Showcase

This demo is designed for a portfolio recording or live walkthrough. It deliberately triggers concerning agent behavior and shows Sentient stopping or escalating it.

Run:

```bash
PYTHONPATH=src python3 -m sentient.risk_showcase \
  --output-dir demo_output/risk_showcase
```

Inspect records:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store demo_output/risk_showcase/audit.jsonl

PYTHONPATH=src python3 -m sentient.cli approvals list \
  --store demo_output/risk_showcase/approvals.jsonl \
  --all
```

What it demonstrates:

- prompt-injection-style bulk customer export is blocked
- secret-shaped output is blocked and redacted from audit artifacts
- payment-card-like output is blocked
- wrong-role refund attempt is blocked
- very large refund requires human approval
- unsupported factual claim is blocked
- completion claim without artifacts is blocked

The large refund is intentionally left pending so the approval queue has a clear item to show.
