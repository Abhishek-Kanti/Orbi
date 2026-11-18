"""PostgreSQL connection helpers and isolated schema DDL for EVAL-04."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://orbi:orbi_eval04_pass@127.0.0.1:54328/orbi_eval04"


def get_database_url() -> str:
    """Return the PostgreSQL DSN for EVAL-04 (never falls back to SQLite/memory)."""
    return os.environ.get("ORBI_EVAL04_DATABASE_URL", DEFAULT_DATABASE_URL)


@contextmanager
def open_connection(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
    search_path: str = "orbi_app,orbi_automation,public",
) -> Iterator[psycopg.Connection]:
    """Open a real PostgreSQL connection configured with dict_row."""
    conn_dsn = dsn or get_database_url()
    with psycopg.connect(
        conn_dsn,
        autocommit=autocommit,
        row_factory=dict_row,
        options=f"-c search_path={search_path}",
    ) as conn:
        yield conn


DDL_STATEMENTS = """
CREATE SCHEMA IF NOT EXISTS orbi_app;
CREATE SCHEMA IF NOT EXISTS orbi_automation;
CREATE SCHEMA IF NOT EXISTS langgraph_checkpoints;

CREATE TABLE IF NOT EXISTS orbi_app.tasks (
    task_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal TEXT NOT NULL,
    execution_id UUID NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    task_version INTEGER NOT NULL DEFAULT 1,
    waiting_reason TEXT,
    pending_checkpoint_id TEXT,
    verified_checkpoint_id TEXT,
    resume_token_id UUID,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    claimed_by_worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    automation_id UUID,
    automation_generation INTEGER,
    expires_at TIMESTAMPTZ,
    result_payload JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_lease
    ON orbi_app.tasks (status, lease_expires_at);

CREATE TABLE IF NOT EXISTS orbi_app.interaction_requests (
    interaction_id UUID PRIMARY KEY,
    task_id UUID REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    task_version INTEGER,
    execution_id UUID,
    automation_id UUID,
    automation_generation INTEGER,
    verified_checkpoint_id TEXT,
    approval_id UUID,
    interaction_type TEXT NOT NULL,
    requesting_agent TEXT NOT NULL,
    target_principal TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    status TEXT NOT NULL,
    allowed_response_channels TEXT[] NOT NULL,
    resolved_via_channel TEXT,
    prompt_payload JSONB NOT NULL,
    response_schema JSONB NOT NULL,
    sanitized_response_payload JSONB,
    response_idempotency_key TEXT,
    interaction_version INTEGER NOT NULL DEFAULT 1,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_interactions_task_status
    ON orbi_app.interaction_requests (task_id, status);

CREATE TABLE IF NOT EXISTS orbi_app.approval_requests (
    approval_id UUID PRIMARY KEY,
    interaction_id UUID NOT NULL REFERENCES orbi_app.interaction_requests (interaction_id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    execution_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    canonical_arguments_hash TEXT NOT NULL,
    target_resource TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    binding_digest TEXT NOT NULL,
    hmac_signature TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS orbi_app.task_lifecycle_outbox (
    event_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    interaction_id UUID,
    event_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    expected_task_version INTEGER NOT NULL,
    resume_token_id UUID,
    payload JSONB NOT NULL,
    status TEXT NOT NULL,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    claimed_by_worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_status_lease
    ON orbi_app.task_lifecycle_outbox (event_type, status, lease_expires_at);

CREATE TABLE IF NOT EXISTS orbi_app.task_lifecycle_processed_events (
    idempotency_key TEXT PRIMARY KEY,
    event_id UUID NOT NULL,
    task_id UUID NOT NULL,
    processed_by_worker_id TEXT NOT NULL,
    claim_epoch INTEGER NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_app.step_execution_receipts (
    receipt_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    execution_id UUID NOT NULL,
    logical_step_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    canonical_arguments_hash TEXT NOT NULL,
    target_resource TEXT NOT NULL,
    approval_id UUID,
    action_idempotency_key TEXT NOT NULL UNIQUE,
    external_idempotency_mode TEXT NOT NULL,
    reconciliation_probe_tool_id TEXT,
    status TEXT NOT NULL,
    dispatched_claim_epoch INTEGER,
    dispatched_at TIMESTAMPTZ,
    provider_reference_id TEXT,
    sanitized_output JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, execution_id, logical_step_id)
);

CREATE TABLE IF NOT EXISTS orbi_app.audit_events (
    audit_event_id UUID PRIMARY KEY,
    task_id UUID,
    interaction_id UUID,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION orbi_app.prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'orbi_app.audit_events is append-only; UPDATE and DELETE are prohibited';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_update ON orbi_app.audit_events;
CREATE TRIGGER trg_prevent_audit_update
    BEFORE UPDATE OR DELETE ON orbi_app.audit_events
    FOR EACH ROW EXECUTE FUNCTION orbi_app.prevent_audit_mutation();

CREATE TABLE IF NOT EXISTS orbi_automation.automation_definitions (
    automation_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal TEXT NOT NULL,
    status TEXT NOT NULL,
    schedule_version INTEGER NOT NULL DEFAULT 1,
    generation INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_schemas(conn: psycopg.Connection) -> None:
    """Create the isolated schemas, tables, indexes, and append-only audit trigger."""
    with conn.cursor() as cur:
        cur.execute(DDL_STATEMENTS)
    if not conn.autocommit:
        conn.commit()


def reset_all_tables(conn: psycopg.Connection) -> None:
    """Truncate prototype tables between tests (audit_events cleared via TRUNCATE)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE TABLE
                orbi_app.step_execution_receipts,
                orbi_app.task_lifecycle_processed_events,
                orbi_app.task_lifecycle_outbox,
                orbi_app.approval_requests,
                orbi_app.interaction_requests,
                orbi_app.tasks,
                orbi_app.audit_events,
                orbi_automation.automation_definitions
            RESTART IDENTITY CASCADE;
            """
        )
        # Purge LangGraph checkpoint tables if already set up
        cur.execute(
            """
            DO $$
            BEGIN
                IF to_regclass('langgraph_checkpoints.checkpoint_writes') IS NOT NULL THEN
                    TRUNCATE TABLE
                        langgraph_checkpoints.checkpoint_writes,
                        langgraph_checkpoints.checkpoint_blobs,
                        langgraph_checkpoints.checkpoints
                    CASCADE;
                END IF;
            END $$;
            """
        )
    if not conn.autocommit:
        conn.commit()
