# ADR-004: Cryptographic Action-Bound Approval Integrity

- **Status**: Accepted *(Core PG + LangGraph Handoff `CONDITIONAL PASS` in `EVAL-04` on `2026-11-18`; Concrete Tier Matrix & TTLs Open under `OQ-08`; Live OpenFGA/Redis/SecureInput/Takeover Integration Pending Phases 1–3)*
- **Date**: `2026-11-14`
- **Drivers**: Decision 8, Clarification C, Principles `AP-03`, `AP-06`, `AP-07`

---

## Context

When Orbi pauses a task for human-in-the-loop (HITL) approval of a sensitive or irreversible action (`AP-06`), naive task-level approvals ("approve task #42 to continue") create a severe **Time-of-Check to Time-of-Use (TOCTOU)** vulnerability. Between approval request and execution, an agent retry, prompt drift, UI shift, or OpenFGA policy update could alter the email recipient, payment amount, file path, or permissions while reusing the earlier approval.

---

## Decision

1. **Exact Immutable Binding Tuple & Centralized `HumanInteractionManager` (`MOD-03`)**: Every `APPROVAL_REQUEST` is managed through `MOD-03`'s centralized `HumanInteractionManager` (`orbi_app.interaction_requests` linked 1-to-1 with `orbi_app.approval_requests`, pausing tasks as `WAITING_FOR_USER` rather than keeping workers alive) and verified by `MOD-08` (Tool Execution Gateway). Each `ApprovalRequest` must bind cryptographically (`SHA-256` canonical JSON digest + HMAC) to:
   - `user_id` (authenticated approver authorized under OpenFGA)
   - `task_id` & `execution_id` *(stable logical execution identity invariant across worker crashes, `W1 -> W2` lease replacement, checkpoint pause/restoration, and retries—distinct from per-attempt `claim_epoch` or `attempt_count`; includes `automation_generation` when automation-triggered; **`approval_id` MUST also persist in checkpointed step state or resolve from `step_execution_receipts` across secondary interruptions such as `APPROVAL_REQUEST` -> `UNCERTAIN_EXECUTION_RECONCILIATION` per `EVAL-04` Finding 4**)*
   - `tool_id` & `tool_version`
   - `canonical_arguments_hash` (deterministic canonical serialization of semantic tool arguments)
   - `target_resource` (canonical resource identifier)
   - `policy_version` (OpenFGA model/policy revision at request time)
   - `expires_at` (UTC expiration timestamp)
2. **Strict Separation of Interaction Types, Authorization Non-Invalidation & Validation Outcomes (`Clarification C`)**:
   - **Type Isolation**: Ordinary `INFORMATION_REQUEST` answers, `PHYSICAL_ACTION_REQUEST` confirmations, `SECURE_INPUT_REQUEST` secret entries (which bypass LLM context, checkpoints, memory, and logs), and `HUMAN_TAKEOVER` handoffs **never implicitly count as an `APPROVAL_REQUEST`**.
   - **Pre-Mutation OpenFGA Gate vs. Explicit Denial vs. Invalidation**:
     - **(A) Unauthorized Responder**: Checked via `MOD-09` OpenFGA **before** any mutation to `interaction_requests` or `approval_requests`. Denied attempts return `403 AUTHZ_DENIED`, write an immutable `UNAUTHORIZED_INTERACTION_ATTEMPT` security audit in `MOD-15`, and leave the legitimate user's pending interaction (`PENDING_USER`, `interaction_version`) and approval (`PENDING`) completely intact.
     - **(B) Authorized Explicit Denial (`REJECTED`)**: Only an OpenFGA-authorized approver explicitly denying the action transitions `approval_requests` and `interaction_requests` to `REJECTED` (and cancels the waiting task), preventing execution.
     - **(C) Digest, Target, or Policy Mismatch (`INVALIDATED`)**: Before executing a previously approved `ToolCallIntent`, `MOD-08` Gate 5 revalidates live task/cancellation state, active `automation.generation` (if automation-triggered), live OpenFGA `policy_version`, `now < expires_at`, binding digest + HMAC equality, and executes single-use `APPROVED -> CONSUMED` CAS. **Any change** to the tool, tool version, canonical arguments, target resource, approving user, task/automation cancellation state, or OpenFGA `policy_version`—or passage of `expires_at`—automatically transitions `approval_requests` and linked `interaction_requests` to **`INVALIDATED`** (`APPROVAL_INVALIDATED_MUTATION`), aborts execution, records the exact invalidation reason in `MOD-15`, and triggers a fresh approval request if still permitted.
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
