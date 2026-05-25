# Pilot Rollout Plan

## Phase 1: Package

- Collect tools and policies.
- Create `customer_policy.json`.
- Create `tool_routes.json`.
- Ingest context documents.
- Review and activate rules.

## Phase 2: Shadow

- Route selected tool calls through Sentient.
- Run `--enforcement-mode shadow`.
- Review audit findings daily.
- Tune noisy or missing rules.

## Phase 3: Enforce

- Move one high-risk tool to enforce mode.
- Confirm blocked calls are not forwarded.
- Confirm approval-required calls create approval requests.
- Expand to more tools after customer sign-off.

## Rollback

- Switch back to `--enforcement-mode shadow`.
- Or temporarily remove a tool route from the pilot.
