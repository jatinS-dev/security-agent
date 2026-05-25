# First Customer Pilot Notes

## Workflow

Support agent handling refund and customer email workflows.

## Shadow Mode Goal

Run selected tool calls through Sentient without blocking production traffic. Review would-have blocked and would-have approval-required decisions daily.

## Initial Tools

- `read_ticket`
- `send_email`
- `issue_refund`
- `export_customer_database`

## Initial Rules

- Block customer database export.
- Require approval for customer email send.
- Require finance approval for refunds above $100.
- Require support manager role for refunds.
- Block secrets and payment card disclosure.

## Promotion To Enforce

Move `export_customer_database`, `send_email`, and high-value `issue_refund` to enforce after customer review.
