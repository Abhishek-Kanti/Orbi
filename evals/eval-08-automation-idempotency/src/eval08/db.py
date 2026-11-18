"""Isolated PostgreSQL database/schema management for EVAL-08."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

DEFAULT_EVAL08_DATABASE_URL = (
    "postgresql://orbi:orbi_eval04_pass@127.0.0.1:54328/orbi_eval08"
)
DEFAULT_ADMIN_DATABASE_URL = (
    "postgresql://orbi:orbi_eval04_pass@127.0.0.1:54328/postgres"
)


def get_database_url() -> str:
    """Return the PostgreSQL DSN for EVAL-08 (always real PostgreSQL)."""
    return os.environ.get("ORBI_EVAL08_DATABASE_URL", DEFAULT_EVAL08_DATABASE_URL)


def ensure_eval08_database() -> None:
    """Create isolated database `orbi_eval08` if it does not yet exist."""
    admin_dsn = os.environ.get("ORBI_EVAL08_ADMIN_URL", DEFAULT_ADMIN_DATABASE_URL)
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'orbi_eval08'")
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE orbi_eval08 OWNER orbi")


@contextmanager
def open_connection(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
    search_path: str = "orbi_automation,orbi_app,public",
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
CREATE SCHEMA IF NOT EXISTS orbi_automation;
CREATE SCHEMA IF NOT EXISTS orbi_app;

CREATE TABLE IF NOT EXISTS orbi_automation.automation_definitions (
    automation_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal TEXT NOT NULL,
    creator_principal TEXT NOT NULL,
    execution_target TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_spec JSONB NOT NULL,
    task_template JSONB NOT NULL,
    status TEXT NOT NULL,
    schedule_version INTEGER NOT NULL DEFAULT 1,
    generation INTEGER NOT NULL DEFAULT 1,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_automation.automation_schedule_versions (
    version_id UUID PRIMARY KEY,
    automation_id UUID NOT NULL REFERENCES orbi_automation.automation_definitions (automation_id) ON DELETE CASCADE,
    schedule_version INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    changed_by_principal TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    trigger_spec JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (automation_id, schedule_version)
);

CREATE TABLE IF NOT EXISTS orbi_automation.automation_condition_monitors (
    monitor_id TEXT PRIMARY KEY,
    automation_id UUID NOT NULL REFERENCES orbi_automation.automation_definitions (automation_id) ON DELETE CASCADE,
    last_state BOOLEAN NOT NULL DEFAULT FALSE,
    rising_edge_seq INTEGER NOT NULL DEFAULT 0,
    poll_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_automation.automation_executions (
    execution_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    automation_id UUID NOT NULL REFERENCES orbi_automation.automation_definitions (automation_id) ON DELETE CASCADE,
    schedule_version INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_occurrence_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    task_id UUID,
    claim_epoch INTEGER NOT NULL DEFAULT 1,
    claimed_by_worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_automation.automation_outbox (
    event_id UUID PRIMARY KEY,
    automation_id UUID NOT NULL REFERENCES orbi_automation.automation_definitions (automation_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    schedule_version INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_automation.automation_processed_events (
    event_key TEXT PRIMARY KEY,
    automation_id UUID NOT NULL,
    outcome TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unconstrained scratch table used ONLY to prove SELECT-then-INSERT concurrency race
CREATE TABLE IF NOT EXISTS orbi_automation.naive_race_executions (
    row_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_app.tasks (
    task_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_principal TEXT NOT NULL,
    execution_id UUID NOT NULL UNIQUE,
    automation_id UUID,
    automation_generation INTEGER,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    task_version INTEGER NOT NULL DEFAULT 1,
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    claimed_by_worker_id TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_app.interaction_requests (
    interaction_id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    task_version INTEGER NOT NULL,
    execution_id UUID NOT NULL,
    automation_id UUID,
    automation_generation INTEGER,
    interaction_type TEXT NOT NULL,
    status TEXT NOT NULL,
    interaction_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orbi_app.approval_requests (
    approval_id UUID PRIMARY KEY,
    interaction_id UUID NOT NULL REFERENCES orbi_app.interaction_requests (interaction_id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    expected_task_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS orbi_app.step_execution_receipts (
    receipt_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id UUID NOT NULL REFERENCES orbi_app.tasks (task_id) ON DELETE CASCADE,
    execution_id UUID NOT NULL,
    logical_step_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    target_resource TEXT NOT NULL,
    action_idempotency_key TEXT NOT NULL UNIQUE,
    external_idempotency_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_reference_id TEXT,
    sanitized_output JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, execution_id, logical_step_id)
);

CREATE TABLE IF NOT EXISTS orbi_app.audit_events (
    audit_event_id UUID PRIMARY KEY,
    task_id UUID,
    automation_id UUID,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION orbi_app.prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'orbi_app.audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_mutation ON orbi_app.audit_events;
CREATE TRIGGER trg_prevent_audit_mutation
    BEFORE UPDATE OR DELETE ON orbi_app.audit_events
    FOR EACH ROW EXECUTE FUNCTION orbi_app.prevent_audit_mutation();
"""


def init_schemas(conn: psycopg.Connection) -> None:
    """Create isolated schemas and tables for EVAL-08."""
    with conn.cursor() as cur:
        cur.execute(DDL_STATEMENTS)
    if not conn.autocommit:
        conn.commit()


def reset_all_tables(conn: psycopg.Connection) -> None:
    """Clean all EVAL-08 tables between test runs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE TABLE
                orbi_app.step_execution_receipts,
                orbi_app.approval_requests,
                orbi_app.interaction_requests,
                orbi_app.tasks,
                orbi_app.audit_events,
                orbi_automation.naive_race_executions,
                orbi_automation.automation_processed_events,
                orbi_automation.automation_outbox,
                orbi_automation.automation_executions,
                orbi_automation.automation_condition_monitors,
                orbi_automation.automation_schedule_versions,
                orbi_automation.automation_definitions
            RESTART IDENTITY CASCADE;
            """
        )
    if not conn.autocommit:
        conn.commit()
