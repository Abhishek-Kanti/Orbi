# ADR-004: Cryptographic Action-Bound Approval Integrity

- **Status**: Accepted *(State Handoff Validated in `EVAL-04`; Concrete Tier Matrix & TTLs Open under `OQ-08`)*
- **Date**: `2026-11-14`
- **Drivers**: Decision 8, Clarification C, Principles `AP-03`, `AP-06`, `AP-07`

---

## Context

When Orbi pauses a task for human-in-the-loop (HITL) approval of a sensitive or irreversible action (`AP-06`), naive task-level approvals ("approve task #42 to continue") create a severe **Time-of-Check to Time-of-Use (TOCTOU)** vulnerability. Between approval request and execution, an agent retry, prompt drift, UI shift, or OpenFGA policy update could alter the email recipient, payment amount, file path, or permissions while reusing the earlier approval.

---

## Decision

1. **Exact Immutable Binding Tuple**: Every `ApprovalRequest` persisted by `MOD-03` (Task Manager) and verified by `MOD-08` (Tool Execution Gateway) must bind cryptographically (`SHA-256` canonical JSON digest) to:
   - `user_id` (authenticated approver authorized under OpenFGA)
   - `task_id`
   - `tool_id` & `tool_version`
   - `canonical_arguments_hash` (deterministic canonical serialization of semantic tool arguments)
   - `target_resource` (canonical resource identifier)
   - `policy_version` (OpenFGA model/policy revision at request time)
   - `expires_at` (UTC expiration timestamp)
2. **Strict Invalidation on Mutation (`Clarification C`)**:
   - Before executing a previously approved `ToolCallIntent`, `MOD-08` recomputes the binding digest and verifies expiration and `policy_version`.
   - **Any change** to the tool, tool version, canonical arguments, target resource, approving user, or OpenFGA `policy_version`—or passage of `expires_at`—automatically **invalidates** the approval (`APPROVAL_INVALIDATED_MUTATION`), aborts execution, records the event in `MOD-15`, and triggers a fresh approval request.
3. **Volatile Session Token Separation (`CON-10`)**:
   - Ephemeral non-semantic execution tokens (such as refreshed Playwright/Windows `snapshot_id` epochs) must be declared explicitly outside semantic action arguments in `MOD-07` schemas and validated separately via UI/state freshness guards (`CON-05`) rather than allowing silent semantic argument drift.

---

## Consequences

### Positive
- Eliminates TOCTOU bait-and-switch attacks and accidental execution of modified arguments after human review.
- Supports atomic, idempotent single-use consumption across dual UI channels (`MOD-01` Telegram and `MOD-02` Dashboard, `CON-06`).

### Negative / Risks
- Requires strict canonical JSON normalization rules in LLD so innocuous whitespace or key-ordering differences never trigger false invalidations.
- Long human response delays that cross policy updates or `expires_at` TTLs require re-approval (`OQ-08` to calibrate practical TTLs).
