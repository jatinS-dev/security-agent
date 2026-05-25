# Pilot Success Criteria

## Routing

- 100% of selected tool calls route through Sentient.
- Tool names and argument schemas are stable.
- Upstream tool responses are visible in proxy responses.

## Safety

- Shadow mode identifies risky calls without blocking traffic.
- Enforce mode blocks agreed critical actions.
- Approval-required actions create reviewable requests.
- No blocked call is forwarded in enforce mode.

## Audit

- Every decision has an audit record.
- Audit records include `enforcement_mode` and `enforced`.
- Violations include rule IDs and evidence.
- Context decisions cite source docs where available.

## Business

- Customer agrees on at least three production rules.
- Customer names the owner for ongoing policy review.
- Customer agrees whether to expand the pilot.
