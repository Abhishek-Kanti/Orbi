"""Core MOD-17 version-fencing, atomic claim/task creation, condition edge, and MOD-03/08 validation engine."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable
from uuid import UUID, uuid4

from eval08.canonical import (
    TriggerType,
    build_trigger_occurrence_id,
    compute_automation_idempotency_key,
    compute_tool_action_idempotency_key,
)
from eval08.db import open_connection


class AutomationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    PAUSED_SAFETY_TRIP = "PAUSED_SAFETY_TRIP"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ClaimOutcome(StrEnum):
    CLAIMED_AND_DISPATCHED = "CLAIMED_AND_DISPATCHED"
    DUPLICATE_DEDUPLICATED = "DUPLICATE_DEDUPLICATED"
    REJECTED_FENCED = "REJECTED_FENCED"
    RECOVERED_ABANDONED = "RECOVERED_ABANDONED"


class ReceiptStatus(StrEnum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE_SAFE_TO_RETRY = "CONFIRMED_FAILURE_SAFE_TO_RETRY"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class ExternalIdempotencyMode(StrEnum):
    PROVIDER_IDEMPOTENCY_KEY = "PROVIDER_IDEMPOTENCY_KEY"
    RECONCILABLE_READ_PROBE = "RECONCILABLE_READ_PROBE"
    NON_IDEMPOTENT_NO_RECONCILE = "NON_IDEMPOTENT_NO_RECONCILE"


class SimulatedCrashError(BaseException):
    """Deterministic failure injection error."""


class StaleTaskVersionError(Exception):
    """Raised when optimistic task_version CAS fails or stale approval/cancel arrives."""


class StaleClaimEpochFencedError(Exception):
    """Raised when an expired-lease worker tries to mutate a reclaimed execution."""


class UncertainOutcomeError(Exception):
    """Raised when a NON_IDEMPOTENT_NO_RECONCILE tool step hits UNKNOWN_OUTCOME."""

    def __init__(self, message: str, *, action_idempotency_key: str) -> None:
        super().__init__(message)
        self.action_idempotency_key = action_idempotency_key


@dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    idempotency_key: str
    canonical_payload: str
    execution_id: UUID | None = None
    task_id: UUID | None = None
    reason: str | None = None
    claim_epoch: int | None = None


def naive_select_then_insert_race(
    *,
    idempotency_key: str,
    worker_ids: list[str],
    dsn: str | None = None,
) -> int:
    """Demonstrate why SELECT-then-INSERT without UNIQUE constraint fails under concurrency."""
    select_barrier = threading.Barrier(len(worker_ids))
    insert_barrier = threading.Barrier(len(worker_ids))
    errors: list[BaseException] = []

    def worker_fn(wid: str) -> None:
        try:
            with open_connection(dsn) as conn:
                with conn.cursor() as cur:
                    select_barrier.wait(timeout=5.0)
                    cur.execute(
                        "SELECT 1 FROM orbi_automation.naive_race_executions WHERE idempotency_key = %s",
                        (idempotency_key,),
                    )
                    exists = cur.fetchone() is not None
                    insert_barrier.wait(timeout=5.0)
                    if not exists:
                        cur.execute(
                            """
                            INSERT INTO orbi_automation.naive_race_executions
                                (row_id, idempotency_key, worker_id)
                            VALUES (%s, %s, %s)
                            """,
                            (uuid4(), idempotency_key, wid),
                        )
                conn.commit()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker_fn, args=(wid,)) for wid in worker_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    if errors:
        raise errors[0]

    with open_connection(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS cnt FROM orbi_automation.naive_race_executions WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            return int(cur.fetchone()["cnt"])


class AutomationFencingEngine:
    """Prototype engine spanning MOD-17 fencing, MOD-03 task CAS, and MOD-08 receipts."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn

    def _audit(
        self,
        cur: Any,
        *,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        task_id: UUID | None = None,
        automation_id: UUID | None = None,
    ) -> None:
        cur.execute(
            """
            INSERT INTO orbi_app.audit_events
                (audit_event_id, task_id, automation_id, event_type, actor, details)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (uuid4(), task_id, automation_id, event_type, actor, json.dumps(details, default=str)),
        )

    def get_automation(self, automation_id: UUID) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s",
                    (automation_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise ValueError(f"Automation {automation_id} not found")
        return dict(row)

    def get_task(self, task_id: UUID) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM orbi_app.tasks WHERE task_id = %s", (task_id,))
                row = cur.fetchone()
        if row is None:
            raise ValueError(f"Task {task_id} not found")
        return dict(row)

    def get_execution(self, execution_id: UUID) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_executions WHERE execution_id = %s",
                    (execution_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise ValueError(f"Execution {execution_id} not found")
        return dict(row)

    def count_tasks_for_automation(self, automation_id: UUID) -> int:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS cnt FROM orbi_app.tasks WHERE automation_id = %s",
                    (automation_id,),
                )
                return int(cur.fetchone()["cnt"])

    def count_executions_for_automation(self, automation_id: UUID) -> int:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS cnt FROM orbi_automation.automation_executions WHERE automation_id = %s",
                    (automation_id,),
                )
                return int(cur.fetchone()["cnt"])

    def get_audit_events(self, *, automation_id: UUID | None = None, task_id: UUID | None = None) -> list[dict[str, Any]]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                if automation_id is not None:
                    cur.execute(
                        "SELECT * FROM orbi_app.audit_events WHERE automation_id = %s ORDER BY created_at ASC",
                        (automation_id,),
                    )
                elif task_id is not None:
                    cur.execute(
                        "SELECT * FROM orbi_app.audit_events WHERE task_id = %s ORDER BY created_at ASC",
                        (task_id,),
                    )
                else:
                    cur.execute("SELECT * FROM orbi_app.audit_events ORDER BY created_at ASC")
                return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Automation Lifecycle & Version Transitions (MOD-17)
    # ------------------------------------------------------------------
    def create_automation(
        self,
        *,
        tenant_id: str,
        owner_principal: str,
        creator_principal: str = "agent:planner",
        execution_target: str = "AGENT:agent:email@1.0.0",
        trigger_type: TriggerType = TriggerType.CRON,
        trigger_spec: dict[str, Any] | None = None,
        task_template: dict[str, Any] | None = None,
        automation_id: UUID | None = None,
    ) -> dict[str, Any]:
        aid = automation_id or uuid4()
        spec = trigger_spec or {"cron": "0 8 * * *"}
        tpl = task_template or {"goal": "Morning summary"}
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_definitions (
                        automation_id, tenant_id, owner_principal, creator_principal,
                        execution_target, trigger_type, trigger_spec, task_template,
                        status, schedule_version, generation
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, 1, 1)
                    RETURNING *
                    """,
                    (
                        aid,
                        tenant_id,
                        owner_principal,
                        creator_principal,
                        execution_target,
                        trigger_type.value,
                        json.dumps(spec),
                        json.dumps(tpl),
                        AutomationStatus.ACTIVE.value,
                    ),
                )
                auto = dict(cur.fetchone())
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_schedule_versions (
                        version_id, automation_id, schedule_version, generation,
                        changed_by_principal, change_reason, trigger_spec
                    )
                    VALUES (%s, %s, 1, 1, %s, 'initial_create', %s::jsonb)
                    """,
                    (uuid4(), aid, creator_principal, json.dumps(spec)),
                )
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_outbox
                        (event_id, automation_id, event_type, schedule_version, generation, payload)
                    VALUES (%s, %s, 'AUTOMATION_CREATED', 1, 1, %s::jsonb)
                    """,
                    (uuid4(), aid, json.dumps({"trigger_spec": spec})),
                )
                self._audit(
                    cur,
                    event_type="AUTOMATION_CREATED",
                    actor=creator_principal,
                    automation_id=aid,
                    details={"schedule_version": 1, "generation": 1},
                )
            conn.commit()
        return auto

    def _cascade_waiting_tasks_for_automation(self, cur: Any, automation_id: UUID) -> None:
        cur.execute(
            """
            UPDATE orbi_app.tasks
            SET status = %s,
                task_version = task_version + 1,
                cancellation_requested = TRUE,
                updated_at = now()
            WHERE automation_id = %s
              AND status IN ('QUEUED', 'WAITING_FOR_USER')
            RETURNING task_id
            """,
            (TaskStatus.CANCELLED.value, automation_id),
        )
        tids = [r["task_id"] for r in cur.fetchall()]
        if tids:
            cur.execute(
                """
                UPDATE orbi_app.interaction_requests
                SET status = 'CANCELLED', interaction_version = interaction_version + 1
                WHERE task_id = ANY(%s)
                  AND status IN ('CREATED', 'PENDING_USER', 'RESPONSE_RECEIVED', 'VALIDATING')
                """,
                (tids,),
            )
            cur.execute(
                """
                UPDATE orbi_app.approval_requests
                SET status = 'INVALIDATED'
                WHERE task_id = ANY(%s) AND status IN ('PENDING', 'APPROVED')
                """,
                (tids,),
            )

    def update_automation(
        self,
        automation_id: UUID,
        *,
        trigger_spec: dict[str, Any],
        actor: str,
        reason: str = "schedule_update",
        start_barrier: threading.Barrier | None = None,
    ) -> dict[str, Any]:
        """In-place schedule update: increments BOTH schedule_version (+1) and generation (+1)."""
        if start_barrier is not None:
            start_barrier.wait(timeout=5.0)
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                row = cur.fetchone()
                if row is None or row["status"] in (
                    AutomationStatus.CANCELLED.value,
                    AutomationStatus.DELETED.value,
                ):
                    raise ValueError("Cannot update cancelled or deleted automation")
                new_sched = row["schedule_version"] + 1
                new_gen = row["generation"] + 1
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_definitions
                    SET trigger_spec = %s::jsonb,
                        schedule_version = %s,
                        generation = %s,
                        updated_at = now()
                    WHERE automation_id = %s
                    RETURNING *
                    """,
                    (json.dumps(trigger_spec), new_sched, new_gen, automation_id),
                )
                updated = dict(cur.fetchone())
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_schedule_versions (
                        version_id, automation_id, schedule_version, generation,
                        changed_by_principal, change_reason, trigger_spec
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (uuid4(), automation_id, new_sched, new_gen, actor, reason, json.dumps(trigger_spec)),
                )
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_outbox
                        (event_id, automation_id, event_type, schedule_version, generation, payload)
                    VALUES (%s, %s, 'AUTOMATION_UPDATED', %s, %s, %s::jsonb)
                    """,
                    (uuid4(), automation_id, new_sched, new_gen, json.dumps({"reason": reason})),
                )
                self._cascade_waiting_tasks_for_automation(cur, automation_id)
                self._audit(
                    cur,
                    event_type="AUTOMATION_UPDATED",
                    actor=actor,
                    automation_id=automation_id,
                    details={"schedule_version": new_sched, "generation": new_gen, "reason": reason},
                )
            conn.commit()
        return updated

    def pause_automation(self, automation_id: UUID, *, actor: str) -> dict[str, Any]:
        """PAUSE: increments generation (+1), keeps schedule_version unchanged."""
        return self._transition_lifecycle(
            automation_id, new_status=AutomationStatus.PAUSED, actor=actor, event_type="AUTOMATION_PAUSED"
        )

    def resume_automation(self, automation_id: UUID, *, actor: str) -> dict[str, Any]:
        """RESUME: increments generation (+1), keeps schedule_version unchanged."""
        return self._transition_lifecycle(
            automation_id, new_status=AutomationStatus.ACTIVE, actor=actor, event_type="AUTOMATION_RESUMED"
        )

    def cancel_automation(self, automation_id: UUID, *, actor: str) -> dict[str, Any]:
        """CANCEL: increments generation (+1), keeps schedule_version unchanged."""
        return self._transition_lifecycle(
            automation_id, new_status=AutomationStatus.CANCELLED, actor=actor, event_type="AUTOMATION_CANCELLED"
        )

    def _transition_lifecycle(
        self,
        automation_id: UUID,
        *,
        new_status: AutomationStatus,
        actor: str,
        event_type: str,
    ) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Automation {automation_id} not found")
                new_gen = row["generation"] + 1
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_definitions
                    SET status = %s,
                        generation = %s,
                        updated_at = now()
                    WHERE automation_id = %s
                    RETURNING *
                    """,
                    (new_status.value, new_gen, automation_id),
                )
                updated = dict(cur.fetchone())
                if new_status in (AutomationStatus.PAUSED, AutomationStatus.CANCELLED):
                    self._cascade_waiting_tasks_for_automation(cur, automation_id)
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_outbox
                        (event_id, automation_id, event_type, schedule_version, generation, payload)
                    VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
                    """,
                    (uuid4(), automation_id, event_type, row["schedule_version"], new_gen),
                )
                self._audit(
                    cur,
                    event_type=event_type,
                    actor=actor,
                    automation_id=automation_id,
                    details={"schedule_version": row["schedule_version"], "generation": new_gen},
                )
            conn.commit()
        return updated

    def delete_automation_6step(self, automation_id: UUID, *, actor: str) -> dict[str, Any]:
        """Authoritative 6-step DELETE ordering (DOC-HLD-002 Section D.10)."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Automation {automation_id} not found")
                new_gen = row["generation"] + 1
                # Steps 1-4: DELETED + next_run_at NULL + generation += 1 + payload scrub + outbox
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_definitions
                    SET status = %s,
                        next_run_at = NULL,
                        generation = %s,
                        trigger_spec = '{}'::jsonb,
                        task_template = '{}'::jsonb,
                        updated_at = now()
                    WHERE automation_id = %s
                    RETURNING *
                    """,
                    (AutomationStatus.DELETED.value, new_gen, automation_id),
                )
                deleted = dict(cur.fetchone())
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_outbox
                        (event_id, automation_id, event_type, schedule_version, generation, payload)
                    VALUES (%s, %s, 'AUTOMATION_DELETED_CLEANUP', %s, %s, '{}'::jsonb)
                    """,
                    (uuid4(), automation_id, row["schedule_version"], new_gen),
                )
                # Step 5: Reconcile/cascade pending/waiting tasks & HITL
                self._cascade_waiting_tasks_for_automation(cur, automation_id)
                # Step 6: Retain redacted MOD-15 proof & non-sensitive execution ledger
                self._audit(
                    cur,
                    event_type="AUTOMATION_DELETED",
                    actor=actor,
                    automation_id=automation_id,
                    details={"schedule_version": row["schedule_version"], "generation": new_gen},
                )
            conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Atomic Version-Fenced Claim & Task Creation (MOD-17 -> MOD-03)
    # ------------------------------------------------------------------
    def claim_and_dispatch_occurrence(
        self,
        *,
        tenant_id: str,
        automation_id: UUID,
        schedule_version: int,
        generation: int,
        trigger_type: TriggerType,
        trigger_occurrence_id: str,
        worker_id: str,
        goal: str = "Run scheduled task",
        crash_after_claim_before_task_in_tx: bool = False,
        start_barrier: threading.Barrier | None = None,
    ) -> ClaimResult:
        """Single-PG-transaction version fencing + UNIQUE execution claim + MOD-03 task insert."""
        idempotency_key, canonical_payload = compute_automation_idempotency_key(
            tenant_id=tenant_id,
            automation_id=automation_id,
            schedule_version=schedule_version,
            generation=generation,
            trigger_type=trigger_type,
            trigger_occurrence_id=trigger_occurrence_id,
        )
        if start_barrier is not None:
            start_barrier.wait(timeout=5.0)

        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                # Row-lock automation_definitions so concurrent update/pause/cancel/delete serializes
                cur.execute(
                    """
                    SELECT * FROM orbi_automation.automation_definitions
                    WHERE automation_id = %s
                    FOR UPDATE
                    """,
                    (automation_id,),
                )
                auto = cur.fetchone()
                if auto is None:
                    conn.rollback()
                    return ClaimResult(
                        outcome=ClaimOutcome.REJECTED_FENCED,
                        idempotency_key=idempotency_key,
                        canonical_payload=canonical_payload,
                        reason="AUTOMATION_NOT_FOUND",
                    )

                rejection_reason: str | None = None
                if auto["tenant_id"] != tenant_id:
                    rejection_reason = "TENANT_SCOPE_MISMATCH"
                elif auto["status"] != AutomationStatus.ACTIVE.value:
                    rejection_reason = f"SKIPPED_STATUS_{auto['status']}"
                elif auto["generation"] != generation:
                    rejection_reason = "SKIPPED_STALE_GENERATION"
                elif auto["schedule_version"] != schedule_version:
                    rejection_reason = "SKIPPED_STALE_SCHEDULE_VERSION"

                if rejection_reason is not None:
                    cur.execute(
                        """
                        INSERT INTO orbi_automation.automation_processed_events
                            (event_key, automation_id, outcome)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (event_key) DO NOTHING
                        """,
                        (f"{idempotency_key}:{rejection_reason}", automation_id, rejection_reason),
                    )
                    self._audit(
                        cur,
                        event_type=rejection_reason,
                        actor=worker_id,
                        automation_id=automation_id,
                        details={
                            "event_sched": schedule_version,
                            "event_gen": generation,
                            "live_status": auto["status"],
                            "live_sched": auto["schedule_version"],
                            "live_gen": auto["generation"],
                        },
                    )
                    conn.commit()
                    return ClaimResult(
                        outcome=ClaimOutcome.REJECTED_FENCED,
                        idempotency_key=idempotency_key,
                        canonical_payload=canonical_payload,
                        reason=rejection_reason,
                    )

                new_exec_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_executions (
                        execution_id, tenant_id, automation_id, schedule_version,
                        generation, trigger_type, trigger_occurrence_id,
                        idempotency_key, status, claim_epoch, claimed_by_worker_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CLAIMING', 1, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        new_exec_id,
                        tenant_id,
                        automation_id,
                        schedule_version,
                        generation,
                        trigger_type.value,
                        trigger_occurrence_id,
                        idempotency_key,
                        worker_id,
                    ),
                )
                inserted_exec = cur.fetchone()
                if inserted_exec is None:
                    # Already claimed by another worker or prior delivery
                    cur.execute(
                        """
                        SELECT execution_id, task_id, claim_epoch
                        FROM orbi_automation.automation_executions
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
                    existing = cur.fetchone()
                    conn.commit()
                    return ClaimResult(
                        outcome=ClaimOutcome.DUPLICATE_DEDUPLICATED,
                        idempotency_key=idempotency_key,
                        canonical_payload=canonical_payload,
                        execution_id=existing["execution_id"],
                        task_id=existing["task_id"],
                        claim_epoch=existing["claim_epoch"],
                    )

                if crash_after_claim_before_task_in_tx:
                    raise SimulatedCrashError(
                        "Injected crash inside single transaction between execution claim and task creation"
                    )

                new_task_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO orbi_app.tasks (
                        task_id, tenant_id, owner_principal, execution_id,
                        automation_id, automation_generation, goal, status, task_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        new_task_id,
                        tenant_id,
                        auto["owner_principal"],
                        new_exec_id,
                        automation_id,
                        generation,
                        goal,
                        TaskStatus.QUEUED.value,
                    ),
                )
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_executions
                    SET task_id = %s, status = 'DISPATCHED', updated_at = now()
                    WHERE execution_id = %s
                    """,
                    (new_task_id, new_exec_id),
                )
                self._audit(
                    cur,
                    event_type="AUTOMATION_EXECUTION_DISPATCHED",
                    actor=worker_id,
                    task_id=new_task_id,
                    automation_id=automation_id,
                    details={
                        "execution_id": str(new_exec_id),
                        "idempotency_key": idempotency_key,
                        "schedule_version": schedule_version,
                        "generation": generation,
                    },
                )
            conn.commit()

        return ClaimResult(
            outcome=ClaimOutcome.CLAIMED_AND_DISPATCHED,
            idempotency_key=idempotency_key,
            canonical_payload=canonical_payload,
            execution_id=new_exec_id,
            task_id=new_task_id,
            claim_epoch=1,
        )

    # ------------------------------------------------------------------
    # Two-Phase Decoupled Claim & Abandoned Lease Recovery
    # ------------------------------------------------------------------
    def claim_occurrence_two_phase(
        self,
        *,
        tenant_id: str,
        automation_id: UUID,
        schedule_version: int,
        generation: int,
        trigger_type: TriggerType,
        trigger_occurrence_id: str,
        worker_id: str,
        lease_ttl_seconds: float = 0.2,
        crash_between_claim_and_task: bool = False,
        goal: str = "Two-phase task",
    ) -> ClaimResult:
        """Demonstrate recoverable two-phase claim when execution ledger commits before task insert."""
        idempotency_key, canonical_payload = compute_automation_idempotency_key(
            tenant_id=tenant_id,
            automation_id=automation_id,
            schedule_version=schedule_version,
            generation=generation,
            trigger_type=trigger_type,
            trigger_occurrence_id=trigger_occurrence_id,
        )
        # Tx 1: Validate automation & commit CLAIMED_PENDING_TASK with bounded lease
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                auto = cur.fetchone()
                if (
                    auto is None
                    or auto["status"] != AutomationStatus.ACTIVE.value
                    or auto["generation"] != generation
                    or auto["schedule_version"] != schedule_version
                ):
                    conn.rollback()
                    return ClaimResult(
                        outcome=ClaimOutcome.REJECTED_FENCED,
                        idempotency_key=idempotency_key,
                        canonical_payload=canonical_payload,
                        reason="FENCED_BEFORE_TWO_PHASE_CLAIM",
                    )

                exec_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_executions (
                        execution_id, tenant_id, automation_id, schedule_version,
                        generation, trigger_type, trigger_occurrence_id,
                        idempotency_key, status, claim_epoch, claimed_by_worker_id, lease_expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CLAIMED_PENDING_TASK', 1, %s,
                            now() + (%s * interval '1 second'))
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        exec_id,
                        tenant_id,
                        automation_id,
                        schedule_version,
                        generation,
                        trigger_type.value,
                        trigger_occurrence_id,
                        idempotency_key,
                        worker_id,
                        lease_ttl_seconds,
                    ),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        "SELECT execution_id, task_id, claim_epoch FROM orbi_automation.automation_executions WHERE idempotency_key = %s",
                        (idempotency_key,),
                    )
                    existing = cur.fetchone()
                    conn.commit()
                    return ClaimResult(
                        outcome=ClaimOutcome.DUPLICATE_DEDUPLICATED,
                        idempotency_key=idempotency_key,
                        canonical_payload=canonical_payload,
                        execution_id=existing["execution_id"],
                        task_id=existing["task_id"],
                        claim_epoch=existing["claim_epoch"],
                    )
                owner_principal = auto["owner_principal"]
            conn.commit()

        if crash_between_claim_and_task:
            raise SimulatedCrashError(
                "Injected crash after Tx 1 CLAIMED_PENDING_TASK commit before Tx 2 task creation"
            )

        # Tx 2: Create task fenced on (execution_id, claim_epoch=1, worker_id)
        task_id = self._finalize_claimed_execution_task(
            execution_id=exec_id,
            worker_id=worker_id,
            expected_claim_epoch=1,
            owner_principal=owner_principal,
            goal=goal,
        )
        return ClaimResult(
            outcome=ClaimOutcome.CLAIMED_AND_DISPATCHED,
            idempotency_key=idempotency_key,
            canonical_payload=canonical_payload,
            execution_id=exec_id,
            task_id=task_id,
            claim_epoch=1,
        )

    def _finalize_claimed_execution_task(
        self,
        *,
        execution_id: UUID,
        worker_id: str,
        expected_claim_epoch: int,
        owner_principal: str,
        goal: str,
    ) -> UUID:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM orbi_automation.automation_executions
                    WHERE execution_id = %s
                    FOR UPDATE
                    """,
                    (execution_id,),
                )
                ex = cur.fetchone()
                if (
                    ex is None
                    or ex["status"] != "CLAIMED_PENDING_TASK"
                    or ex["claim_epoch"] != expected_claim_epoch
                    or ex["claimed_by_worker_id"] != worker_id
                ):
                    conn.rollback()
                    raise StaleClaimEpochFencedError(
                        f"Stale worker {worker_id} (epoch={expected_claim_epoch}) fenced on execution {execution_id}"
                    )

                new_task_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO orbi_app.tasks (
                        task_id, tenant_id, owner_principal, execution_id,
                        automation_id, automation_generation, goal, status, task_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    ON CONFLICT (execution_id) DO NOTHING
                    RETURNING task_id
                    """,
                    (
                        new_task_id,
                        ex["tenant_id"],
                        owner_principal,
                        execution_id,
                        ex["automation_id"],
                        ex["generation"],
                        goal,
                        TaskStatus.QUEUED.value,
                    ),
                )
                t_row = cur.fetchone()
                if t_row is None:
                    cur.execute(
                        "SELECT task_id FROM orbi_app.tasks WHERE execution_id = %s",
                        (execution_id,),
                    )
                    actual_task_id = cur.fetchone()["task_id"]
                else:
                    actual_task_id = t_row["task_id"]

                cur.execute(
                    """
                    UPDATE orbi_automation.automation_executions
                    SET task_id = %s, status = 'DISPATCHED', updated_at = now()
                    WHERE execution_id = %s
                    """,
                    (actual_task_id, execution_id),
                )
            conn.commit()
        return actual_task_id

    def stale_worker_finalize_claimed_execution(
        self,
        *,
        execution_id: UUID,
        stale_worker_id: str,
        stale_claim_epoch: int,
        owner_principal: str = "user:owner",
    ) -> UUID:
        """Attempt task creation with an expired/fenced claim_epoch -> raises StaleClaimEpochFencedError."""
        return self._finalize_claimed_execution_task(
            execution_id=execution_id,
            worker_id=stale_worker_id,
            expected_claim_epoch=stale_claim_epoch,
            owner_principal=owner_principal,
            goal="Stale task attempt",
        )

    def recover_abandoned_executions(
        self, *, recovery_worker_id: str, goal: str = "Recovered task"
    ) -> list[ClaimResult]:
        """Reclaim expired CLAIMED_PENDING_TASK rows, bump claim_epoch, revalidate version, and materialize task."""
        recovered: list[ClaimResult] = []
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM orbi_automation.automation_executions
                    WHERE status = 'CLAIMED_PENDING_TASK'
                      AND (lease_expires_at IS NULL OR lease_expires_at < now())
                    FOR UPDATE SKIP LOCKED
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
                for ex in rows:
                    cur.execute(
                        "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                        (ex["automation_id"],),
                    )
                    auto = cur.fetchone()
                    if (
                        auto is None
                        or auto["status"] != AutomationStatus.ACTIVE.value
                        or auto["generation"] != ex["generation"]
                        or auto["schedule_version"] != ex["schedule_version"]
                    ):
                        cur.execute(
                            """
                            UPDATE orbi_automation.automation_executions
                            SET status = 'ABORTED_SUPERSEDED',
                                claim_epoch = claim_epoch + 1,
                                claimed_by_worker_id = %s,
                                updated_at = now()
                            WHERE execution_id = %s
                            """,
                            (recovery_worker_id, ex["execution_id"]),
                        )
                        recovered.append(
                            ClaimResult(
                                outcome=ClaimOutcome.REJECTED_FENCED,
                                idempotency_key=ex["idempotency_key"],
                                canonical_payload="",
                                execution_id=ex["execution_id"],
                                reason="ABORTED_SUPERSEDED_DURING_RECOVERY",
                            )
                        )
                        continue

                    new_epoch = ex["claim_epoch"] + 1
                    cur.execute(
                        """
                        UPDATE orbi_automation.automation_executions
                        SET claim_epoch = %s,
                            claimed_by_worker_id = %s,
                            lease_expires_at = now() + interval '30 seconds',
                            updated_at = now()
                        WHERE execution_id = %s
                          AND claim_epoch = %s
                          AND status = 'CLAIMED_PENDING_TASK'
                        """,
                        (new_epoch, recovery_worker_id, ex["execution_id"], ex["claim_epoch"]),
                    )
                    if cur.rowcount != 1:
                        continue

                    new_task_id = uuid4()
                    cur.execute(
                        """
                        INSERT INTO orbi_app.tasks (
                            task_id, tenant_id, owner_principal, execution_id,
                            automation_id, automation_generation, goal, status, task_version
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                        ON CONFLICT (execution_id) DO NOTHING
                        RETURNING task_id
                        """,
                        (
                            new_task_id,
                            ex["tenant_id"],
                            auto["owner_principal"],
                            ex["execution_id"],
                            ex["automation_id"],
                            ex["generation"],
                            goal,
                            TaskStatus.QUEUED.value,
                        ),
                    )
                    t_row = cur.fetchone()
                    if t_row is not None:
                        actual_tid = t_row["task_id"]
                    else:
                        cur.execute(
                            "SELECT task_id FROM orbi_app.tasks WHERE execution_id = %s",
                            (ex["execution_id"],),
                        )
                        actual_tid = cur.fetchone()["task_id"]
                    cur.execute(
                        """
                        UPDATE orbi_automation.automation_executions
                        SET task_id = %s, status = 'DISPATCHED', updated_at = now()
                        WHERE execution_id = %s
                        """,
                        (actual_tid, ex["execution_id"]),
                    )
                    self._audit(
                        cur,
                        event_type="ABANDONED_EXECUTION_RECOVERED",
                        actor=recovery_worker_id,
                        task_id=actual_tid,
                        automation_id=ex["automation_id"],
                        details={"execution_id": str(ex["execution_id"]), "claim_epoch": new_epoch},
                    )
                    recovered.append(
                        ClaimResult(
                            outcome=ClaimOutcome.RECOVERED_ABANDONED,
                            idempotency_key=ex["idempotency_key"],
                            canonical_payload="",
                            execution_id=ex["execution_id"],
                            task_id=actual_tid,
                            claim_epoch=new_epoch,
                        )
                    )
            conn.commit()
        return recovered

    # ------------------------------------------------------------------
    # Condition Rising-Edge Evaluator
    # ------------------------------------------------------------------
    def evaluate_condition_poll(
        self,
        *,
        monitor_id: str,
        tenant_id: str,
        automation_id: UUID,
        condition_value: bool,
        worker_id: str = "cond-evaluator",
    ) -> ClaimResult | None:
        """Fire ONLY on rising edge FALSE -> TRUE; sustained TRUE creates zero new occurrences."""
        rising_edge_seq: int | None = None
        sched_ver: int = 1
        gen: int = 1

        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                auto = cur.fetchone()
                if auto is None or auto["status"] != AutomationStatus.ACTIVE.value:
                    conn.rollback()
                    return None
                sched_ver = auto["schedule_version"]
                gen = auto["generation"]

                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_condition_monitors
                        (monitor_id, automation_id, last_state, rising_edge_seq, poll_count)
                    VALUES (%s, %s, FALSE, 0, 0)
                    ON CONFLICT (monitor_id) DO NOTHING
                    """,
                    (monitor_id, automation_id),
                )
                cur.execute(
                    """
                    SELECT * FROM orbi_automation.automation_condition_monitors
                    WHERE monitor_id = %s
                    FOR UPDATE
                    """,
                    (monitor_id,),
                )
                mon = cur.fetchone()
                was_true = bool(mon["last_state"])
                seq = int(mon["rising_edge_seq"])
                if not was_true and condition_value:
                    seq += 1
                    rising_edge_seq = seq
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_condition_monitors
                    SET last_state = %s,
                        rising_edge_seq = %s,
                        poll_count = poll_count + 1,
                        updated_at = now()
                    WHERE monitor_id = %s
                    """,
                    (condition_value, seq, monitor_id),
                )
            conn.commit()

        if rising_edge_seq is None:
            return None

        occ_id = build_trigger_occurrence_id(
            TriggerType.CONDITION, monitor_id=monitor_id, rising_edge_seq=rising_edge_seq
        )
        return self.claim_and_dispatch_occurrence(
            tenant_id=tenant_id,
            automation_id=automation_id,
            schedule_version=sched_ver,
            generation=gen,
            trigger_type=TriggerType.CONDITION,
            trigger_occurrence_id=occ_id,
            worker_id=worker_id,
            goal=f"Condition rising edge #{rising_edge_seq}",
        )

    # ------------------------------------------------------------------
    # Task-Level Optimistic Concurrency (task_version CAS)
    # ------------------------------------------------------------------
    def cas_transition_task(
        self,
        *,
        task_id: UUID,
        expected_task_version: int,
        expected_status: TaskStatus,
        new_status: TaskStatus,
        actor: str,
        start_barrier: threading.Barrier | None = None,
    ) -> dict[str, Any]:
        """Atomic task_version CAS transition (DOC-HLD-002 / MOD-03)."""
        if start_barrier is not None:
            start_barrier.wait(timeout=5.0)
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = task_version + 1,
                        cancellation_requested = CASE
                            WHEN %s = 'CANCELLED' THEN TRUE
                            ELSE cancellation_requested
                        END,
                        updated_at = now()
                    WHERE task_id = %s
                      AND task_version = %s
                      AND status = %s
                    RETURNING *
                    """,
                    (
                        new_status.value,
                        new_status.value,
                        task_id,
                        expected_task_version,
                        expected_status.value,
                    ),
                )
                updated = cur.fetchone()
                if updated is None:
                    conn.rollback()
                    raise StaleTaskVersionError(
                        f"Task CAS failed for {task_id}: expected v={expected_task_version}, status={expected_status.value}"
                    )
                self._audit(
                    cur,
                    event_type=f"TASK_CAS_{expected_status.value}_TO_{new_status.value}",
                    actor=actor,
                    task_id=task_id,
                    details={"new_task_version": updated["task_version"]},
                )
            conn.commit()
        return dict(updated)

    def create_waiting_approval(
        self, *, task_id: UUID, expected_task_version: int, actor: str = "mod03"
    ) -> tuple[UUID, UUID, int]:
        """Pause task RUNNING -> WAITING_FOR_USER (task_version + 1) and bind ApprovalRequest."""
        interaction_id = uuid4()
        approval_id = uuid4()
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = task_version + 1,
                        updated_at = now()
                    WHERE task_id = %s AND task_version = %s AND status = %s
                    RETURNING execution_id, automation_id, automation_generation, task_version
                    """,
                    (
                        TaskStatus.WAITING_FOR_USER.value,
                        task_id,
                        expected_task_version,
                        TaskStatus.RUNNING.value,
                    ),
                )
                t_row = cur.fetchone()
                if t_row is None:
                    conn.rollback()
                    raise StaleTaskVersionError("Task version/status mismatch creating approval")
                new_ver = int(t_row["task_version"])
                cur.execute(
                    """
                    INSERT INTO orbi_app.interaction_requests (
                        interaction_id, task_id, task_version, execution_id,
                        automation_id, automation_generation, interaction_type, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'APPROVAL_REQUEST', 'PENDING_USER')
                    """,
                    (
                        interaction_id,
                        task_id,
                        new_ver,
                        t_row["execution_id"],
                        t_row["automation_id"],
                        t_row["automation_generation"],
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO orbi_app.approval_requests (
                        approval_id, interaction_id, task_id, expected_task_version, status
                    )
                    VALUES (%s, %s, %s, %s, 'PENDING')
                    """,
                    (approval_id, interaction_id, task_id, new_ver),
                )
            conn.commit()
        return interaction_id, approval_id, new_ver

    def resolve_approval_with_version_fence(
        self, *, approval_id: UUID, expected_task_version: int, actor: str
    ) -> dict[str, Any]:
        """Resolve approval only if live task_version, status, and automation generation still match."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.approval_requests WHERE approval_id = %s FOR UPDATE",
                    (approval_id,),
                )
                app = cur.fetchone()
                if app is None:
                    raise StaleTaskVersionError("Approval not found")
                cur.execute(
                    "SELECT * FROM orbi_app.tasks WHERE task_id = %s FOR UPDATE",
                    (app["task_id"],),
                )
                task = cur.fetchone()
                if (
                    task["status"] != TaskStatus.WAITING_FOR_USER.value
                    or task["cancellation_requested"]
                    or task["task_version"] != expected_task_version
                    or task["task_version"] != app["expected_task_version"]
                    or app["status"] != "PENDING"
                ):
                    conn.rollback()
                    raise StaleTaskVersionError(
                        f"Stale approval rejected: task status={task['status']}, task_version={task['task_version']}, expected={expected_task_version}"
                    )

                if task["automation_id"] is not None:
                    cur.execute(
                        "SELECT status, generation FROM orbi_automation.automation_definitions WHERE automation_id = %s",
                        (task["automation_id"],),
                    )
                    auto = cur.fetchone()
                    if (
                        auto is None
                        or auto["status"] != AutomationStatus.ACTIVE.value
                        or auto["generation"] != task["automation_generation"]
                    ):
                        conn.rollback()
                        raise StaleTaskVersionError("Automation inactive or generation superseded")

                cur.execute(
                    "UPDATE orbi_app.approval_requests SET status = 'APPROVED', decided_at = now() WHERE approval_id = %s",
                    (approval_id,),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = 'RESOLVED', interaction_version = interaction_version + 1
                    WHERE interaction_id = %s
                    """,
                    (app["interaction_id"],),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s, task_version = task_version + 1, updated_at = now()
                    WHERE task_id = %s AND task_version = %s
                    RETURNING *
                    """,
                    (TaskStatus.QUEUED.value, task["task_id"], expected_task_version),
                )
                updated_task = dict(cur.fetchone())
            conn.commit()
        return updated_task

    # ------------------------------------------------------------------
    # Two-Layer Tool Step Idempotency (MOD-08 Gate 2)
    # ------------------------------------------------------------------
    def execute_tool_step_with_receipt(
        self,
        *,
        tenant_id: str,
        task_id: UUID,
        execution_id: UUID,
        logical_step_id: str,
        tool_id: str,
        tool_version: str,
        arguments: dict[str, Any],
        target_resource: str,
        mode: ExternalIdempotencyMode,
        provider_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
        probe_fn: Callable[[str], dict[str, Any]] | None = None,
        crash_after_provider_before_confirm: bool = False,
    ) -> dict[str, Any]:
        """Validate MOD-08 step_execution_receipts separately from MOD-17 occurrence dedup."""
        action_key = compute_tool_action_idempotency_key(
            tenant_id=tenant_id,
            task_id=task_id,
            execution_id=execution_id,
            logical_step_id=logical_step_id,
            tool_id=tool_id,
            tool_version=tool_version,
            arguments=arguments,
            target_resource=target_resource,
        )
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM orbi_app.step_execution_receipts
                    WHERE task_id = %s AND execution_id = %s AND logical_step_id = %s
                    FOR UPDATE
                    """,
                    (task_id, execution_id, logical_step_id),
                )
                rec = cur.fetchone()
                if rec is None:
                    cur.execute(
                        """
                        INSERT INTO orbi_app.step_execution_receipts (
                            receipt_id, tenant_id, task_id, execution_id, logical_step_id,
                            tool_id, tool_version, target_resource, action_idempotency_key,
                            external_idempotency_mode, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            uuid4(),
                            tenant_id,
                            task_id,
                            execution_id,
                            logical_step_id,
                            tool_id,
                            tool_version,
                            target_resource,
                            action_key,
                            mode.value,
                            ReceiptStatus.PREPARED.value,
                        ),
                    )
                    rec = cur.fetchone()
                status = ReceiptStatus(rec["status"])
                if status == ReceiptStatus.CONFIRMED_SUCCESS:
                    conn.commit()
                    return {"replayed": True, "output": rec["sanitized_output"]}
                if status == ReceiptStatus.DISPATCHING:
                    cur.execute(
                        "UPDATE orbi_app.step_execution_receipts SET status = %s, updated_at = now() WHERE receipt_id = %s",
                        (ReceiptStatus.UNKNOWN_OUTCOME.value, rec["receipt_id"]),
                    )
                    status = ReceiptStatus.UNKNOWN_OUTCOME
                receipt_id = rec["receipt_id"]
            conn.commit()

        if status == ReceiptStatus.UNKNOWN_OUTCOME:
            if mode == ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE:
                raise UncertainOutcomeError(
                    "Blind retry prohibited for NON_IDEMPOTENT_NO_RECONCILE step in UNKNOWN_OUTCOME",
                    action_idempotency_key=action_key,
                )
            if mode == ExternalIdempotencyMode.RECONCILABLE_READ_PROBE:
                assert probe_fn is not None
                probe_res = probe_fn(action_key)
                if probe_res.get("executed") is True:
                    with open_connection(self.dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE orbi_app.step_execution_receipts
                                SET status = %s, sanitized_output = %s::jsonb, updated_at = now()
                                WHERE receipt_id = %s
                                """,
                                (
                                    ReceiptStatus.CONFIRMED_SUCCESS.value,
                                    json.dumps(probe_res["output"]),
                                    receipt_id,
                                ),
                            )
                        conn.commit()
                    return {"reconciled": True, "output": probe_res["output"]}

        # Write-ahead DISPATCHING commit before external provider invocation
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE orbi_app.step_execution_receipts SET status = %s, updated_at = now() WHERE receipt_id = %s",
                    (ReceiptStatus.DISPATCHING.value, receipt_id),
                )
            conn.commit()

        out = provider_fn(action_key, arguments)
        if crash_after_provider_before_confirm:
            raise SimulatedCrashError(
                "Injected crash after provider call before CONFIRMED_SUCCESS receipt commit"
            )

        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.step_execution_receipts
                    SET status = %s, provider_reference_id = %s, sanitized_output = %s::jsonb, updated_at = now()
                    WHERE receipt_id = %s
                    """,
                    (
                        ReceiptStatus.CONFIRMED_SUCCESS.value,
                        str(out.get("ref", "ok")),
                        json.dumps(out),
                        receipt_id,
                    ),
                )
            conn.commit()
        return {"replayed": False, "output": out}
