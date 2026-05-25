# Tool Inventory

| Tool Name | Description | Arguments | Risk | Default Action | Approval Owner | Upstream URL |
| --- | --- | --- | --- | --- | --- | --- |
| read_ticket | Read support ticket details | ticket_id | low | allow |  |  |
| send_email | Send customer email | customer_id, body | high | require approval | support lead |  |
| issue_refund | Issue customer refund | customer_id, amount, reason | high | limit/approval | finance |  |
| export_customer_database | Export customer records |  | critical | block | security |  |

Risk values: `low`, `medium`, `high`, `critical`.

Default action values: `allow`, `require approval`, `block`.
