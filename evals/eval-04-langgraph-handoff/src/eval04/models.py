"""Domain models, enumerations, exceptions, and policy engine stub for EVAL-04."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class TaskStatus(StrEnum):
    """Canonical MOD-03 task lifecycle states (DOC-HLD-002 Section D.1)."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUSPENDING_FOR_USER = "SUSPENDING_FOR_USER"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CANCELLED_AFTER_EXTERNAL_COMMIT = "CANCELLED_AFTER_EXTERNAL_COMMIT"
    EXPIRED = "EXPIRED"


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.CANCELLED_AFTER_EXTERNAL_COMMIT,
        TaskStatus.EXPIRED,
    }
)


class WaitingReason(StrEnum):
    """Reasons a task sits in WAITING_FOR_USER (DOC-HLD-002 Section D.1 & E.5)."""

    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    SECURE_INPUT_REQUEST = "SECURE_INPUT_REQUEST"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"
    PHYSICAL_ACTION_REQUEST = "PHYSICAL_ACTION_REQUEST"
    UNCERTAIN_EXECUTION_RECONCILIATION = "UNCERTAIN_EXECUTION_RECONCILIATION"


class InteractionType(StrEnum):
    """Five centralized HITL interaction types (DOC-HLD-002 Section E.5.2)."""

    INFORMATION_REQUEST = "INFORMATION_REQUEST"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    SECURE_INPUT_REQUEST = "SECURE_INPUT_REQUEST"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"
    PHYSICAL_ACTION_REQUEST = "PHYSICAL_ACTION_REQUEST"


class InteractionStatus(StrEnum):
    """Centralized interaction lifecycle states (DOC-HLD-002 Section E.5.3)."""

    CREATED = "CREATED"
    PENDING_USER = "PENDING_USER"
    TAKEOVER_ACTIVE = "TAKEOVER_ACTIVE"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED_RETRYABLE = "VALIDATION_FAILED_RETRYABLE"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class ApprovalStatus(StrEnum):
    """Cryptographic action-bound approval states (ADR-004, Diagram 6)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


class OutboxEventType(StrEnum):
    """Outbox event types in orbi_app.task_lifecycle_outbox."""

    DELIVER_INTERACTION_PROMPT = "DELIVER_INTERACTION_PROMPT"
    RESUME_TASK = "RESUME_TASK"
    MEMORY_CLARIFICATION_DECIDED = "MEMORY_CLARIFICATION_DECIDED"


class OutboxStatus(StrEnum):
    """Transactional outbox dispatch & lease claim states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PROCESSED = "PROCESSED"
    CANCELLED = "CANCELLED"
    DEAD_LETTERED = "DEAD_LETTERED"


class ReceiptStatus(StrEnum):
    """Durable write-ahead step execution receipt states (DOC-HLD-002 Section C.2)."""

    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE_SAFE_TO_RETRY = "CONFIRMED_FAILURE_SAFE_TO_RETRY"
    CONFIRMED_FAILURE_TERMINAL = "CONFIRMED_FAILURE_TERMINAL"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class ExternalIdempotencyMode(StrEnum):
    """External tool idempotency & reconciliation modes (MOD-07 / MOD-08)."""

    PROVIDER_IDEMPOTENCY_KEY = "PROVIDER_IDEMPOTENCY_KEY"
    RECONCILABLE_READ_PROBE = "RECONCILABLE_READ_PROBE"
    NON_IDEMPOTENT_NO_RECONCILE = "NON_IDEMPOTENT_NO_RECONCILE"


class SensitivityTier(StrEnum):
    """Tool execution sensitivity tiers (MOD-07 / ADR-004)."""

    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    SENSITIVE_WRITE = "SENSITIVE_WRITE"
    IRREVERSIBLE = "IRREVERSIBLE"


class AutomationStatus(StrEnum):
    """Automation lifecycle states (MOD-17 / DOC-HLD-002 Section D.10)."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    PAUSED_SAFETY_TRIP = "PAUSED_SAFETY_TRIP"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"


class Eval04Error(Exception):
    """Base exception for the EVAL-04 prototype."""


class AuthzDeniedError(Eval04Error):
    """403 AUTHZ_DENIED from pre-mutation OpenFGA / policy check."""

    status_code: int = 403


class InteractionConflictError(Eval04Error):
    """409 Conflict from optimistic interaction_version CAS failure."""

    status_code: int = 409


class StaleInteractionRejectedError(Eval04Error):
    """409 STALE_INTERACTION_REJECTED when task state/version changed."""

    status_code: int = 409


class StaleAutomationGenerationRejectedError(Eval04Error):
    """409 STALE_AUTOMATION_GENERATION_REJECTED when automation status/generation drifted."""

    status_code: int = 409


class StaleClaimEpochFencedError(Eval04Error):
    """STALE_CLAIM_EPOCH_FENCED when a zombie worker holds an expired claim_epoch."""


class ApprovalInvalidatedError(Eval04Error):
    """APPROVAL_INVALIDATED when binding digest, target, args, or policy_version drifted."""


class TaskNotResumableError(Eval04Error):
    """Task is terminal, cancelled, expired, or lacks a verified checkpoint."""


class UncertainExecutionEscalation(Eval04Error):
    """Raised when a NON_IDEMPOTENT_NO_RECONCILE action is left in UNKNOWN_OUTCOME."""

    def __init__(
        self,
        message: str,
        *,
        receipt_id: UUID,
        action_idempotency_key: str,
        tool_id: str,
        target_resource: str,
    ) -> None:
        super().__init__(message)
        self.receipt_id = receipt_id
        self.action_idempotency_key = action_idempotency_key
        self.tool_id = tool_id
        self.target_resource = target_resource


class SimulatedCrashError(BaseException):
    """Deterministic crash injection error (inherits BaseException so normal handlers don't swallow it)."""


@dataclass(frozen=True)
class InteractionSubmissionResult:
    """Result returned by HumanInteractionManager.submit_interaction_response."""

    interaction_id: UUID
    interaction_status: InteractionStatus
    interaction_version: int
    task_id: UUID | None
    task_status: TaskStatus | None
    resume_outbox_event_id: UUID | None = None
    resume_token_id: UUID | None = None
    idempotent_replay: bool = False
    validation_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TurnResult:
    """Outcome of a single bounded worker turn."""

    worker_id: str
    task_id: UUID
    execution_id: UUID
    task_status: TaskStatus
    claim_epoch: int
    checkpoint_id: str | None = None
    interaction_id: UUID | None = None
    approval_id: UUID | None = None
    final_answer: str | None = None
    resumed_from_checkpoint: bool = False


class InMemoryPolicyEngine:
    """Minimal deterministic MOD-09 OpenFGA stand-in tracking policy_version and tuples."""

    def __init__(self, initial_policy_version: int = 1) -> None:
        self.policy_version: int = initial_policy_version
        # Set of (actor, relation, object_ref)
        self._tuples: set[tuple[str, str, str]] = set()

    def grant_interaction_role(
        self, actor: str, relation: str, interaction_id: UUID | str
    ) -> None:
        self._tuples.add((actor, relation, f"interaction_request:{interaction_id}"))

    def revoke_interaction_role(
        self, actor: str, relation: str, interaction_id: UUID | str
    ) -> None:
        self._tuples.discard((actor, relation, f"interaction_request:{interaction_id}"))

    def grant_tool_access(
        self, actor: str, tool_id: str, target_resource: str = "*"
    ) -> None:
        self._tuples.add((actor, "execute", f"tool:{tool_id}@{target_resource}"))

    def revoke_tool_access(
        self, actor: str, tool_id: str, target_resource: str = "*"
    ) -> None:
        self._tuples.discard((actor, "execute", f"tool:{tool_id}@{target_resource}"))

    def bump_policy_version(self) -> int:
        self.policy_version += 1
        return self.policy_version

    def check_interaction_actor(
        self, actor: str, relation: str, interaction_id: UUID | str
    ) -> bool:
        return (actor, relation, f"interaction_request:{interaction_id}") in self._tuples

    def check_tool_execute(
        self, actor: str, tool_id: str, target_resource: str
    ) -> bool:
        return (
            (actor, "execute", f"tool:{tool_id}@{target_resource}") in self._tuples
            or (actor, "execute", f"tool:{tool_id}@*") in self._tuples
        )


@dataclass
class TaskRecord:
    """Snapshot of a row in orbi_app.tasks."""

    task_id: UUID
    tenant_id: str
    owner_principal: str
    execution_id: UUID
    goal: str
    status: TaskStatus
    task_version: int
    waiting_reason: str | None
    pending_checkpoint_id: str | None
    verified_checkpoint_id: str | None
    resume_token_id: UUID | None
    claim_epoch: int
    claimed_by_worker_id: str | None
    lease_expires_at: datetime | None
    cancellation_requested: bool
    automation_id: UUID | None
    automation_generation: int | None
    expires_at: datetime | None
    result_payload: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
