# Orbi — EVAL-04 Evaluation & Decision Report

- **Experiment ID**: `EVAL-04`
- **Target Baseline**: `DOC-HLD-002` ([system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md) Rev 1.4, §C.2, §D.1–D.3, §E.4, §E.5, §L.1–L.2) and [ADR-004](file:///f:/Projects/Orbi/docs/04-adr/ADR-004-cryptographic-action-bound-approvals.md)
- **Date**: `2026-11-18`
- **Runtime Stack**: Python `3.12.8`, PostgreSQL `16-alpine` (Docker), `langgraph==1.2.22`, `langgraph-checkpoint-postgres==3.2.1`, `psycopg==3.3.9`, `jsonschema==4.26.0`, `pytest==9.2.0`
- **Final Decision**: **`CONDITIONAL PASS`** *(Core HLD architecture validated across all 13 experiments with 12/12 automated test functions passing against real PostgreSQL; conditioned on incorporating 6 concrete empirical specifications into LLD / targeted HLD wording clarifications before locking `docs/03-lld/`)*

---

## 1. Executive Summary

This evaluation empirically validated Orbi's core execution and human-in-the-loop (HITL) handoff architecture in an isolated prototype (`evals/eval-04-langgraph-handoff/`) backed by real PostgreSQL 16 schemas (`orbi_app`, `orbi_automation`, `langgraph_checkpoints`) and LangGraph's `PostgresSaver`.

All **13 required experiments** passed without requiring any structural redesign to the frozen HLD (`docs/` remained completely untouched). The prototype confirms that:
1. `MOD-03` (`DeterministicTaskManager` + internal `HumanInteractionManager`) can hold sole authoritative lifecycle state in PostgreSQL while relegating LangGraph checkpoints strictly to subordinate intra-turn scratchpad state.
2. Workers release 100% of threads, memory, and checkpointer database connections upon entering `WAITING_FOR_USER` and resume seamlessly on completely different workers from `verified_checkpoint_id`.
3. The Four-Phase Suspension Protocol (`SUSPENDING_FOR_USER` -> verified `langgraph_checkpoints` -> `WAITING_FOR_USER` + outbox) and Bounded-Lease `claim_epoch` CAS (`task_lifecycle_outbox` + `task_lifecycle_processed_events`) survive crashes across every critical boundary with zero duplicate resumes or duplicate external side effects.
4. Write-ahead `step_execution_receipts` (`PREPARED -> DISPATCHING -> CONFIRMED_SUCCESS | UNKNOWN_OUTCOME`) keyed by immutable `action_idempotency_key` preserve logical action identity across worker crashes and enforce safe reconciliation (`PROVIDER_IDEMPOTENCY_KEY`, `RECONCILABLE_READ_PROBE`, and no-blind-retry escalation via `WAITING_FOR_USER` / `UNCERTAIN_EXECUTION_RECONCILIATION`).

---

## 2. Reproducible Local Setup & Prototype Scope

### Files Created (Isolated Under `evals/eval-04-langgraph-handoff/`)
- [docker-compose.yml](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/docker-compose.yml): Single `postgres:16-alpine` container (`orbi-eval04-postgres`, host port `54328`).
- [pyproject.toml](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/pyproject.toml): Python `>=3.12` dependencies (`langgraph`, `langgraph-checkpoint-postgres`, `psycopg[binary,pool]`, `jsonschema`, `pytest`).
- [models.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/models.py): HLD state enums (`TaskStatus`, `InteractionType`, `InteractionStatus`, `ApprovalStatus`, `OutboxStatus`, `ReceiptStatus`, `ExternalIdempotencyMode`, `AutomationStatus`), domain exceptions, and `InMemoryPolicyEngine` (`MOD-09` stand-in).
- [db.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/db.py): Isolated PostgreSQL DDL for `orbi_app`, `orbi_automation`, and `langgraph_checkpoints`, including append-only trigger `prevent_audit_mutation` on `orbi_app.audit_events`.
- [checkpointer.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/checkpointer.py): `OrbiPostgresSaver` wrapping `PostgresSaver` on `search_path=langgraph_checkpoints,public`, two-table `__interrupt__` verification, terminal purge, and deterministic crash injection hooks.
- [gateway.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/gateway.py): `ToolRegistry` (`MOD-07`) and `ToolExecutionGateway` (`MOD-08`) enforcing Gates 1–8, `ADR-004` digest/HMAC validation, `claim_epoch` fencing, write-ahead `DISPATCHING` receipts, uncertain-outcome reconciliation, and cancellation race honesty (`CANCELLED_AFTER_EXTERNAL_COMMIT`).
- [task_manager.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/task_manager.py): `DeterministicTaskManager`, `HumanInteractionManager` (Step 0 pre-mutation authz + 3-step `interaction_version` CAS + Phase A–D suspension/resumption), and 3-part `CrashReconciler`.
- [runtime.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/runtime.py): `LangGraphWorkerRuntime` (`StateGraph` using `langgraph.types.interrupt()` / `Command(resume=...)`) and stateless `Worker`.
- [cli.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/src/eval04/cli.py): End-to-end runnable CLI walkthrough.
- [test_eval04_scenarios.py](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/tests/test_eval04_scenarios.py): Automated pytest suite covering Experiments 1–13 + latency benchmark (`test_exp14_latency_measurements`).

### Verification Commands Executed
```bash
docker-compose -f evals/eval-04-langgraph-handoff/docker-compose.yml up -d
evals/eval-04-langgraph-handoff/.venv/Scripts/python.exe -m pytest evals/eval-04-langgraph-handoff/tests -v -s
evals/eval-04-langgraph-handoff/.venv/Scripts/python.exe -m eval04.cli
```

---

## 3. Scenario Test Report (Experiments 1–13)

**Overall Automated Result**: `12 passed in 36.32s` (`0` failed, `0` skipped) against PostgreSQL `16-alpine`.

| Exp # | Required Scenario | Test Function | Mapped HLD / EVAL IDs | Observed Outcome & Key Assertions | Status |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **1** | Execute simple LangGraph task & persist checkpoint | `test_exp01_simple_langgraph_task_and_checkpoint_persistence` | §D.1, §D.2, §D.3 Part 3 | Task executed `plan_step -> execute_tool_step -> conclude_step`, persisted `>= 2` rows in `langgraph_checkpoints.checkpoints`, committed `COMPLETED`, and `purge_orphaned_checkpoints` reduced thread rows to `0`. | **PASS** |
| **2** | Suspend execution when human input is required | `test_exp02_03_suspend_and_persist_authoritative_pg_state` | §E.5.4, `HITL-EVAL-01` | Node called `langgraph.types.interrupt()`; Phase B read-verified both `checkpoints` and `checkpoint_writes (channel='__interrupt__')` in `langgraph_checkpoints`. | **PASS** |
| **3** | Persist authoritative task & interaction state in PostgreSQL | `test_exp02_03_suspend_and_persist_authoritative_pg_state` | §D.1, §E.5.3, §L.1 | Verified `orbi_app.tasks` (`WAITING_FOR_USER`, `task_version=2`, `verified_checkpoint_id`), `interaction_requests` (`PENDING_USER`, `interaction_version=1`), `task_lifecycle_outbox` (`DELIVER_INTERACTION_PROMPT`), and PG trigger blocking `UPDATE` on `audit_events`. | **PASS** |
| **4** | Release worker completely while waiting for user | `test_exp04_05_release_worker_and_resume_on_different_worker` | §B.3, §E.5.4 (`NFR-REL-04`) | `Worker("worker-1")` closed its DB/checkpointer connection (`saver.conn.closed == True`), deleted and GC'd; PG task retained `WAITING_FOR_USER` with `claimed_by_worker_id IS NULL` and `lease_expires_at IS NULL`. | **PASS** |
| **5** | Submit response & resume graph using different worker | `test_exp04_05_release_worker_and_resume_on_different_worker` | §E.5.3, §E.5.4, `HITL-EVAL-01`, `HITL-EVAL-02` | Schema-invalid reply triggered `VALIDATION_FAILED_RETRYABLE` (`interaction_version=4`); valid reply passed 3-step CAS (`RESOLVED`), queued `RESUME_TASK`, and brand-new `Worker("worker-2")` resumed `verified_checkpoint_id` via `Command(resume=...)` to `COMPLETED` (clearing `resume_token_id`). | **PASS** |
| **6** | Verify PG remains authoritative & checkpoints subordinate | `test_exp06_postgres_authoritative_and_checkpoints_subordinate` | §D.2, `ADR-004`, `HITL-EVAL-03`, `04`, `14`, `21` | Unauthorized actor rejected with `403 AuthzDeniedError` with zero state mutation (`PENDING_USER`, `v=1` intact); direct rogue `Command(resume=...)` on LangGraph without PG lease fenced by `MOD-08` (`StaleClaimEpochFencedError`, `0` external calls); mutated tool args invalidated `APPROVAL_REQUEST` (`INVALIDATED`, `0` external calls). | **PASS** |
| **7** | Duplicate outbox delivery & idempotent resume handling | `test_exp07_duplicate_outbox_delivery_and_idempotent_resume` | §E.5.3, §E.5.4, `HITL-EVAL-13` | Duplicate `response_idempotency_key` returned `idempotent_replay=True` (`1` outbox row total); stale `expected_version=1` raised `409 InteractionConflictError`; duplicate `RESUME_TASK` delivery allowed only `worker-2a` to win CAS while `worker-2b` and post-completion `worker-2c` (`task_lifecycle_processed_events`) received `None` (`1` external call total). | **PASS** |
| **8** | Crash between checkpoint persistence & PG suspension commit | `test_exp08_crash_between_checkpoint_and_pg_suspension_commit` | §D.3 Part 1, §E.5.4, `HITL-EVAL-22` | **8a**: Crash after Phase B checkpoint save left `SUSPENDING_FOR_USER` + valid `__interrupt__` checkpoint -> reconciler rolled forward to `WAITING_FOR_USER` + `PENDING_USER` and W2 resumed cleanly to `COMPLETED`. **8b**: Injected `put_writes(__interrupt__)` crash after Phase A -> reconciler marked `CREATED` interaction `FAILED`, purged partial checkpoint rows (`0` remaining), and restored `QUEUED` without stranding in `WAITING_FOR_USER`. | **PASS** |
| **9** | Crash after interaction resolution before resume processing | `test_exp09_crash_after_interaction_resolution_before_resume` | §D.3 Part 2, `HITL-EVAL-24` | `RESOLVED` + `QUEUED` + `PENDING` `RESUME_TASK` committed atomically; simulated dispatch loss recovered via `CrashReconciler.reconcile_outbox_and_stale_leases()` -> W2 claimed (`claim_epoch=1`) and completed. | **PASS** |
| **10** | Bounded worker leases, `claim_epoch` fencing & stale worker rejection | `test_exp10_bounded_worker_leases_claim_epoch_fencing` | §C.2 Gate 5, §E.5.4, `HITL-EVAL-25` | W1 claimed `claim_epoch=1` with `0.15s` TTL and stalled; reconciler reclaimed for W2 at `claim_epoch=2`. Waking zombie W1 (`claim_epoch=1`) was rejected on heartbeat (`renew_worker_lease`), `MOD-08` dispatch (`0` external calls), and `complete_task_turn` (`StaleClaimEpochFencedError` + `STALE_CLAIM_EPOCH_FENCED` audit); W2 completed cleanly. | **PASS** |
| **11** | Crashes after worker claims a resume event | `test_exp11_crashes_after_worker_claims_resume_event` | §D.3 Part 2, §E.5.4, `HITL-EVAL-25` | **11a**: Mid-turn crash retained `resume_token_id`; W2 reclaimed `claim_epoch=2` and completed. **11b**: Crash after `task_lifecycle_processed_events` + `COMPLETED` commit before outbox ack -> reconciler set `outbox.status = 'PROCESSED'` with `acked_already_processed=1` and `0` re-runs. **11c**: `attempt_count >= max_attempts` moved outbox to `DEAD_LETTERED` and task to `FAILED` (`RESUME_RETRIES_EXHAUSTED`). | **PASS** |
| **12** | External action recovery with durable receipt & uncertain outcome | `test_exp12_external_action_recovery_and_unknown_outcome` | §C.2 Gate 2/5, §E.5.4, `HITL-EVAL-26` | Verified byte-for-byte invariance of `execution_id`, `logical_step_id`, and `action_idempotency_key` across W1 crash & W2 replacement (`DISPATCHING -> UNKNOWN_OUTCOME`). **12b (`PROVIDER_IDEMPOTENCY_KEY`)**: W2 re-sent identical key, provider deduplicated (`1` call). **12c (`RECONCILABLE_READ_PROBE`)**: probe verified execution, reconciled `CONFIRMED_SUCCESS` (`1` call). **12d (`NON_IDEMPOTENT_NO_RECONCILE`)**: refused blind retry (`UncertainExecutionEscalation`), suspended to `WAITING_FOR_USER` (`UNCERTAIN_EXECUTION_RECONCILIATION`), and human `CONFIRM_ALREADY_EXECUTED` resumed on W3 to `COMPLETED` (`1` call). | **PASS** |
| **13** | Expired, cancelled, or superseded task cannot resume | `test_exp13_expired_cancelled_or_superseded_cannot_resume` | §C.2, §D.10, §E.5.4, `HITL-EVAL-09`, `11`, `16`, `17` | **13a**: Elapsed `expires_at` cascaded `EXPIRED` to task + interaction and rejected late reply. **13b**: `cancel_task` cascaded `CANCELLED`; mid-HTTP cancel race recorded `CANCELLED_AFTER_EXTERNAL_COMMIT` honestly without false rollback. **13c**: Bumped/cancelled `orbi_automation.automation_definitions` (`generation`) rejected both late HITL response (`StaleAutomationGenerationRejectedError`) and queued `RESUME_TASK` outbox claim (`cancelled_stale >= 1`). | **PASS** |

---

## 4. Latency Measurements (PostgreSQL 16 Docker)

Captured across **10 consecutive iterations** per stage in `test_exp14_latency_measurements` ([benchmark_results.json](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/benchmark_results.json)), using real `psycopg` connections against `orbi-eval04-postgres` (`127.0.0.1:54328`) with fresh `Worker` and `OrbiPostgresSaver` instances per turn:

| Operation Measured | N | Min (ms) | p50 (ms) | Mean (ms) | p95 (ms) | Max (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Simple Task Execution + Checkpoint Persistence** *(claim + LangGraph `StateGraph` + `PostgresSaver` writes + PG completion)* | 10 | 212.294 | **221.175** | 228.324 | **258.979** | 258.979 |
| **Suspension (`Phase A + Phase B + Phase C`)** *(claim + Phase A PG + `interrupt()` checkpoint + read-back verify + Phase C PG + outbox)* | 10 | 154.313 | **171.708** | 181.845 | **233.113** | 233.113 |
| **Resumption (`Phase D + Worker 2 Completion`)** *(3-step `interaction_version` CAS + `RESUME_TASK` outbox + CAS claim + fresh W2 checkpoint rehydrate & commit)* | 10 | 242.339 | **254.586** | 265.528 | **300.431** | 300.431 |
| **Crash Recovery (`CrashReconciler` Roll-Forward)** *(scan `SUSPENDING_FOR_USER` + verify `__interrupt__` checkpoint + Phase C PG commit)* | 10 | 75.428 | **91.769** | 91.577 | **119.755** | 119.755 |

*Note*: These measurements isolate orchestration, LangGraph checkpointing, and PostgreSQL ACID/outbox round-trips without external LLM API latency. Each worker created a dedicated TCP connection per turn rather than borrowing from a warm `psycopg_pool.ConnectionPool`, so pooled production workers in `orbi-core` are expected to achieve lower tail latencies.

---

## 5. Discovered Limitations & Architectural Implications

During prototype execution against real `langgraph==1.2.22` and `langgraph-checkpoint-postgres==3.2.1`, six concrete behaviors and edge cases surfaced that directly inform Phase 3 LLD (`docs/03-lld/`) and minor HLD wording precision:

1. **LangGraph Generates `checkpoint_id` Internally (Phase A Pre-Allocation Correction)**:
   - *Finding*: HLD §E.5.4 Phase A states `MOD-03` "allocates `pending_checkpoint_id`" before LangGraph runs. In reality, `langgraph.checkpoint.postgres.PostgresSaver` expects LangGraph's Pregel engine to generate a monotonic UUIDv6 `checkpoint_id` during `StateGraph.invoke()` / `put()`.
   - *Implication*: Keep `pending_checkpoint_id = NULL` in Phase A (`SUSPENDING_FOR_USER`), persist the generated UUIDv6 `checkpoint_id` into `orbi_app.tasks.pending_checkpoint_id` during Phase B immediately upon `interrupt()` read-verification, and ensure `CrashReconciler.reconcile_incomplete_suspensions` falls back to scanning `langgraph_checkpoints` for the latest `__interrupt__` checkpoint for `thread_id = str(task_id)` if a worker crashes right after `put_writes()` and before writing `pending_checkpoint_id` to `orbi_app.tasks`.

2. **Two-Table Checkpoint Verification (`checkpoints` + `checkpoint_writes` `__interrupt__`)**:
   - *Finding*: Calling `langgraph.types.interrupt()` splits persistence across `langgraph_checkpoints.checkpoints` (`put()`) and `langgraph_checkpoints.checkpoint_writes` (`put_writes()` with `channel = '__interrupt__'`). If a crash interrupts `put_writes()`, a `checkpoints` row exists in PostgreSQL *without* an `__interrupt__` entry and cannot resume cleanly.
   - *Implication*: Phase B and `CrashReconciler` MUST verify both `checkpoints` and `checkpoint_writes WHERE channel = '__interrupt__'`. Checking `checkpoints` alone produces false-positive recovery roll-forwards.

3. **Node Replay-From-Top on `Command(resume=...)` & Multi-Interrupt `approval_id` Carry-Over**:
   - *Finding*: When LangGraph resumes an `interrupt()`, it **re-executes the entire interrupted node function from line 1**. Additionally, if a resumed step later suspends *a second time* inside `execute_tool_step` (observed in Experiment 12d when an `APPROVAL_REQUEST`-approved tool hit `NON_IDEMPOTENT_NO_RECONCILE` `UNKNOWN_OUTCOME` and suspended for `UNCERTAIN_EXECUTION_RECONCILIATION`), the second `RESUME_TASK` outbox payload carries the reconciliation `INFORMATION_REQUEST` rather than the original `approval_id`—causing `Gate 2`'s immutable retry identity check to fail if `approval_id` was only passed via per-turn `configurable`.
   - *Implication*:
     - Every Phase A / side-effect call preceding `interrupt()` inside a graph node must be strictly idempotent (keyed by deterministic `logical_interaction_key` in `interaction_requests`).
     - Any logical step `approval_id` must be persisted in the graph step state (or resolved from `step_execution_receipts` / `approval_requests` for that `(task_id, execution_id, logical_step_id)`) across multi-interrupt resumptions.
     - Additionally, when a resumed turn suspends into a *new* `WAITING_FOR_USER` interaction (such as `UNCERTAIN_EXECUTION_RECONCILIATION`), Phase C must mark the *prior* `RESUME_TASK` outbox row `PROCESSED` (`task_lifecycle_processed_events`) and clear the prior `resume_token_id` atomically so the old `RESUME_TASK` event is not left `RUNNING`.

4. **LangGraph 1.x Strict `RunnableConfig` Annotation Requirement**:
   - *Finding*: `langgraph==1.2.22` inspects node function type hints and silently omits `config` injection (raising `TypeError`) if a node types `config` as `dict[str, Any]` instead of `langchain_core.runnables.RunnableConfig`.
   - *Implication*: Standardize all `MOD-04` node signatures on `RunnableConfig` in LLD and add static linting/type-check coverage.

5. **`PostgresSaver` `autocommit=True` & `setup()` DDL Overhead**:
   - *Finding*: `langgraph-checkpoint-postgres` requires `autocommit=True` and `row_factory=dict_row`, physically reinforcing HLD §D.2's mandate that `orbi_app` and `langgraph_checkpoints` cannot share a single ACID transaction. Furthermore, invoking `PostgresSaver.setup()` per worker turn added ~100 ms of redundant migration queries (initially causing a `0.15s` test lease to expire mid-turn).
   - *Implication*: Run `PostgresSaver.setup()` strictly once at service startup/migration, share a warm `psycopg_pool.ConnectionPool` across worker turns, and enforce bounded production worker lease TTLs (`>= 10s–30s`) with background heartbeat renewal (`renew_worker_lease`).

6. **Parent Task TTL Expiration Must Cascade to Open `interaction_requests`**:
   - *Finding*: When a parent `task.expires_at` elapses earlier than a child `interaction.expires_at`, sweeping only `tasks` leaves child `interaction_requests` in `PENDING_USER`.
   - *Implication*: LLD TTL sweepers must atomically cascade `EXPIRED` across `tasks`, all open `interaction_requests` for that `task_id`, open `approval_requests`, and pending `task_lifecycle_outbox` rows in one transaction.

---

## 6. EVAL-04 Decision Report

### Decision: **`CONDITIONAL PASS`**

**Rationale**:
- **PASS Criteria Met**: Every architectural assumption under test—LangGraph checkpointing in isolated PostgreSQL schema `langgraph_checkpoints`, `MOD-03` authoritative task/interaction ownership in `orbi_app`, zero sleeping workers during `WAITING_FOR_USER`, cross-worker checkpoint resumption, 3-step `interaction_version` CAS, `task_lifecycle_outbox` + `task_lifecycle_processed_events` idempotency, bounded-lease `claim_epoch` fencing, crash recovery across all three crash windows (Phase B->C, Phase D->Claim, post-Claim/pre-Ack), write-ahead `step_execution_receipts` (`action_idempotency_key` invariance and `UNKNOWN_OUTCOME` escalation), and expiry/cancellation/superseded-automation gating—executed cleanly and deterministically against real PostgreSQL 16 (`12/12` pytest tests passing, `0` failures).
- **Conditions Prior to Locking Phase 3 LLD (`docs/03-lld/`)**:
  1. Clarify in HLD/LLD that LangGraph generates `checkpoint_id` during Phase B (Phase A does not pre-allocate `checkpoint_id`; `CrashReconciler` queries `langgraph_checkpoints` fallback when `pending_checkpoint_id IS NULL`).
  2. Mandate two-table checkpoint verification (`checkpoints` + `checkpoint_writes` `channel='__interrupt__'`) in Phase B and `CrashReconciler`.
  3. Mandate idempotent `logical_interaction_key` gating before `interrupt()`, multi-interrupt `approval_id` persistence, and `Phase C` outbox closure of prior `RESUME_TASK` tokens when a resumed turn enters a secondary suspension (`UNCERTAIN_EXECUTION_RECONCILIATION`).
  4. Mandate startup-once `PostgresSaver.setup()`, warm connection pooling, `RunnableConfig` typing, and task-to-interaction `EXPIRED` cascade.
  5. Note scope boundary: `MOD-09` OpenFGA was represented by a deterministic in-process `policy_version` stub and Redis transport was bypassed via direct PostgreSQL outbox claims; full containerized OpenFGA (`orbi-openfga`), Redis stream dispatch, and `SECURE_INPUT_REQUEST` / `HUMAN_TAKEOVER` / `MEMORY_CLARIFICATION_DECIDED` harnesses remain covered by Phase 1 integration & `EVAL-05` / `EVAL-08` validation suites (`HITL-EVAL-01`–`26`).
