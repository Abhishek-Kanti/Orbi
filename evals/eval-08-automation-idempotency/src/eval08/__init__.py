"""Orbi EVAL-08 Automation Idempotency & Version Fencing Spike."""

from eval08.canonical import (
    TriggerType,
    build_execution_identity_payload,
    build_trigger_occurrence_id,
    compute_automation_idempotency_key,
    compute_tool_action_idempotency_key,
    jcs_canonicalize,
)
from eval08.db import (
    ensure_eval08_database,
    get_database_url,
    init_schemas,
    open_connection,
    reset_all_tables,
)
from eval08.engine import (
    AutomationFencingEngine,
    AutomationStatus,
    ClaimOutcome,
    ClaimResult,
    ExternalIdempotencyMode,
    ReceiptStatus,
    SimulatedCrashError,
    StaleClaimEpochFencedError,
    StaleTaskVersionError,
    TaskStatus,
    UncertainOutcomeError,
    naive_select_then_insert_race,
)

__all__ = [
    "AutomationFencingEngine",
    "AutomationStatus",
    "ClaimOutcome",
    "ClaimResult",
    "ExternalIdempotencyMode",
    "ReceiptStatus",
    "SimulatedCrashError",
    "StaleClaimEpochFencedError",
    "StaleTaskVersionError",
    "TaskStatus",
    "TriggerType",
    "UncertainOutcomeError",
    "build_execution_identity_payload",
    "build_trigger_occurrence_id",
    "compute_automation_idempotency_key",
    "compute_tool_action_idempotency_key",
    "ensure_eval08_database",
    "get_database_url",
    "init_schemas",
    "jcs_canonicalize",
    "naive_select_then_insert_race",
    "open_connection",
    "reset_all_tables",
]
