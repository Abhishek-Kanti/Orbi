"""MOD-07 Tool Registry and MOD-08 Tool Execution Gateway for EVAL-04."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

import jsonschema

from eval04.db import open_connection
from eval04.models import (
    ApprovalInvalidatedError,
    ApprovalStatus,
    AuthzDeniedError,
    AutomationStatus,
    Eval04Error,
    ExternalIdempotencyMode,
    InMemoryPolicyEngine,
    InteractionStatus,
    ReceiptStatus,
    SensitivityTier,
    SimulatedCrashError,
    StaleAutomationGenerationRejectedError,
    StaleClaimEpochFencedError,
    TaskNotResumableError,
    TaskStatus,
    UncertainExecutionEscalation,
)

DEFAULT_HMAC_KEY = b"orbi-eval04-dev-approval-hmac-key"


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_canonical_arguments_hash(
    args: dict[str, Any],
    semantic_fields: tuple[str, ...] | None = None,
) -> str:
    """Hash deterministic semantic tool arguments (excluding volatile metadata)."""
    if semantic_fields is not None:
        filtered = {k: args[k] for k in sorted(semantic_fields) if k in args}
    else:
        filtered = args
    return hashlib.sha256(_canonical_json(filtered).encode("utf-8")).hexdigest()


def compute_action_idempotency_key(
    *,
    tenant_id: str,
    task_id: UUID,
    execution_id: UUID,
    logical_step_id: str,
    tool_id: str,
    tool_version: str,
    canonical_arguments_hash: str,
    target_resource: str,
    approval_id: UUID | None = None,
) -> str:
    """Compute stable logical action_idempotency_key (never includes worker/epoch/attempt)."""
    payload = {
        "tenant_id": tenant_id,
        "task_id": str(task_id),
        "execution_id": str(execution_id),
        "logical_step_id": logical_step_id,
        "tool_id": tool_id,
        "tool_version": tool_version,
        "canonical_arguments_hash": canonical_arguments_hash,
        "target_resource": target_resource,
        "approval_id": str(approval_id) if approval_id else None,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_approval_binding_digest(
    *,
    user_id: str,
    task_id: UUID,
    execution_id: UUID,
    tool_id: str,
    tool_version: str,
    canonical_arguments_hash: str,
    target_resource: str,
    policy_version: int,
    expires_at: datetime,
    automation_generation: int | None = None,
) -> str:
    """Construct canonical ADR-004 SHA-256 binding digest."""
    exp_utc = expires_at.astimezone(timezone.utc).isoformat()
    payload = {
        "user_id": user_id,
        "task_id": str(task_id),
        "execution_id": str(execution_id),
        "automation_generation": automation_generation,
        "tool_id": tool_id,
        "tool_version": tool_version,
        "canonical_arguments_hash": canonical_arguments_hash,
        "target_resource": target_resource,
        "policy_version": policy_version,
        "expires_at": exp_utc,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_approval_hmac(
    approval_id: UUID,
    binding_digest: str,
    hmac_key: bytes = DEFAULT_HMAC_KEY,
) -> str:
    """Compute HMAC-SHA256(key, approval_id || binding_digest)."""
    msg = f"{approval_id}:{binding_digest}".encode("utf-8")
    return hmac.new(hmac_key, msg, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class ToolDefinition:
    """MOD-07 Central Tool Registry metadata entry."""

    tool_id: str
    tool_version: str
    sensitivity_tier: SensitivityTier
    semantic_fields: tuple[str, ...] | None = None
    external_idempotency_mode: ExternalIdempotencyMode = (
        ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE
    )
    idempotency_window_seconds: int | None = None
    reconciliation_probe_tool_id: str | None = None
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})


class ToolRegistry:
    """In-process MOD-07 tool catalog for EVAL-04."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tools[tool.tool_id] = tool

    def get_tool(self, tool_id: str) -> ToolDefinition:
        if tool_id not in self._tools:
            raise Eval04Error(f"Unknown tool_id: {tool_id}")
        return self._tools[tool_id]


@dataclass(frozen=True)
class ToolCallIntent:
    """Intent submitted by MOD-04 worker to MOD-08 Gateway."""

    tenant_id: str
    actor_user_id: str
    task_id: UUID
    execution_id: UUID
    logical_step_id: str
    tool_id: str
    arguments: dict[str, Any]
    target_resource: str
    worker_id: str
    claim_epoch: int
    resume_token_id: UUID | None = None
    approval_id: UUID | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized Gateway outcome returned to MOD-04."""

    receipt_id: UUID
    action_idempotency_key: str
    status: ReceiptStatus
    output: dict[str, Any]
    replayed_from_receipt: bool = False
    reconciled_via_probe: bool = False
    cancelled_after_external_commit: bool = False


class ToolExecutionGateway:
    """MOD-08 Gateway enforcing Gates 1-8, write-ahead receipts, and claim_epoch fencing."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy_engine: InMemoryPolicyEngine,
        dsn: str | None = None,
        hmac_key: bytes = DEFAULT_HMAC_KEY,
    ) -> None:
        self.registry = registry
        self.policy_engine = policy_engine
        self.dsn = dsn
        self.hmac_key = hmac_key
        self._providers: dict[str, Callable[..., dict[str, Any]]] = {}
        self._probes: dict[str, Callable[..., dict[str, Any]]] = {}
        self.crash_after_provider_before_receipt_commit: bool = False
        self.on_during_external_call: Callable[[], None] | None = None

    def register_provider(
        self, tool_id: str, handler: Callable[..., dict[str, Any]]
    ) -> None:
        self._providers[tool_id] = handler

    def register_probe(
        self, probe_tool_id: str, handler: Callable[..., dict[str, Any]]
    ) -> None:
        self._probes[probe_tool_id] = handler

    def _write_audit(
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
            conn.commit()

    def execute_tool_intent(self, intent: ToolCallIntent) -> ToolExecutionResult:
        """Execute Gates 1-8 with write-ahead DISPATCHING receipts and epoch fencing."""
        # Gate 1: Existence & Schema Gate
        tool = self.registry.get_tool(intent.tool_id)
        jsonschema.validate(instance=intent.arguments, schema=tool.input_schema)

        # Gate 1.5: Always fence stale/zombie workers BEFORE replaying or probing receipts
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, claim_epoch, claimed_by_worker_id,
                           (lease_expires_at > now()) AS lease_valid,
                           cancellation_requested
                    FROM orbi_app.tasks
                    WHERE task_id = %s
                    """,
                    (intent.task_id,),
                )
                task_row = cur.fetchone()
        if task_row is None:
            raise TaskNotResumableError(f"Task {intent.task_id} not found")
        if (
            task_row["claim_epoch"] != intent.claim_epoch
            or task_row["claimed_by_worker_id"] != intent.worker_id
            or not task_row["lease_valid"]
        ):
            self._write_audit(
                task_id=intent.task_id,
                interaction_id=None,
                event_type="STALE_CLAIM_EPOCH_FENCED",
                actor=intent.worker_id,
                details={
                    "worker_claim_epoch": intent.claim_epoch,
                    "authoritative_claim_epoch": task_row["claim_epoch"],
                    "authoritative_worker": task_row["claimed_by_worker_id"],
                    "lease_valid": task_row["lease_valid"],
                },
            )
            raise StaleClaimEpochFencedError(
                f"Worker {intent.worker_id} (epoch={intent.claim_epoch}) fenced by authoritative epoch={task_row['claim_epoch']}"
            )
        if task_row["status"] != TaskStatus.RUNNING.value or task_row["cancellation_requested"]:
            self._write_audit(
                task_id=intent.task_id,
                interaction_id=None,
                event_type="PRE_DISPATCH_CANCELLED_ABORT",
                actor=intent.worker_id,
                details={
                    "status": task_row["status"],
                    "cancellation_requested": task_row["cancellation_requested"],
                },
            )
            raise TaskNotResumableError(
                f"Pre-dispatch abort: task status={task_row['status']}, cancellation_requested={task_row['cancellation_requested']}"
            )

        # Gate 2: Immutable Action Identity & Write-Ahead Receipt Lookup
        canonical_args_hash = compute_canonical_arguments_hash(
            intent.arguments, tool.semantic_fields
        )
        action_key = compute_action_idempotency_key(
            tenant_id=intent.tenant_id,
            task_id=intent.task_id,
            execution_id=intent.execution_id,
            logical_step_id=intent.logical_step_id,
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            canonical_arguments_hash=canonical_args_hash,
            target_resource=intent.target_resource,
            approval_id=intent.approval_id,
        )

        existing_receipt: dict[str, Any] | None = None
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM orbi_app.step_execution_receipts
                    WHERE task_id = %s AND execution_id = %s AND logical_step_id = %s
                    FOR UPDATE
                    """,
                    (intent.task_id, intent.execution_id, intent.logical_step_id),
                )
                existing_receipt = cur.fetchone()
                if existing_receipt is not None:
                    # Enforce immutable retry identity
                    if (
                        existing_receipt["tool_id"] != tool.tool_id
                        or existing_receipt["tool_version"] != tool.tool_version
                        or existing_receipt["canonical_arguments_hash"] != canonical_args_hash
                        or existing_receipt["target_resource"] != intent.target_resource
                        or existing_receipt["approval_id"] != intent.approval_id
                        or existing_receipt["action_idempotency_key"] != action_key
                    ):
                        raise Eval04Error(
                            "IMMUTABLE_RETRY_IDENTITY_VIOLATION: retry mutated args, target, or key"
                        )

                    status = ReceiptStatus(existing_receipt["status"])
                    if status == ReceiptStatus.CONFIRMED_SUCCESS:
                        return ToolExecutionResult(
                            receipt_id=existing_receipt["receipt_id"],
                            action_idempotency_key=action_key,
                            status=ReceiptStatus.CONFIRMED_SUCCESS,
                            output=existing_receipt["sanitized_output"] or {},
                            replayed_from_receipt=True,
                        )
                    if status == ReceiptStatus.DISPATCHING:
                        # Transition unfinalized DISPATCHING -> UNKNOWN_OUTCOME first
                        cur.execute(
                            """
                            UPDATE orbi_app.step_execution_receipts
                            SET status = %s, updated_at = now()
                            WHERE receipt_id = %s
                            """,
                            (ReceiptStatus.UNKNOWN_OUTCOME.value, existing_receipt["receipt_id"]),
                        )
                        existing_receipt["status"] = ReceiptStatus.UNKNOWN_OUTCOME.value
                else:
                    receipt_id = uuid4()
                    cur.execute(
                        """
                        INSERT INTO orbi_app.step_execution_receipts (
                            receipt_id, tenant_id, task_id, execution_id, logical_step_id,
                            tool_id, tool_version, canonical_arguments_hash, target_resource,
                            approval_id, action_idempotency_key, external_idempotency_mode,
                            reconciliation_probe_tool_id, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            receipt_id,
                            intent.tenant_id,
                            intent.task_id,
                            intent.execution_id,
                            intent.logical_step_id,
                            tool.tool_id,
                            tool.tool_version,
                            canonical_args_hash,
                            intent.target_resource,
                            intent.approval_id,
                            action_key,
                            tool.external_idempotency_mode.value,
                            tool.reconciliation_probe_tool_id,
                            ReceiptStatus.PREPARED.value,
                        ),
                    )
                    existing_receipt = cur.fetchone()
            conn.commit()

        assert existing_receipt is not None
        receipt_id = existing_receipt["receipt_id"]
        current_receipt_status = ReceiptStatus(existing_receipt["status"])

        # Uncertain-Outcome Reconciliation Protocol
        if current_receipt_status == ReceiptStatus.UNKNOWN_OUTCOME:
            mode = tool.external_idempotency_mode
            if mode == ExternalIdempotencyMode.RECONCILABLE_READ_PROBE:
                probe_id = tool.reconciliation_probe_tool_id
                if not probe_id or probe_id not in self._probes:
                    raise UncertainExecutionEscalation(
                        "Missing reconciliation probe for RECONCILABLE_READ_PROBE",
                        receipt_id=receipt_id,
                        action_idempotency_key=action_key,
                        tool_id=tool.tool_id,
                        target_resource=intent.target_resource,
                    )
                probe_res = self._probes[probe_id](
                    action_idempotency_key=action_key,
                    arguments=intent.arguments,
                    target_resource=intent.target_resource,
                )
                if probe_res.get("executed") is True:
                    output_payload = probe_res.get("output", {"reconciled": True})
                    with open_connection(self.dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE orbi_app.step_execution_receipts
                                SET status = %s,
                                    provider_reference_id = %s,
                                    sanitized_output = %s::jsonb,
                                    updated_at = now()
                                WHERE receipt_id = %s
                                """,
                                (
                                    ReceiptStatus.CONFIRMED_SUCCESS.value,
                                    probe_res.get("provider_reference_id"),
                                    json.dumps(output_payload),
                                    receipt_id,
                                ),
                            )
                        conn.commit()
                    self._write_audit(
                        task_id=intent.task_id,
                        interaction_id=None,
                        event_type="EXTERNAL_OUTCOME_RECONCILED_SUCCESS",
                        actor=intent.worker_id,
                        details={
                            "receipt_id": str(receipt_id),
                            "action_idempotency_key": action_key,
                            "probe_tool_id": probe_id,
                        },
                    )
                    return ToolExecutionResult(
                        receipt_id=receipt_id,
                        action_idempotency_key=action_key,
                        status=ReceiptStatus.CONFIRMED_SUCCESS,
                        output=output_payload,
                        reconciled_via_probe=True,
                    )
                if probe_res.get("executed") is False:
                    with open_connection(self.dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE orbi_app.step_execution_receipts
                                SET status = %s, updated_at = now()
                                WHERE receipt_id = %s
                                """,
                                (
                                    ReceiptStatus.CONFIRMED_FAILURE_SAFE_TO_RETRY.value,
                                    receipt_id,
                                ),
                            )
                        conn.commit()
                    current_receipt_status = ReceiptStatus.CONFIRMED_FAILURE_SAFE_TO_RETRY
                else:
                    self._write_audit(
                        task_id=intent.task_id,
                        interaction_id=None,
                        event_type="EXTERNAL_ACTION_OUTCOME_UNKNOWN",
                        actor=intent.worker_id,
                        details={
                            "receipt_id": str(receipt_id),
                            "action_idempotency_key": action_key,
                            "reason": "inconclusive_probe",
                        },
                    )
                    raise UncertainExecutionEscalation(
                        "Inconclusive probe on UNKNOWN_OUTCOME",
                        receipt_id=receipt_id,
                        action_idempotency_key=action_key,
                        tool_id=tool.tool_id,
                        target_resource=intent.target_resource,
                    )
            elif mode == ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE:
                # Strictly prohibit blind automatic retry
                self._write_audit(
                    task_id=intent.task_id,
                    interaction_id=None,
                    event_type="EXTERNAL_ACTION_OUTCOME_UNKNOWN",
                    actor=intent.worker_id,
                    details={
                        "receipt_id": str(receipt_id),
                        "action_idempotency_key": action_key,
                        "tool_id": tool.tool_id,
                        "mode": mode.value,
                    },
                )
                raise UncertainExecutionEscalation(
                    "Blind retry prohibited for NON_IDEMPOTENT_NO_RECONCILE action in UNKNOWN_OUTCOME",
                    receipt_id=receipt_id,
                    action_idempotency_key=action_key,
                    tool_id=tool.tool_id,
                    target_resource=intent.target_resource,
                )
            # PROVIDER_IDEMPOTENCY_KEY falls through to Gate 5 dispatch with identical action_key

        # Gate 3: Stage-2 OpenFGA Gate
        if not self.policy_engine.check_tool_execute(
            intent.actor_user_id, tool.tool_id, intent.target_resource
        ):
            self._write_audit(
                task_id=intent.task_id,
                interaction_id=None,
                event_type="TOOL_AUTHZ_DENIED",
                actor=intent.actor_user_id,
                details={
                    "tool_id": tool.tool_id,
                    "target_resource": intent.target_resource,
                },
            )
            raise AuthzDeniedError(
                f"Actor {intent.actor_user_id} denied execute on {tool.tool_id}"
            )

        # Gate 4: Sensitivity & ADR-004 Approval Presence Check
        if tool.sensitivity_tier in (
            SensitivityTier.SENSITIVE_WRITE,
            SensitivityTier.IRREVERSIBLE,
        ):
            if intent.approval_id is None:
                raise ApprovalInvalidatedError(
                    f"Tool {tool.tool_id} ({tool.sensitivity_tier}) requires action-bound approval"
                )

        # Gate 5: Authoritative Pre-Dispatch Cancellation, Generation, Claim-Epoch & Write-Ahead Receipt
        is_recovery_dispatch = current_receipt_status in (
            ReceiptStatus.UNKNOWN_OUTCOME,
            ReceiptStatus.CONFIRMED_FAILURE_SAFE_TO_RETRY,
        )
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *, (lease_expires_at > now()) AS lease_valid
                    FROM orbi_app.tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (intent.task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise TaskNotResumableError(f"Task {intent.task_id} not found")

                if (
                    task["claim_epoch"] != intent.claim_epoch
                    or task["claimed_by_worker_id"] != intent.worker_id
                    or not task["lease_valid"]
                ):
                    conn.rollback()
                    self._write_audit(
                        task_id=intent.task_id,
                        interaction_id=None,
                        event_type="STALE_CLAIM_EPOCH_FENCED",
                        actor=intent.worker_id,
                        details={
                            "worker_claim_epoch": intent.claim_epoch,
                            "authoritative_claim_epoch": task["claim_epoch"],
                            "authoritative_worker": task["claimed_by_worker_id"],
                            "lease_valid": task["lease_valid"],
                        },
                    )
                    raise StaleClaimEpochFencedError(
                        f"Worker {intent.worker_id} (epoch={intent.claim_epoch}) fenced by authoritative epoch={task['claim_epoch']}"
                    )

                if (
                    intent.resume_token_id is not None
                    and task["resume_token_id"] != intent.resume_token_id
                ):
                    conn.rollback()
                    raise StaleClaimEpochFencedError("resume_token_id mismatch")

                if task["status"] != TaskStatus.RUNNING.value or task["cancellation_requested"]:
                    conn.rollback()
                    self._write_audit(
                        task_id=intent.task_id,
                        interaction_id=None,
                        event_type="PRE_DISPATCH_CANCELLED_ABORT",
                        actor=intent.worker_id,
                        details={
                            "status": task["status"],
                            "cancellation_requested": task["cancellation_requested"],
                        },
                    )
                    raise TaskNotResumableError(
                        f"Pre-dispatch abort: task status={task['status']}, cancellation_requested={task['cancellation_requested']}"
                    )

                if task["automation_id"] is not None:
                    cur.execute(
                        """
                        SELECT status, generation
                        FROM orbi_automation.automation_definitions
                        WHERE automation_id = %s
                        """,
                        (task["automation_id"],),
                    )
                    auto = cur.fetchone()
                    if (
                        auto is None
                        or auto["status"] != AutomationStatus.ACTIVE.value
                        or auto["generation"] != task["automation_generation"]
                    ):
                        conn.rollback()
                        self._write_audit(
                            task_id=intent.task_id,
                            interaction_id=None,
                            event_type="SKIPPED_STALE_GENERATION",
                            actor=intent.worker_id,
                            details={
                                "automation_id": str(task["automation_id"]),
                                "task_gen": task["automation_generation"],
                                "live_auto": auto,
                            },
                        )
                        raise StaleAutomationGenerationRejectedError(
                            "Automation inactive or generation drifted before dispatch"
                        )

                if intent.approval_id is not None:
                    cur.execute(
                        """
                        SELECT *, (expires_at > now()) AS not_expired
                        FROM orbi_app.approval_requests
                        WHERE approval_id = %s
                        FOR UPDATE
                        """,
                        (intent.approval_id,),
                    )
                    approval = cur.fetchone()
                    if approval is None:
                        raise ApprovalInvalidatedError("Approval record not found")

                    expected_digest = compute_approval_binding_digest(
                        user_id=approval["user_id"],
                        task_id=intent.task_id,
                        execution_id=intent.execution_id,
                        tool_id=tool.tool_id,
                        tool_version=tool.tool_version,
                        canonical_arguments_hash=canonical_args_hash,
                        target_resource=intent.target_resource,
                        policy_version=approval["policy_version"],
                        expires_at=approval["expires_at"],
                        automation_generation=task["automation_generation"],
                    )
                    expected_hmac = compute_approval_hmac(
                        intent.approval_id, expected_digest, self.hmac_key
                    )
                    allowed_statuses = (
                        {ApprovalStatus.APPROVED.value, ApprovalStatus.CONSUMED.value}
                        if is_recovery_dispatch
                        else {ApprovalStatus.APPROVED.value}
                    )
                    drift_reason: str | None = None
                    if approval["status"] not in allowed_statuses:
                        drift_reason = f"invalid_approval_status:{approval['status']}"
                    elif not approval["not_expired"]:
                        drift_reason = "approval_expired"
                    elif approval["policy_version"] != self.policy_engine.policy_version:
                        drift_reason = (
                            f"policy_version_drift:{approval['policy_version']}!={self.policy_engine.policy_version}"
                        )
                    elif (
                        approval["binding_digest"] != expected_digest
                        or approval["hmac_signature"] != expected_hmac
                    ):
                        drift_reason = "binding_digest_or_hmac_mismatch"

                    if drift_reason is not None:
                        cur.execute(
                            """
                            UPDATE orbi_app.approval_requests
                            SET status = %s
                            WHERE approval_id = %s
                            """,
                            (ApprovalStatus.INVALIDATED.value, intent.approval_id),
                        )
                        cur.execute(
                            """
                            UPDATE orbi_app.interaction_requests
                            SET status = %s, interaction_version = interaction_version + 1
                            WHERE interaction_id = %s
                            """,
                            (InteractionStatus.INVALIDATED.value, approval["interaction_id"]),
                        )
                        conn.commit()
                        self._write_audit(
                            task_id=intent.task_id,
                            interaction_id=approval["interaction_id"],
                            event_type="APPROVAL_INVALIDATED",
                            actor=intent.worker_id,
                            details={
                                "approval_id": str(intent.approval_id),
                                "reason": drift_reason,
                            },
                        )
                        raise ApprovalInvalidatedError(f"Approval invalidated: {drift_reason}")

                    if approval["status"] == ApprovalStatus.APPROVED.value:
                        cur.execute(
                            """
                            UPDATE orbi_app.approval_requests
                            SET status = %s, consumed_at = now()
                            WHERE approval_id = %s AND status = %s
                            """,
                            (
                                ApprovalStatus.CONSUMED.value,
                                intent.approval_id,
                                ApprovalStatus.APPROVED.value,
                            ),
                        )
                        if cur.rowcount != 1:
                            raise ApprovalInvalidatedError("Approval single-use CAS failed")

                # Commit write-ahead DISPATCHING state BEFORE network/adapter transmission
                cur.execute(
                    """
                    UPDATE orbi_app.step_execution_receipts
                    SET status = %s,
                        dispatched_claim_epoch = %s,
                        dispatched_at = now(),
                        updated_at = now()
                    WHERE receipt_id = %s
                    """,
                    (ReceiptStatus.DISPATCHING.value, intent.claim_epoch, receipt_id),
                )
            conn.commit()

        # Gate 6 & 7: Adapter Dispatch
        if tool.tool_id not in self._providers:
            raise Eval04Error(f"No provider registered for tool_id={tool.tool_id}")

        provider_result = self._providers[tool.tool_id](
            action_idempotency_key=action_key,
            arguments=intent.arguments,
            target_resource=intent.target_resource,
        )

        if self.on_during_external_call is not None:
            self.on_during_external_call()

        if self.crash_after_provider_before_receipt_commit:
            raise SimulatedCrashError(
                "Injected crash after external provider commit before receipt CONFIRMED_SUCCESS"
            )

        provider_ref = str(provider_result.get("provider_reference_id", f"ref-{uuid4().hex[:8]}"))

        # Check cancellation race honesty & commit CONFIRMED_SUCCESS
        cancelled_race = False
        with open_connection(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orbi_app.step_execution_receipts
                    SET status = %s,
                        provider_reference_id = %s,
                        sanitized_output = %s::jsonb,
                        updated_at = now()
                    WHERE receipt_id = %s
                    """,
                    (
                        ReceiptStatus.CONFIRMED_SUCCESS.value,
                        provider_ref,
                        json.dumps(provider_result),
                        receipt_id,
                    ),
                )
                cur.execute(
                    "SELECT cancellation_requested FROM orbi_app.tasks WHERE task_id = %s FOR UPDATE",
                    (intent.task_id,),
                )
                row = cur.fetchone()
                if row and row["cancellation_requested"]:
                    cancelled_race = True
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
                        (TaskStatus.CANCELLED_AFTER_EXTERNAL_COMMIT.value, intent.task_id),
                    )
            conn.commit()

        self._write_audit(
            task_id=intent.task_id,
            interaction_id=None,
            event_type=(
                "TOOL_COMPLETED_DURING_CANCELLATION_RACE"
                if cancelled_race
                else "TOOL_EXECUTED"
            ),
            actor=intent.worker_id,
            details={
                "receipt_id": str(receipt_id),
                "action_idempotency_key": action_key,
                "tool_id": tool.tool_id,
                "provider_reference_id": provider_ref,
            },
        )

        return ToolExecutionResult(
            receipt_id=receipt_id,
            action_idempotency_key=action_key,
            status=ReceiptStatus.CONFIRMED_SUCCESS,
            output=provider_result,
            cancelled_after_external_commit=cancelled_race,
        )
