"""MOD-03 Deterministic Task Manager, HumanInteractionManager, and Three-Part CrashReconciler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jsonschema

from eval04.checkpointer import OrbiPostgresSaver
from eval04.db import open_connection
from eval04.gateway import (
    DEFAULT_HMAC_KEY,
    compute_approval_binding_digest,
    compute_approval_hmac,
    compute_canonical_arguments_hash,
)
from eval04.models import (
    TERMINAL_TASK_STATUSES,
    ApprovalInvalidatedError,
    ApprovalStatus,
    AuthzDeniedError,
    AutomationStatus,
    Eval04Error,
    InMemoryPolicyEngine,
    InteractionConflictError,
    InteractionStatus,
    InteractionSubmissionResult,
    InteractionType,
    OutboxEventType,
    OutboxStatus,
    ReceiptStatus,
    SimulatedCrashError,
    StaleAutomationGenerationRejectedError,
    StaleClaimEpochFencedError,
    StaleInteractionRejectedError,
    TaskNotResumableError,
    TaskRecord,
    TaskStatus,
    WaitingReason,
)


@dataclass(frozen=True)
class ClaimedResumeEvent:
    """Result of a single-winner RESUME_TASK bounded-lease claim."""

    event_id: UUID
    task_id: UUID
    execution_id: UUID
    interaction_id: UUID | None
    resume_token_id: UUID
    claim_epoch: int
    expected_task_version: int
    verified_checkpoint_id: str
    payload: dict[str, Any]


def _row_to_task_record(row: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        task_id=row["task_id"],
        tenant_id=row["tenant_id"],
        owner_principal=row["owner_principal"],
        execution_id=row["execution_id"],
        goal=row["goal"],
        status=TaskStatus(row["status"]),
        task_version=row["task_version"],
        waiting_reason=row["waiting_reason"],
        pending_checkpoint_id=row["pending_checkpoint_id"],
        verified_checkpoint_id=row["verified_checkpoint_id"],
        resume_token_id=row["resume_token_id"],
        claim_epoch=row["claim_epoch"],
        claimed_by_worker_id=row["claimed_by_worker_id"],
        lease_expires_at=row["lease_expires_at"],
        cancellation_requested=row["cancellation_requested"],
        automation_id=row["automation_id"],
        automation_generation=row["automation_generation"],
        expires_at=row["expires_at"],
        result_payload=row["result_payload"],
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class DeterministicTaskManager:
    """MOD-03 Task Manager owning authoritative PostgreSQL state and HumanInteractionManager."""

    def __init__(
        self,
        *,
        policy_engine: InMemoryPolicyEngine,
        dsn: str | None = None,
        hmac_key: bytes = DEFAULT_HMAC_KEY,
    ) -> None:
        self.policy_engine = policy_engine
        self.dsn = dsn
        self.hmac_key = hmac_key

    def _audit(
        self,
        cur: Any,
        *,
        task_id: UUID | None,
        interaction_id: UUID | None,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        cur.execute(
            """
            INSERT INTO orbi_app.audit_events
                (audit_event_id, task_id, interaction_id, event_type, actor, details)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                uuid4(),
                task_id,
                interaction_id,
                event_type,
                actor,
                json.dumps(details, default=str),
            ),
        )

    def _standalone_audit(
        self,
        *,
        task_id: UUID | None,
        interaction_id: UUID | None,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type=event_type,
                    actor=actor,
                    details=details,
                )
            conn.commit()

    def get_task(self, task_id: UUID) -> TaskRecord:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM orbi_app.tasks WHERE task_id = %s", (task_id,))
                row = cur.fetchone()
        if row is None:
            raise Eval04Error(f"Task {task_id} not found")
        return _row_to_task_record(row)

    def get_interaction(self, interaction_id: UUID) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.interaction_requests WHERE interaction_id = %s",
                    (interaction_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise Eval04Error(f"Interaction {interaction_id} not found")
        return dict(row)

    def get_approval(self, approval_id: UUID) -> dict[str, Any]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.approval_requests WHERE approval_id = %s",
                    (approval_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise Eval04Error(f"Approval {approval_id} not found")
        return dict(row)

    def get_outbox_events(
        self, task_id: UUID, event_type: OutboxEventType | None = None
    ) -> list[dict[str, Any]]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                if event_type is None:
                    cur.execute(
                        "SELECT * FROM orbi_app.task_lifecycle_outbox WHERE task_id = %s ORDER BY created_at ASC",
                        (task_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM orbi_app.task_lifecycle_outbox
                        WHERE task_id = %s AND event_type = %s
                        ORDER BY created_at ASC
                        """,
                        (task_id, event_type.value),
                    )
                return [dict(r) for r in cur.fetchall()]

    def get_receipt(self, task_id: UUID, logical_step_id: str) -> dict[str, Any] | None:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM orbi_app.step_execution_receipts
                    WHERE task_id = %s AND logical_step_id = %s
                    """,
                    (task_id, logical_step_id),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def get_audit_events(self, task_id: UUID) -> list[dict[str, Any]]:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.audit_events WHERE task_id = %s ORDER BY created_at ASC",
                    (task_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def create_automation(
        self,
        *,
        tenant_id: str,
        owner_principal: str,
        automation_id: UUID | None = None,
        status: AutomationStatus = AutomationStatus.ACTIVE,
        schedule_version: int = 1,
        generation: int = 1,
    ) -> UUID:
        aid = automation_id or uuid4()
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orbi_automation.automation_definitions
                        (automation_id, tenant_id, owner_principal, status, schedule_version, generation)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (aid, tenant_id, owner_principal, status.value, schedule_version, generation),
                )
            conn.commit()
        return aid

    def update_or_cancel_automation(
        self,
        automation_id: UUID,
        *,
        new_status: AutomationStatus | None = None,
        bump_generation: bool = True,
        cascade_waiting_tasks: bool = True,
    ) -> int:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE",
                    (automation_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise Eval04Error(f"Automation {automation_id} not found")
                next_gen = row["generation"] + (1 if bump_generation else 0)
                next_status = new_status.value if new_status else row["status"]
                next_sched = row["schedule_version"] + (1 if new_status is None else 0)
                cur.execute(
                    """
                    UPDATE orbi_automation.automation_definitions
                    SET status = %s,
                        generation = %s,
                        schedule_version = %s,
                        updated_at = now()
                    WHERE automation_id = %s
                    """,
                    (next_status, next_gen, next_sched, automation_id),
                )
                if cascade_waiting_tasks:
                    cur.execute(
                        """
                        UPDATE orbi_app.tasks
                        SET status = %s,
                            task_version = task_version + 1,
                            cancellation_requested = TRUE,
                            resume_token_id = NULL,
                            updated_at = now()
                        WHERE automation_id = %s
                          AND status IN ('QUEUED', 'SUSPENDING_FOR_USER', 'WAITING_FOR_USER')
                        RETURNING task_id
                        """,
                        (TaskStatus.CANCELLED.value, automation_id),
                    )
                    affected_tasks = [r["task_id"] for r in cur.fetchall()]
                    if affected_tasks:
                        cur.execute(
                            """
                            UPDATE orbi_app.interaction_requests
                            SET status = %s, interaction_version = interaction_version + 1
                            WHERE task_id = ANY(%s)
                              AND status IN ('CREATED', 'PENDING_USER', 'TAKEOVER_ACTIVE', 'RESPONSE_RECEIVED', 'VALIDATING', 'VALIDATION_FAILED_RETRYABLE')
                            """,
                            (InteractionStatus.CANCELLED.value, affected_tasks),
                        )
                        cur.execute(
                            """
                            UPDATE orbi_app.approval_requests
                            SET status = %s
                            WHERE task_id = ANY(%s)
                              AND status IN ('PENDING', 'APPROVED')
                            """,
                            (ApprovalStatus.INVALIDATED.value, affected_tasks),
                        )
            conn.commit()
        return next_gen

    def create_task(
        self,
        *,
        tenant_id: str,
        owner_principal: str,
        goal: str,
        automation_id: UUID | None = None,
        automation_generation: int | None = None,
        expires_in_seconds: int | None = 3600,
    ) -> TaskRecord:
        task_id = uuid4()
        execution_id = uuid4()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None
            else None
        )
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orbi_app.tasks (
                        task_id, tenant_id, owner_principal, execution_id, goal,
                        status, task_version, automation_id, automation_generation, expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        task_id,
                        tenant_id,
                        owner_principal,
                        execution_id,
                        goal,
                        TaskStatus.QUEUED.value,
                        automation_id,
                        automation_generation,
                        expires_at,
                    ),
                )
                row = cur.fetchone()
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=None,
                    event_type="TASK_CREATED_QUEUED",
                    actor=owner_principal,
                    details={"execution_id": str(execution_id), "goal": goal},
                )
            conn.commit()
        return _row_to_task_record(row)

    def claim_initial_task(
        self,
        task_id: UUID,
        *,
        worker_id: str,
        lease_ttl_seconds: float = 30.0,
    ) -> TaskRecord:
        """Atomically claim a QUEUED initial task with bounded lease and claim_epoch + 1."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        claim_epoch = claim_epoch + 1,
                        claimed_by_worker_id = %s,
                        lease_expires_at = now() + (%s * interval '1 second'),
                        updated_at = now()
                    WHERE task_id = %s
                      AND status = %s
                      AND cancellation_requested = FALSE
                      AND (expires_at IS NULL OR expires_at > now())
                    RETURNING *
                    """,
                    (
                        TaskStatus.RUNNING.value,
                        worker_id,
                        lease_ttl_seconds,
                        task_id,
                        TaskStatus.QUEUED.value,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            raise TaskNotResumableError(f"Task {task_id} could not be claimed in QUEUED state")
        return _row_to_task_record(row)

    def prepare_suspension_if_needed(
        self,
        *,
        task_id: UUID,
        worker_id: str,
        claim_epoch: int,
        logical_interaction_key: str,
        interaction_type: InteractionType,
        waiting_reason: WaitingReason,
        requesting_agent: str,
        prompt_payload: dict[str, Any],
        response_schema: dict[str, Any],
        allowed_response_channels: tuple[str, ...] = ("telegram", "dashboard"),
        visibility_scope: str = "private_user",
        expires_in_seconds: int = 900,
        approval_tool_id: str | None = None,
        approval_tool_version: str | None = None,
        approval_args: dict[str, Any] | None = None,
        approval_semantic_fields: tuple[str, ...] | None = None,
        approval_target_resource: str | None = None,
    ) -> tuple[UUID, UUID | None] | None:
        """Phase A idempotent suspension prepare (no-op if this logical_interaction_key is already RESOLVED)."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT interaction_id, approval_id, status
                    FROM orbi_app.interaction_requests
                    WHERE task_id = %s
                      AND prompt_payload->>'logical_interaction_key' = %s
                      AND status IN ('CREATED', 'PENDING_USER', 'TAKEOVER_ACTIVE', 'RESPONSE_RECEIVED', 'VALIDATING', 'RESOLVED')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (task_id, logical_interaction_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if existing["status"] == InteractionStatus.RESOLVED.value:
                        return None
                    return existing["interaction_id"], existing["approval_id"]

                cur.execute(
                    """
                    SELECT *, (lease_expires_at > now()) AS lease_valid
                    FROM orbi_app.tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise TaskNotResumableError(f"Task {task_id} not found")
                if (
                    task["claim_epoch"] != claim_epoch
                    or task["claimed_by_worker_id"] != worker_id
                    or not task["lease_valid"]
                ):
                    raise StaleClaimEpochFencedError(
                        f"Stale worker {worker_id} epoch={claim_epoch} during Phase A"
                    )
                if task["status"] != TaskStatus.RUNNING.value or task["cancellation_requested"]:
                    raise TaskNotResumableError(
                        f"Cannot suspend task {task_id}: status={task['status']}, cancelled={task['cancellation_requested']}"
                    )

                interaction_id = uuid4()
                approval_id: UUID | None = None
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
                enriched_prompt = {
                    **prompt_payload,
                    "logical_interaction_key": logical_interaction_key,
                }

                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        waiting_reason = %s,
                        pending_checkpoint_id = NULL,
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (TaskStatus.SUSPENDING_FOR_USER.value, waiting_reason.value, task_id),
                )

                if interaction_type == InteractionType.APPROVAL_REQUEST:
                    assert approval_tool_id and approval_tool_version and approval_target_resource
                    approval_id = uuid4()
                    canonical_hash = compute_canonical_arguments_hash(
                        approval_args or {}, approval_semantic_fields
                    )
                    binding_digest = compute_approval_binding_digest(
                        user_id=task["owner_principal"],
                        task_id=task_id,
                        execution_id=task["execution_id"],
                        tool_id=approval_tool_id,
                        tool_version=approval_tool_version,
                        canonical_arguments_hash=canonical_hash,
                        target_resource=approval_target_resource,
                        policy_version=self.policy_engine.policy_version,
                        expires_at=expires_at,
                        automation_generation=task["automation_generation"],
                    )
                    sig = compute_approval_hmac(approval_id, binding_digest, self.hmac_key)

                cur.execute(
                    """
                    INSERT INTO orbi_app.interaction_requests (
                        interaction_id, task_id, task_version, execution_id,
                        automation_id, automation_generation, verified_checkpoint_id,
                        approval_id, interaction_type, requesting_agent, target_principal,
                        tenant_id, visibility_scope, status, allowed_response_channels,
                        prompt_payload, response_schema, interaction_version, expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, 1, %s)
                    """,
                    (
                        interaction_id,
                        task_id,
                        task["task_version"],
                        task["execution_id"],
                        task["automation_id"],
                        task["automation_generation"],
                        approval_id,
                        interaction_type.value,
                        requesting_agent,
                        task["owner_principal"],
                        task["tenant_id"],
                        visibility_scope,
                        InteractionStatus.CREATED.value,
                        list(allowed_response_channels),
                        json.dumps(enriched_prompt),
                        json.dumps(response_schema),
                        expires_at,
                    ),
                )

                if approval_id is not None:
                    cur.execute(
                        """
                        INSERT INTO orbi_app.approval_requests (
                            approval_id, interaction_id, task_id, execution_id,
                            user_id, tool_id, tool_version, canonical_arguments_hash,
                            target_resource, policy_version, binding_digest, hmac_signature,
                            status, expires_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            approval_id,
                            interaction_id,
                            task_id,
                            task["execution_id"],
                            task["owner_principal"],
                            approval_tool_id,
                            approval_tool_version,
                            canonical_hash,
                            approval_target_resource,
                            self.policy_engine.policy_version,
                            binding_digest,
                            sig,
                            ApprovalStatus.PENDING.value,
                            expires_at,
                        ),
                    )
                    self.policy_engine.grant_interaction_role(
                        task["owner_principal"], "approver", interaction_id
                    )
                else:
                    self.policy_engine.grant_interaction_role(
                        task["owner_principal"], "responder", interaction_id
                    )
                self.policy_engine.grant_interaction_role(
                    task["owner_principal"], "canceller", interaction_id
                )

                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="SUSPENSION_PHASE_A_PREPARED",
                    actor=worker_id,
                    details={
                        "interaction_type": interaction_type.value,
                        "waiting_reason": waiting_reason.value,
                        "approval_id": str(approval_id) if approval_id else None,
                    },
                )
            conn.commit()
        return interaction_id, approval_id

    def phase_b_record_and_verify_checkpoint(
        self,
        *,
        task_id: UUID,
        saver: OrbiPostgresSaver,
        checkpoint_id: str | None = None,
        require_interrupt: bool = True,
    ) -> str:
        """Phase B: Verify checkpoint exists in langgraph_checkpoints and record pending_checkpoint_id."""
        thread_id = str(task_id)
        ckpt_id = checkpoint_id or saver.get_latest_interrupt_checkpoint_id(thread_id)
        if not ckpt_id:
            raise Eval04Error(f"Phase B verification failed: no interrupt checkpoint for {thread_id}")
        if not saver.verify_checkpoint(thread_id, ckpt_id, require_interrupt=require_interrupt):
            raise Eval04Error(f"Phase B read-back verification failed for checkpoint {ckpt_id}")

        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET pending_checkpoint_id = %s,
                        updated_at = now()
                    WHERE task_id = %s
                      AND status = %s
                    """,
                    (ckpt_id, task_id, TaskStatus.SUSPENDING_FOR_USER.value),
                )
            conn.commit()
        return ckpt_id

    def phase_c_commit_waiting_for_user(
        self,
        *,
        task_id: UUID,
        interaction_id: UUID,
        checkpoint_id: str,
        actor_id: str,
        claim_epoch: int | None = None,
        from_reconciler: bool = False,
        outbox_event_id: UUID | None = None,
        resume_token_id: UUID | None = None,
    ) -> UUID:
        """Phase C: Commit WAITING_FOR_USER + PENDING_USER + DELIVER_INTERACTION_PROMPT outbox."""
        prompt_outbox_event_id = uuid4()
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.tasks WHERE task_id = %s FOR UPDATE",
                    (task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise TaskNotResumableError(f"Task {task_id} not found")
                if task["status"] != TaskStatus.SUSPENDING_FOR_USER.value:
                    raise TaskNotResumableError(
                        f"Phase C requires SUSPENDING_FOR_USER, got {task['status']}"
                    )
                if task["cancellation_requested"]:
                    raise TaskNotResumableError("Task cancelled before Phase C commit")
                if not from_reconciler and claim_epoch is not None:
                    if task["claim_epoch"] != claim_epoch:
                        raise StaleClaimEpochFencedError("Stale claim_epoch during Phase C")
                if resume_token_id is not None and task["resume_token_id"] != resume_token_id:
                    raise StaleClaimEpochFencedError("resume_token_id mismatch during Phase C")

                new_task_version = task["task_version"] + 1
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = %s,
                        pending_checkpoint_id = %s,
                        verified_checkpoint_id = %s,
                        resume_token_id = NULL,
                        claimed_by_worker_id = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (
                        TaskStatus.WAITING_FOR_USER.value,
                        new_task_version,
                        checkpoint_id,
                        checkpoint_id,
                        task_id,
                    ),
                )
                if outbox_event_id is not None or task["resume_token_id"] is not None:
                    if outbox_event_id is not None:
                        cur.execute(
                            "SELECT event_id, idempotency_key, claim_epoch FROM orbi_app.task_lifecycle_outbox WHERE event_id = %s",
                            (outbox_event_id,),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT event_id, idempotency_key, claim_epoch
                            FROM orbi_app.task_lifecycle_outbox
                            WHERE task_id = %s AND resume_token_id = %s AND status = 'RUNNING'
                            LIMIT 1
                            """,
                            (task_id, task["resume_token_id"]),
                        )
                    ob = cur.fetchone()
                    if ob is not None:
                        cur.execute(
                            """
                            INSERT INTO orbi_app.task_lifecycle_processed_events
                                (idempotency_key, event_id, task_id, processed_by_worker_id, claim_epoch)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (idempotency_key) DO NOTHING
                            """,
                            (
                                ob["idempotency_key"],
                                ob["event_id"],
                                task_id,
                                actor_id,
                                ob["claim_epoch"],
                            ),
                        )
                        cur.execute(
                            """
                            UPDATE orbi_app.task_lifecycle_outbox
                            SET status = %s, processed_at = now()
                            WHERE event_id = %s
                            """,
                            (OutboxStatus.PROCESSED.value, ob["event_id"]),
                        )
                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = %s,
                        verified_checkpoint_id = %s,
                        task_version = %s
                    WHERE interaction_id = %s AND status = %s
                    """,
                    (
                        InteractionStatus.PENDING_USER.value,
                        checkpoint_id,
                        new_task_version,
                        interaction_id,
                        InteractionStatus.CREATED.value,
                    ),
                )
                if cur.rowcount != 1:
                    raise Eval04Error(
                        f"Interaction {interaction_id} not in CREATED state during Phase C"
                    )

                idemp_key = f"deliver:{interaction_id}"
                cur.execute(
                    """
                    INSERT INTO orbi_app.task_lifecycle_outbox (
                        event_id, task_id, interaction_id, event_type, idempotency_key,
                        expected_task_version, resume_token_id, payload, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    (
                        prompt_outbox_event_id,
                        task_id,
                        interaction_id,
                        OutboxEventType.DELIVER_INTERACTION_PROMPT.value,
                        idemp_key,
                        new_task_version,
                        json.dumps(
                            {
                                "interaction_id": str(interaction_id),
                                "verified_checkpoint_id": checkpoint_id,
                            }
                        ),
                        OutboxStatus.PENDING.value,
                    ),
                )
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="INTERACTION_CREATED_WAITING_FOR_USER",
                    actor=actor_id,
                    details={
                        "verified_checkpoint_id": checkpoint_id,
                        "task_version": new_task_version,
                        "reconciled": from_reconciler,
                    },
                )
            conn.commit()
        return prompt_outbox_event_id

    def submit_interaction_response(
        self,
        *,
        interaction_id: UUID,
        actor_principal: str,
        channel: str,
        payload: dict[str, Any],
        response_idempotency_key: str,
        expected_version: int | None = None,
    ) -> InteractionSubmissionResult:
        """HumanInteractionManager Step 0 Authz + 3-Step interaction_version CAS + Phase D Outbox."""
        # Step 0: Pre-mutation read & authorization gate (ZERO DB mutation on failure)
        interaction = self.get_interaction(interaction_id)
        if channel not in interaction["allowed_response_channels"]:
            self._standalone_audit(
                task_id=interaction["task_id"],
                interaction_id=interaction_id,
                event_type="CHANNEL_BLOCKED_INTERACTION_ATTEMPT",
                actor=actor_principal,
                details={"channel": channel},
            )
            raise AuthzDeniedError(f"Channel {channel} not permitted for interaction {interaction_id}")

        itype = InteractionType(interaction["interaction_type"])
        required_relation = (
            "approver" if itype == InteractionType.APPROVAL_REQUEST else "responder"
        )
        if not self.policy_engine.check_interaction_actor(
            actor_principal, required_relation, interaction_id
        ):
            self._standalone_audit(
                task_id=interaction["task_id"],
                interaction_id=interaction_id,
                event_type="UNAUTHORIZED_INTERACTION_ATTEMPT",
                actor=actor_principal,
                details={"required_relation": required_relation, "channel": channel},
            )
            raise AuthzDeniedError(
                f"403 AUTHZ_DENIED: {actor_principal} lacks {required_relation} on {interaction_id}"
            )

        # Idempotent retry short-circuit
        if (
            interaction["response_idempotency_key"] == response_idempotency_key
            and interaction["status"]
            in (InteractionStatus.RESOLVED.value, InteractionStatus.REJECTED.value)
        ):
            outbox_id: UUID | None = None
            task = self.get_task(interaction["task_id"])
            if interaction["status"] == InteractionStatus.RESOLVED.value:
                with open_connection(self.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT event_id, resume_token_id
                            FROM orbi_app.task_lifecycle_outbox
                            WHERE interaction_id = %s AND event_type = %s
                            ORDER BY created_at DESC LIMIT 1
                            """,
                            (interaction_id, OutboxEventType.RESUME_TASK.value),
                        )
                        ob = cur.fetchone()
                        outbox_id = ob["event_id"] if ob else None
            return InteractionSubmissionResult(
                interaction_id=interaction_id,
                interaction_status=InteractionStatus(interaction["status"]),
                interaction_version=interaction["interaction_version"],
                task_id=interaction["task_id"],
                task_status=task.status,
                resume_outbox_event_id=outbox_id,
                resume_token_id=task.resume_token_id,
                idempotent_replay=True,
            )

        if expected_version is not None and interaction["interaction_version"] != expected_version:
            raise InteractionConflictError(
                f"409 Conflict: expected interaction_version={expected_version}, got {interaction['interaction_version']}"
            )

        # Pre-check task & automation currency/expiry
        now_utc = datetime.now(timezone.utc)
        task_id: UUID = interaction["task_id"]
        task = self.get_task(task_id)
        if interaction["expires_at"] <= now_utc or (
            task.expires_at is not None and task.expires_at <= now_utc
        ):
            self._expire_single_task(task_id, interaction_id, actor_principal)
            raise StaleInteractionRejectedError("Interaction or task expired")

        if (
            task.status != TaskStatus.WAITING_FOR_USER
            or task.cancellation_requested
            or task.task_version != interaction["task_version"]
        ):
            self._standalone_audit(
                task_id=task_id,
                interaction_id=interaction_id,
                event_type="STALE_INTERACTION_REJECTED",
                actor=actor_principal,
                details={
                    "task_status": task.status.value,
                    "task_version": task.task_version,
                    "interaction_task_version": interaction["task_version"],
                },
            )
            raise StaleInteractionRejectedError(
                f"STALE_INTERACTION_REJECTED: task status={task.status.value}, v={task.task_version}"
            )

        if interaction["automation_id"] is not None:
            with open_connection(self.dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT status, generation FROM orbi_automation.automation_definitions WHERE automation_id = %s",
                        (interaction["automation_id"],),
                    )
                    auto = cur.fetchone()
            if (
                auto is None
                or auto["status"] != AutomationStatus.ACTIVE.value
                or auto["generation"] != interaction["automation_generation"]
            ):
                self._standalone_audit(
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="STALE_AUTOMATION_GENERATION_REJECTED",
                    actor=actor_principal,
                    details={
                        "automation_id": str(interaction["automation_id"]),
                        "expected_gen": interaction["automation_generation"],
                        "live_auto": auto,
                    },
                )
                raise StaleAutomationGenerationRejectedError(
                    "STALE_AUTOMATION_GENERATION_REJECTED"
                )

        # CAS Transition 1: PENDING_USER | TAKEOVER_ACTIVE | VALIDATION_FAILED_RETRYABLE -> RESPONSE_RECEIVED
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                if expected_version is not None:
                    cur.execute(
                        """
                        UPDATE orbi_app.interaction_requests
                        SET status = %s,
                            interaction_version = interaction_version + 1,
                            response_idempotency_key = %s,
                            resolved_via_channel = %s
                        WHERE interaction_id = %s
                          AND interaction_version = %s
                          AND status IN ('PENDING_USER', 'TAKEOVER_ACTIVE', 'VALIDATION_FAILED_RETRYABLE')
                          AND expires_at > now()
                        RETURNING interaction_version
                        """,
                        (
                            InteractionStatus.RESPONSE_RECEIVED.value,
                            response_idempotency_key,
                            channel,
                            interaction_id,
                            expected_version,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE orbi_app.interaction_requests
                        SET status = %s,
                            interaction_version = interaction_version + 1,
                            response_idempotency_key = %s,
                            resolved_via_channel = %s
                        WHERE interaction_id = %s
                          AND status IN ('PENDING_USER', 'TAKEOVER_ACTIVE', 'VALIDATION_FAILED_RETRYABLE')
                          AND expires_at > now()
                        RETURNING interaction_version
                        """,
                        (
                            InteractionStatus.RESPONSE_RECEIVED.value,
                            response_idempotency_key,
                            channel,
                            interaction_id,
                        ),
                    )
                row1 = cur.fetchone()
                if row1 is None:
                    conn.rollback()
                    raise InteractionConflictError(
                        "409 Conflict: ALREADY_CLAIMED_OR_STALE"
                    )
                v_received = int(row1["interaction_version"])
            conn.commit()

        # CAS Transition 2: RESPONSE_RECEIVED -> VALIDATING
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = %s,
                        interaction_version = interaction_version + 1
                    WHERE interaction_id = %s
                      AND interaction_version = %s
                      AND status = %s
                      AND expires_at > now()
                    RETURNING interaction_version
                    """,
                    (
                        InteractionStatus.VALIDATING.value,
                        interaction_id,
                        v_received,
                        InteractionStatus.RESPONSE_RECEIVED.value,
                    ),
                )
                row2 = cur.fetchone()
                if row2 is None:
                    conn.rollback()
                    raise InteractionConflictError("409 Conflict: VALIDATING CAS failed")
                v_validating = int(row2["interaction_version"])
            conn.commit()

        # Deterministic Validation
        validation_errors: list[str] = []
        try:
            jsonschema.validate(instance=payload, schema=interaction["response_schema"])
        except jsonschema.ValidationError as exc:
            validation_errors.append(exc.message)

        if validation_errors:
            with open_connection(self.dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE orbi_app.interaction_requests
                        SET status = %s,
                            interaction_version = interaction_version + 1,
                            response_idempotency_key = NULL
                        WHERE interaction_id = %s
                          AND interaction_version = %s
                          AND status = %s
                        RETURNING interaction_version
                        """,
                        (
                            InteractionStatus.VALIDATION_FAILED_RETRYABLE.value,
                            interaction_id,
                            v_validating,
                            InteractionStatus.VALIDATING.value,
                        ),
                    )
                    r_fail = cur.fetchone()
                    self._audit(
                        cur,
                        task_id=task_id,
                        interaction_id=interaction_id,
                        event_type="INTERACTION_VALIDATION_FAILED_RETRYABLE",
                        actor=actor_principal,
                        details={"errors": validation_errors},
                    )
                conn.commit()
            return InteractionSubmissionResult(
                interaction_id=interaction_id,
                interaction_status=InteractionStatus.VALIDATION_FAILED_RETRYABLE,
                interaction_version=int(r_fail["interaction_version"]),
                task_id=task_id,
                task_status=TaskStatus.WAITING_FOR_USER,
                validation_errors=validation_errors,
            )

        # Approval-specific checks
        approval_id: UUID | None = interaction["approval_id"]
        if itype == InteractionType.APPROVAL_REQUEST:
            assert approval_id is not None
            decision = payload.get("decision")
            if decision == "REJECT":
                with open_connection(self.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE orbi_app.interaction_requests
                            SET status = %s,
                                interaction_version = interaction_version + 1,
                                sanitized_response_payload = %s::jsonb,
                                resolved_at = now()
                            WHERE interaction_id = %s AND interaction_version = %s
                            RETURNING interaction_version
                            """,
                            (
                                InteractionStatus.REJECTED.value,
                                json.dumps({"decision": "REJECT"}),
                                interaction_id,
                                v_validating,
                            ),
                        )
                        r_rej = cur.fetchone()
                        cur.execute(
                            """
                            UPDATE orbi_app.approval_requests
                            SET status = %s, decided_at = now()
                            WHERE approval_id = %s
                            """,
                            (ApprovalStatus.REJECTED.value, approval_id),
                        )
                        cur.execute(
                            """
                            UPDATE orbi_app.tasks
                            SET status = %s,
                                task_version = task_version + 1,
                                cancellation_requested = TRUE,
                                updated_at = now()
                            WHERE task_id = %s
                            """,
                            (TaskStatus.CANCELLED.value, task_id),
                        )
                        self._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=interaction_id,
                            event_type="APPROVAL_REJECTED",
                            actor=actor_principal,
                            details={"approval_id": str(approval_id)},
                        )
                    conn.commit()
                return InteractionSubmissionResult(
                    interaction_id=interaction_id,
                    interaction_status=InteractionStatus.REJECTED,
                    interaction_version=int(r_rej["interaction_version"]),
                    task_id=task_id,
                    task_status=TaskStatus.CANCELLED,
                )

            # Verify policy_version, digest & HMAC before granting APPROVED
            approval = self.get_approval(approval_id)
            expected_hmac = compute_approval_hmac(
                approval_id, approval["binding_digest"], self.hmac_key
            )
            drift_reason: str | None = None
            if approval["policy_version"] != self.policy_engine.policy_version:
                drift_reason = "policy_version_drift"
            elif approval["hmac_signature"] != expected_hmac:
                drift_reason = "hmac_mismatch"
            elif approval["expires_at"] <= datetime.now(timezone.utc):
                drift_reason = "approval_expired"
            elif payload.get("expected_digest") and payload["expected_digest"] != approval["binding_digest"]:
                drift_reason = "expected_digest_mismatch"

            if drift_reason is not None:
                with open_connection(self.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE orbi_app.interaction_requests
                            SET status = %s, interaction_version = interaction_version + 1
                            WHERE interaction_id = %s AND interaction_version = %s
                            """,
                            (InteractionStatus.INVALIDATED.value, interaction_id, v_validating),
                        )
                        cur.execute(
                            "UPDATE orbi_app.approval_requests SET status = %s WHERE approval_id = %s",
                            (ApprovalStatus.INVALIDATED.value, approval_id),
                        )
                        self._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=interaction_id,
                            event_type="APPROVAL_INVALIDATED",
                            actor=actor_principal,
                            details={"approval_id": str(approval_id), "reason": drift_reason},
                        )
                    conn.commit()
                raise ApprovalInvalidatedError(f"Approval invalidated: {drift_reason}")

        # CAS Transition 3 + Phase D: Commit RESOLVED + Task QUEUED + outbox RESUME_TASK atomically
        resume_token_id = uuid4()
        outbox_event_id = uuid4()
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM orbi_app.tasks WHERE task_id = %s FOR UPDATE", (task_id,))
                locked_task = cur.fetchone()
                if (
                    locked_task["status"] != TaskStatus.WAITING_FOR_USER.value
                    or locked_task["cancellation_requested"]
                    or locked_task["task_version"] != interaction["task_version"]
                ):
                    cur.execute(
                        """
                        UPDATE orbi_app.interaction_requests
                        SET status = %s, interaction_version = interaction_version + 1
                        WHERE interaction_id = %s AND interaction_version = %s
                        """,
                        (InteractionStatus.CANCELLED.value, interaction_id, v_validating),
                    )
                    conn.commit()
                    raise StaleInteractionRejectedError("Task changed or cancelled during validation")

                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = %s,
                        interaction_version = interaction_version + 1,
                        sanitized_response_payload = %s::jsonb,
                        resolved_at = now()
                    WHERE interaction_id = %s
                      AND interaction_version = %s
                      AND status = %s
                    RETURNING interaction_version
                    """,
                    (
                        InteractionStatus.RESOLVED.value,
                        json.dumps(payload),
                        interaction_id,
                        v_validating,
                        InteractionStatus.VALIDATING.value,
                    ),
                )
                r_res = cur.fetchone()
                if r_res is None:
                    raise InteractionConflictError("409 Conflict during RESOLVED CAS 3")

                if approval_id is not None:
                    cur.execute(
                        """
                        UPDATE orbi_app.approval_requests
                        SET status = %s, decided_at = now()
                        WHERE approval_id = %s AND status = %s
                        """,
                        (
                            ApprovalStatus.APPROVED.value,
                            approval_id,
                            ApprovalStatus.PENDING.value,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise InteractionConflictError("Approval already consumed or invalid")

                new_task_version = locked_task["task_version"] + 1
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = %s,
                        resume_token_id = %s,
                        claim_epoch = 0,
                        waiting_reason = NULL,
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (
                        TaskStatus.QUEUED.value,
                        new_task_version,
                        resume_token_id,
                        task_id,
                    ),
                )

                outbox_payload = {
                    "interaction_id": str(interaction_id),
                    "interaction_type": itype.value,
                    "response": payload,
                    "approval_id": str(approval_id) if approval_id else None,
                }
                cur.execute(
                    """
                    INSERT INTO orbi_app.task_lifecycle_outbox (
                        event_id, task_id, interaction_id, event_type, idempotency_key,
                        expected_task_version, resume_token_id, payload, status, claim_epoch
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 0)
                    """,
                    (
                        outbox_event_id,
                        task_id,
                        interaction_id,
                        OutboxEventType.RESUME_TASK.value,
                        f"resume:{interaction_id}:{resume_token_id}",
                        new_task_version,
                        resume_token_id,
                        json.dumps(outbox_payload),
                        OutboxStatus.PENDING.value,
                    ),
                )
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="INTERACTION_RESOLVED_RESUME_QUEUED",
                    actor=actor_principal,
                    details={
                        "task_version": new_task_version,
                        "resume_token_id": str(resume_token_id),
                        "outbox_event_id": str(outbox_event_id),
                    },
                )
            conn.commit()

        return InteractionSubmissionResult(
            interaction_id=interaction_id,
            interaction_status=InteractionStatus.RESOLVED,
            interaction_version=int(r_res["interaction_version"]),
            task_id=task_id,
            task_status=TaskStatus.QUEUED,
            resume_outbox_event_id=outbox_event_id,
            resume_token_id=resume_token_id,
        )

    def _expire_single_task(
        self, task_id: UUID, interaction_id: UUID | None, actor: str
    ) -> None:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = task_version + 1,
                        resume_token_id = NULL,
                        claimed_by_worker_id = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE task_id = %s
                      AND status IN ('QUEUED', 'SUSPENDING_FOR_USER', 'WAITING_FOR_USER', 'RUNNING')
                    """,
                    (TaskStatus.EXPIRED.value, task_id),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = %s, interaction_version = interaction_version + 1
                    WHERE task_id = %s
                      AND status IN ('CREATED', 'PENDING_USER', 'TAKEOVER_ACTIVE', 'RESPONSE_RECEIVED', 'VALIDATING', 'VALIDATION_FAILED_RETRYABLE')
                    """,
                    (InteractionStatus.EXPIRED.value, task_id),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.approval_requests
                    SET status = %s
                    WHERE task_id = %s AND status IN ('PENDING', 'APPROVED')
                    """,
                    (ApprovalStatus.EXPIRED.value, task_id),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.task_lifecycle_outbox
                    SET status = %s
                    WHERE task_id = %s AND status IN ('PENDING', 'RUNNING')
                    """,
                    (OutboxStatus.CANCELLED.value, task_id),
                )
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="TASK_OR_INTERACTION_EXPIRED",
                    actor=actor,
                    details={},
                )
            conn.commit()

    def claim_resume_outbox_event(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        expected_claim_epoch: int,
        lease_ttl_seconds: float = 30.0,
    ) -> ClaimedResumeEvent | None:
        """HLD Section E.5.4 Phase D.3 single-winner SQL CAS across outbox and tasks."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.task_lifecycle_outbox AS o
                    SET status = 'RUNNING',
                        claim_epoch = o.claim_epoch + 1,
                        claimed_by_worker_id = %s,
                        lease_expires_at = now() + (%s * interval '1 second'),
                        attempt_count = o.attempt_count + 1
                    FROM orbi_app.tasks AS t
                    WHERE o.event_id = %s
                      AND o.task_id = t.task_id
                      AND o.event_type = 'RESUME_TASK'
                      AND o.claim_epoch = %s
                      AND o.attempt_count < o.max_attempts
                      AND (
                          (o.status = 'PENDING' AND t.status = 'QUEUED')
                          OR (o.status = 'RUNNING' AND o.lease_expires_at < now() AND t.status = 'RUNNING')
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM orbi_app.task_lifecycle_processed_events AS p
                          WHERE p.idempotency_key = o.idempotency_key
                      )
                      AND t.resume_token_id = o.resume_token_id
                      AND t.task_version = o.expected_task_version
                      AND t.cancellation_requested = FALSE
                      AND (t.expires_at IS NULL OR t.expires_at > now())
                      AND (
                          t.automation_id IS NULL
                          OR EXISTS (
                              SELECT 1 FROM orbi_automation.automation_definitions AS a
                              WHERE a.automation_id = t.automation_id
                                AND a.status = 'ACTIVE'
                                AND a.generation = t.automation_generation
                          )
                      )
                    RETURNING o.event_id, o.task_id, o.interaction_id, o.resume_token_id,
                              o.claim_epoch, o.expected_task_version, o.payload,
                              t.execution_id, t.verified_checkpoint_id
                    """,
                    (worker_id, lease_ttl_seconds, event_id, expected_claim_epoch),
                )
                claimed = cur.fetchone()
                if claimed is None:
                    conn.rollback()
                    return None

                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        claim_epoch = %s,
                        claimed_by_worker_id = %s,
                        lease_expires_at = now() + (%s * interval '1 second'),
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (
                        TaskStatus.RUNNING.value,
                        claimed["claim_epoch"],
                        worker_id,
                        lease_ttl_seconds,
                        claimed["task_id"],
                    ),
                )
                self._audit(
                    cur,
                    task_id=claimed["task_id"],
                    interaction_id=claimed["interaction_id"],
                    event_type="RESUME_TASK_CLAIMED",
                    actor=worker_id,
                    details={
                        "event_id": str(event_id),
                        "claim_epoch": claimed["claim_epoch"],
                    },
                )
            conn.commit()

        return ClaimedResumeEvent(
            event_id=claimed["event_id"],
            task_id=claimed["task_id"],
            execution_id=claimed["execution_id"],
            interaction_id=claimed["interaction_id"],
            resume_token_id=claimed["resume_token_id"],
            claim_epoch=claimed["claim_epoch"],
            expected_task_version=claimed["expected_task_version"],
            verified_checkpoint_id=claimed["verified_checkpoint_id"],
            payload=claimed["payload"],
        )

    def renew_worker_lease(
        self,
        *,
        task_id: UUID,
        worker_id: str,
        claim_epoch: int,
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        """Heartbeat renewal gated strictly on active claim_epoch and non-cancelled state."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET lease_expires_at = now() + (%s * interval '1 second'),
                        updated_at = now()
                    WHERE task_id = %s
                      AND claim_epoch = %s
                      AND claimed_by_worker_id = %s
                      AND status = %s
                      AND cancellation_requested = FALSE
                    """,
                    (lease_ttl_seconds, task_id, claim_epoch, worker_id, TaskStatus.RUNNING.value),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    self._standalone_audit(
                        task_id=task_id,
                        interaction_id=None,
                        event_type="STALE_CLAIM_EPOCH_FENCED",
                        actor=worker_id,
                        details={"attempted_claim_epoch": claim_epoch, "operation": "heartbeat"},
                    )
                    raise StaleClaimEpochFencedError(
                        f"Heartbeat rejected for worker={worker_id} claim_epoch={claim_epoch}"
                    )
                cur.execute(
                    """
                    UPDATE orbi_app.task_lifecycle_outbox
                    SET lease_expires_at = now() + (%s * interval '1 second')
                    WHERE task_id = %s
                      AND claim_epoch = %s
                      AND claimed_by_worker_id = %s
                      AND status = 'RUNNING'
                    """,
                    (lease_ttl_seconds, task_id, claim_epoch, worker_id),
                )
            conn.commit()

    def complete_task_turn(
        self,
        *,
        task_id: UUID,
        worker_id: str,
        claim_epoch: int,
        result_payload: dict[str, Any],
        outbox_event_id: UUID | None = None,
        resume_token_id: UUID | None = None,
        crash_after_processed_event_before_outbox_ack: bool = False,
    ) -> TaskRecord:
        """Atomically commit turn completion, clear resume_token_id, and ack outbox."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                if resume_token_id is not None:
                    cur.execute(
                        """
                        UPDATE orbi_app.tasks
                        SET status = %s,
                            task_version = task_version + 1,
                            resume_token_id = NULL,
                            claimed_by_worker_id = NULL,
                            lease_expires_at = NULL,
                            result_payload = %s::jsonb,
                            updated_at = now()
                        WHERE task_id = %s
                          AND claim_epoch = %s
                          AND claimed_by_worker_id = %s
                          AND status = %s
                          AND resume_token_id = %s
                          AND cancellation_requested = FALSE
                        RETURNING *
                        """,
                        (
                            TaskStatus.COMPLETED.value,
                            json.dumps(result_payload),
                            task_id,
                            claim_epoch,
                            worker_id,
                            TaskStatus.RUNNING.value,
                            resume_token_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE orbi_app.tasks
                        SET status = %s,
                            task_version = task_version + 1,
                            resume_token_id = NULL,
                            claimed_by_worker_id = NULL,
                            lease_expires_at = NULL,
                            result_payload = %s::jsonb,
                            updated_at = now()
                        WHERE task_id = %s
                          AND claim_epoch = %s
                          AND claimed_by_worker_id = %s
                          AND status = %s
                          AND cancellation_requested = FALSE
                        RETURNING *
                        """,
                        (
                            TaskStatus.COMPLETED.value,
                            json.dumps(result_payload),
                            task_id,
                            claim_epoch,
                            worker_id,
                            TaskStatus.RUNNING.value,
                        ),
                    )
                updated_task = cur.fetchone()
                if updated_task is None:
                    conn.rollback()
                    self._standalone_audit(
                        task_id=task_id,
                        interaction_id=None,
                        event_type="STALE_CLAIM_EPOCH_FENCED",
                        actor=worker_id,
                        details={"attempted_claim_epoch": claim_epoch, "operation": "complete_turn"},
                    )
                    raise StaleClaimEpochFencedError(
                        f"Completion CAS fenced for worker={worker_id} claim_epoch={claim_epoch}"
                    )

                if outbox_event_id is not None:
                    cur.execute(
                        "SELECT idempotency_key FROM orbi_app.task_lifecycle_outbox WHERE event_id = %s",
                        (outbox_event_id,),
                    )
                    ob = cur.fetchone()
                    if ob is not None:
                        cur.execute(
                            """
                            INSERT INTO orbi_app.task_lifecycle_processed_events
                                (idempotency_key, event_id, task_id, processed_by_worker_id, claim_epoch)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (idempotency_key) DO NOTHING
                            """,
                            (ob["idempotency_key"], outbox_event_id, task_id, worker_id, claim_epoch),
                        )
                    if not crash_after_processed_event_before_outbox_ack:
                        cur.execute(
                            """
                            UPDATE orbi_app.task_lifecycle_outbox
                            SET status = %s, processed_at = now()
                            WHERE event_id = %s AND claim_epoch = %s
                            """,
                            (OutboxStatus.PROCESSED.value, outbox_event_id, claim_epoch),
                        )

                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=None,
                    event_type="TASK_COMPLETED",
                    actor=worker_id,
                    details={"claim_epoch": claim_epoch},
                )
            conn.commit()

        if crash_after_processed_event_before_outbox_ack:
            raise SimulatedCrashError(
                "Injected crash after task_lifecycle_processed_events commit before outbox PROCESSED ack"
            )
        return _row_to_task_record(updated_task)

    def cancel_task(self, task_id: UUID, *, actor: str, reason: str = "user_cancelled") -> TaskRecord:
        """Atomically request cancellation; immediately cancel non-RUNNING tasks & cascade interactions."""
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM orbi_app.tasks WHERE task_id = %s FOR UPDATE",
                    (task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise Eval04Error(f"Task {task_id} not found")

                cur_status = TaskStatus(task["status"])
                if cur_status in TERMINAL_TASK_STATUSES:
                    conn.rollback()
                    return _row_to_task_record(task)

                if cur_status in (
                    TaskStatus.CREATED,
                    TaskStatus.QUEUED,
                    TaskStatus.SUSPENDING_FOR_USER,
                    TaskStatus.WAITING_FOR_USER,
                ):
                    cur.execute(
                        """
                        UPDATE orbi_app.tasks
                        SET status = %s,
                            task_version = task_version + 1,
                            cancellation_requested = TRUE,
                            resume_token_id = NULL,
                            claimed_by_worker_id = NULL,
                            lease_expires_at = NULL,
                            error_code = %s,
                            updated_at = now()
                        WHERE task_id = %s
                        RETURNING *
                        """,
                        (TaskStatus.CANCELLED.value, reason, task_id),
                    )
                    updated = cur.fetchone()
                else:
                    # RUNNING: signal cooperative cancellation for Gateway Gate 5 & race honesty
                    cur.execute(
                        """
                        UPDATE orbi_app.tasks
                        SET cancellation_requested = TRUE,
                            error_code = %s,
                            updated_at = now()
                        WHERE task_id = %s
                        RETURNING *
                        """,
                        (reason, task_id),
                    )
                    updated = cur.fetchone()

                cur.execute(
                    """
                    UPDATE orbi_app.interaction_requests
                    SET status = %s, interaction_version = interaction_version + 1
                    WHERE task_id = %s
                      AND status IN ('CREATED', 'PENDING_USER', 'TAKEOVER_ACTIVE', 'RESPONSE_RECEIVED', 'VALIDATING', 'VALIDATION_FAILED_RETRYABLE')
                    """,
                    (InteractionStatus.CANCELLED.value, task_id),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.approval_requests
                    SET status = %s
                    WHERE task_id = %s AND status IN ('PENDING', 'APPROVED')
                    """,
                    (ApprovalStatus.INVALIDATED.value, task_id),
                )
                cur.execute(
                    """
                    UPDATE orbi_app.task_lifecycle_outbox
                    SET status = %s
                    WHERE task_id = %s AND status IN ('PENDING', 'RUNNING')
                    """,
                    (OutboxStatus.CANCELLED.value, task_id),
                )
                self._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=None,
                    event_type="TASK_CANCELLATION_RECORDED",
                    actor=actor,
                    details={"reason": reason, "previous_status": cur_status.value},
                )
            conn.commit()
        return _row_to_task_record(updated)


class CrashReconciler:
    """Three-part startup/periodic crash recovery reconciler (HLD Section D.3 & E.5.4)."""

    def __init__(self, task_manager: DeterministicTaskManager, dsn: str | None = None) -> None:
        self.task_manager = task_manager
        self.dsn = dsn or task_manager.dsn

    def reconcile_incomplete_suspensions(
        self,
        saver: OrbiPostgresSaver,
        *,
        ignore_lease_ttl: bool = False,
    ) -> dict[str, int]:
        """Part 1: Roll forward or roll back SUSPENDING_FOR_USER tasks."""
        rolled_forward = 0
        rolled_back = 0
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                if ignore_lease_ttl:
                    cur.execute(
                        "SELECT * FROM orbi_app.tasks WHERE status = %s",
                        (TaskStatus.SUSPENDING_FOR_USER.value,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM orbi_app.tasks
                        WHERE status = %s
                          AND (lease_expires_at IS NULL OR lease_expires_at < now())
                        """,
                        (TaskStatus.SUSPENDING_FOR_USER.value,),
                    )
                stuck_tasks = [dict(r) for r in cur.fetchall()]

        for task in stuck_tasks:
            task_id = task["task_id"]
            thread_id = str(task_id)
            with open_connection(self.dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT * FROM orbi_app.interaction_requests
                        WHERE task_id = %s AND status = %s
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (task_id, InteractionStatus.CREATED.value),
                    )
                    interaction = cur.fetchone()

            if task["cancellation_requested"] or (
                task["expires_at"] is not None
                and task["expires_at"] <= datetime.now(timezone.utc)
            ):
                self._rollback_suspension(
                    task_id=task_id,
                    interaction_id=interaction["interaction_id"] if interaction else None,
                    saver=saver,
                    target_status=(
                        TaskStatus.CANCELLED
                        if task["cancellation_requested"]
                        else TaskStatus.EXPIRED
                    ),
                )
                rolled_back += 1
                continue

            candidate_ckpt = task["pending_checkpoint_id"] or saver.get_latest_interrupt_checkpoint_id(
                thread_id
            )
            if (
                candidate_ckpt is not None
                and interaction is not None
                and saver.verify_checkpoint(thread_id, candidate_ckpt, require_interrupt=True)
            ):
                self.task_manager.phase_c_commit_waiting_for_user(
                    task_id=task_id,
                    interaction_id=interaction["interaction_id"],
                    checkpoint_id=candidate_ckpt,
                    actor_id="crash-reconciler",
                    from_reconciler=True,
                )
                rolled_forward += 1
            else:
                self._rollback_suspension(
                    task_id=task_id,
                    interaction_id=interaction["interaction_id"] if interaction else None,
                    saver=saver,
                    target_status=TaskStatus.QUEUED,
                )
                rolled_back += 1

        return {"rolled_forward": rolled_forward, "rolled_back": rolled_back}

    def _rollback_suspension(
        self,
        *,
        task_id: UUID,
        interaction_id: UUID | None,
        saver: OrbiPostgresSaver,
        target_status: TaskStatus,
    ) -> None:
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.tasks
                    SET status = %s,
                        task_version = task_version + 1,
                        waiting_reason = NULL,
                        pending_checkpoint_id = NULL,
                        claimed_by_worker_id = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE task_id = %s AND status = %s
                    """,
                    (target_status.value, task_id, TaskStatus.SUSPENDING_FOR_USER.value),
                )
                if interaction_id is not None:
                    cur.execute(
                        """
                        UPDATE orbi_app.interaction_requests
                        SET status = %s, interaction_version = interaction_version + 1
                        WHERE interaction_id = %s AND status = %s
                        """,
                        (InteractionStatus.FAILED.value, interaction_id, InteractionStatus.CREATED.value),
                    )
                    cur.execute(
                        """
                        UPDATE orbi_app.approval_requests
                        SET status = %s
                        WHERE interaction_id = %s AND status = %s
                        """,
                        (ApprovalStatus.INVALIDATED.value, interaction_id, ApprovalStatus.PENDING.value),
                    )
                self.task_manager._audit(
                    cur,
                    task_id=task_id,
                    interaction_id=interaction_id,
                    event_type="SUSPENSION_PHASE_B_ROLLBACK",
                    actor="crash-reconciler",
                    details={"restored_status": target_status.value},
                )
            conn.commit()
        saver.purge_task_checkpoints(str(task_id))

    def reconcile_outbox_and_stale_leases(
        self,
    ) -> dict[str, Any]:
        """Part 2: Sweep expired TTLs, ack already-processed resumes, dead-letter exhausted rows, and return claimable RESUME_TASK events."""
        expired_count = 0
        acked_already_processed = 0
        cancelled_stale = 0
        dead_lettered = 0
        dispatched_marked_unknown = 0
        claimable: list[tuple[UUID, int]] = []

        # 2a. Sweep expired tasks & interactions
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT task_id FROM orbi_app.tasks
                    WHERE status IN ('QUEUED', 'SUSPENDING_FOR_USER', 'WAITING_FOR_USER', 'RUNNING')
                      AND expires_at IS NOT NULL
                      AND expires_at <= now()
                    """
                )
                expired_tasks = [r["task_id"] for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT interaction_id, task_id FROM orbi_app.interaction_requests
                    WHERE status IN ('CREATED', 'PENDING_USER', 'TAKEOVER_ACTIVE')
                      AND expires_at <= now()
                    """
                )
                expired_interactions = [dict(r) for r in cur.fetchall()]
            conn.commit()

        for tid in expired_tasks:
            self.task_manager._expire_single_task(tid, None, "ttl-sweeper")
            expired_count += 1
        for item in expired_interactions:
            self.task_manager._expire_single_task(
                item["task_id"], item["interaction_id"], "ttl-sweeper"
            )
            expired_count += 1

        # 2b. Inspect PENDING or expired RUNNING RESUME_TASK outbox rows
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT o.*,
                           t.status AS task_status,
                           t.task_version,
                           t.resume_token_id AS live_resume_token_id,
                           t.cancellation_requested,
                           t.expires_at AS task_expires_at,
                           t.automation_id,
                           t.automation_generation
                    FROM orbi_app.task_lifecycle_outbox AS o
                    JOIN orbi_app.tasks AS t ON o.task_id = t.task_id
                    WHERE o.event_type = 'RESUME_TASK'
                      AND (
                          o.status = 'PENDING'
                          OR (o.status = 'RUNNING' AND o.lease_expires_at < now())
                      )
                    ORDER BY o.created_at ASC
                    """
                )
                candidates = [dict(r) for r in cur.fetchall()]

                for row in candidates:
                    event_id = row["event_id"]
                    task_id = row["task_id"]
                    cur.execute(
                        "SELECT 1 FROM orbi_app.task_lifecycle_processed_events WHERE idempotency_key = %s",
                        (row["idempotency_key"],),
                    )
                    already_processed = cur.fetchone() is not None
                    if (
                        already_processed
                        or row["live_resume_token_id"] is None
                        or row["task_version"] > row["expected_task_version"]
                    ):
                        cur.execute(
                            """
                            UPDATE orbi_app.task_lifecycle_outbox
                            SET status = %s, processed_at = now()
                            WHERE event_id = %s
                            """,
                            (OutboxStatus.PROCESSED.value, event_id),
                        )
                        self.task_manager._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=row["interaction_id"],
                            event_type="OUTBOX_ACKED_AFTER_PRE_ACK_CRASH",
                            actor="crash-reconciler",
                            details={"event_id": str(event_id)},
                        )
                        acked_already_processed += 1
                        continue

                    # Pre-recovery cancellation, expiry, version, or automation check
                    stale_auto = False
                    if row["automation_id"] is not None:
                        cur.execute(
                            "SELECT status, generation FROM orbi_automation.automation_definitions WHERE automation_id = %s",
                            (row["automation_id"],),
                        )
                        auto = cur.fetchone()
                        stale_auto = (
                            auto is None
                            or auto["status"] != AutomationStatus.ACTIVE.value
                            or auto["generation"] != row["automation_generation"]
                        )

                    if (
                        row["cancellation_requested"]
                        or row["task_status"] in {s.value for s in TERMINAL_TASK_STATUSES}
                        or row["task_version"] != row["expected_task_version"]
                        or stale_auto
                    ):
                        cur.execute(
                            "UPDATE orbi_app.task_lifecycle_outbox SET status = %s WHERE event_id = %s",
                            (OutboxStatus.CANCELLED.value, event_id),
                        )
                        if not row["task_status"] in {s.value for s in TERMINAL_TASK_STATUSES}:
                            cur.execute(
                                """
                                UPDATE orbi_app.tasks
                                SET status = %s,
                                    task_version = task_version + 1,
                                    resume_token_id = NULL,
                                    claimed_by_worker_id = NULL,
                                    lease_expires_at = NULL,
                                    updated_at = now()
                                WHERE task_id = %s
                                """,
                                (TaskStatus.CANCELLED.value, task_id),
                            )
                        cancelled_stale += 1
                        continue

                    if row["attempt_count"] >= row["max_attempts"]:
                        cur.execute(
                            "UPDATE orbi_app.task_lifecycle_outbox SET status = %s WHERE event_id = %s",
                            (OutboxStatus.DEAD_LETTERED.value, event_id),
                        )
                        cur.execute(
                            """
                            UPDATE orbi_app.tasks
                            SET status = %s,
                                task_version = task_version + 1,
                                error_code = 'RESUME_RETRIES_EXHAUSTED',
                                resume_token_id = NULL,
                                claimed_by_worker_id = NULL,
                                lease_expires_at = NULL,
                                updated_at = now()
                            WHERE task_id = %s
                            """,
                            (TaskStatus.FAILED.value, task_id),
                        )
                        self.task_manager._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=row["interaction_id"],
                            event_type="RESUME_RETRIES_EXHAUSTED_DEAD_LETTERED",
                            actor="crash-reconciler",
                            details={"event_id": str(event_id), "attempts": row["attempt_count"]},
                        )
                        dead_lettered += 1
                        continue

                    # Transition any unfinalized DISPATCHING receipt -> UNKNOWN_OUTCOME
                    cur.execute(
                        """
                        UPDATE orbi_app.step_execution_receipts
                        SET status = %s, updated_at = now()
                        WHERE task_id = %s AND status = %s
                        """,
                        (
                            ReceiptStatus.UNKNOWN_OUTCOME.value,
                            task_id,
                            ReceiptStatus.DISPATCHING.value,
                        ),
                    )
                    dispatched_marked_unknown += cur.rowcount or 0
                    claimable.append((event_id, int(row["claim_epoch"])))
            conn.commit()

        return {
            "expired": expired_count,
            "acked_already_processed": acked_already_processed,
            "cancelled_stale": cancelled_stale,
            "dead_lettered": dead_lettered,
            "dispatched_marked_unknown": dispatched_marked_unknown,
            "claimable": claimable,
        }

    def purge_orphaned_checkpoints(self, saver: OrbiPostgresSaver) -> int:
        """Part 3: Purge langgraph_checkpoints belonging to terminal tasks."""
        terminal_vals = [s.value for s in TERMINAL_TASK_STATUSES]
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id FROM orbi_app.tasks WHERE status = ANY(%s)",
                    (terminal_vals,),
                )
                terminal_ids = [str(r["task_id"]) for r in cur.fetchall()]
        purged = 0
        for tid in terminal_ids:
            purged += saver.purge_task_checkpoints(tid)
        return purged
