"""Automated verification tests for EVAL-04 (Experiments 1-13 + Latency Benchmarks)."""

from __future__ import annotations

import gc
import json
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from langgraph.types import Command

from eval04.checkpointer import OrbiPostgresSaver
from eval04.db import get_database_url, init_schemas, open_connection, reset_all_tables
from eval04.gateway import (
    ToolDefinition,
    ToolExecutionGateway,
    ToolRegistry,
    compute_action_idempotency_key,
    compute_canonical_arguments_hash,
)
from eval04.models import (
    ApprovalInvalidatedError,
    ApprovalStatus,
    AuthzDeniedError,
    AutomationStatus,
    ExternalIdempotencyMode,
    InMemoryPolicyEngine,
    InteractionConflictError,
    InteractionStatus,
    InteractionType,
    OutboxEventType,
    OutboxStatus,
    ReceiptStatus,
    SensitivityTier,
    SimulatedCrashError,
    StaleAutomationGenerationRejectedError,
    StaleClaimEpochFencedError,
    StaleInteractionRejectedError,
    TaskNotResumableError,
    TaskStatus,
    WaitingReason,
)
from eval04.runtime import Worker
from eval04.task_manager import CrashReconciler, DeterministicTaskManager


@pytest.fixture(scope="session")
def db_dsn() -> str:
    dsn = get_database_url()
    with open_connection(dsn) as conn:
        init_schemas(conn)
    setup_saver = OrbiPostgresSaver.create(dsn)
    setup_saver.close()
    return dsn


@pytest.fixture(autouse=True)
def clean_db(db_dsn: str) -> None:
    with open_connection(db_dsn) as conn:
        reset_all_tables(conn)


def _make_harness(dsn: str) -> tuple[
    InMemoryPolicyEngine,
    ToolRegistry,
    ToolExecutionGateway,
    DeterministicTaskManager,
    CrashReconciler,
]:
    policy = InMemoryPolicyEngine(initial_policy_version=1)
    registry = ToolRegistry()
    gateway = ToolExecutionGateway(registry=registry, policy_engine=policy, dsn=dsn)
    task_mgr = DeterministicTaskManager(policy_engine=policy, dsn=dsn)
    reconciler = CrashReconciler(task_mgr, dsn=dsn)
    return policy, registry, gateway, task_mgr, reconciler


def _register_email_tool(
    registry: ToolRegistry,
    policy: InMemoryPolicyEngine,
    gateway: ToolExecutionGateway,
    *,
    mode: ExternalIdempotencyMode = ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
    sensitivity: SensitivityTier = SensitivityTier.SENSITIVE_WRITE,
    probe_tool_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    provider_store: dict[str, dict[str, Any]] = {}

    registry.register_tool(
        ToolDefinition(
            tool_id="gmail.send_email",
            tool_version="1.0.0",
            sensitivity_tier=sensitivity,
            semantic_fields=("to", "subject", "body"),
            external_idempotency_mode=mode,
            idempotency_window_seconds=3600,
            reconciliation_probe_tool_id=probe_tool_id,
        )
    )
    policy.grant_tool_access("user:owner", "gmail.send_email", "res:gmail_primary")

    def provider(*, action_idempotency_key: str, arguments: dict, target_resource: str) -> dict:
        if mode == ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY:
            if action_idempotency_key in provider_store:
                return provider_store[action_idempotency_key]
        calls.append(
            {
                "action_idempotency_key": action_idempotency_key,
                "arguments": dict(arguments),
                "target_resource": target_resource,
            }
        )
        res = {
            "provider_reference_id": f"msg-{len(calls)}",
            "sent_to": arguments.get("to"),
            "status": "sent",
        }
        provider_store[action_idempotency_key] = res
        return res

    gateway.register_provider("gmail.send_email", provider)

    if probe_tool_id:
        def probe(*, action_idempotency_key: str, arguments: dict, target_resource: str) -> dict:
            if action_idempotency_key in provider_store:
                return {"executed": True, "output": provider_store[action_idempotency_key]}
            return {"executed": False}

        gateway.register_probe(probe_tool_id, probe)

    return calls, provider_store


# ---------------------------------------------------------------------------
# Experiment 1: Execute a simple LangGraph task and persist its checkpoint
# ---------------------------------------------------------------------------
def test_exp01_simple_langgraph_task_and_checkpoint_persistence(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls, _ = _register_email_tool(
        registry,
        policy,
        gateway,
        mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
        sensitivity=SensitivityTier.LOW_RISK_WRITE,
    )

    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Send routine status ping",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    res = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": False,
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "ops@example.com", "subject": "Ping", "body": "OK"},
            "target_resource": "res:gmail_primary",
        },
    )
    assert res.task_status == TaskStatus.COMPLETED
    assert len(calls) == 1
    assert res.checkpoint_id is not None

    # Verify checkpoints were physically persisted in PostgreSQL langgraph_checkpoints
    ckpt_count = w1.saver.count_checkpoints_for_thread(str(task.task_id))
    assert ckpt_count >= 2

    # Terminal cleanup purges orphaned checkpoints
    purged = reconciler.purge_orphaned_checkpoints(w1.saver)
    assert purged >= 2
    assert w1.saver.count_checkpoints_for_thread(str(task.task_id)) == 0
    w1.close()


# ---------------------------------------------------------------------------
# Experiments 2 & 3: Suspend for human input + PG authoritative state
# ---------------------------------------------------------------------------
def test_exp02_03_suspend_and_persist_authoritative_pg_state(db_dsn: str) -> None:
    _, _, gateway, task_mgr, _ = _make_harness(db_dsn)
    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Book train ticket",
    )

    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    res = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Which train class would you prefer?"},
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string", "enum": ["1A", "2A"]}},
                "required": ["answer"],
            },
        },
    )
    assert res.task_status == TaskStatus.WAITING_FOR_USER
    assert res.checkpoint_id is not None
    assert res.interaction_id is not None

    # Verify LangGraph checkpoint + __interrupt__ in langgraph_checkpoints
    assert w1.saver.verify_checkpoint(
        str(task.task_id), res.checkpoint_id, require_interrupt=True
    )

    # Verify authoritative PG state in orbi_app
    pg_task = task_mgr.get_task(task.task_id)
    assert pg_task.status == TaskStatus.WAITING_FOR_USER
    assert pg_task.waiting_reason == WaitingReason.INFORMATION_REQUEST.value
    assert pg_task.verified_checkpoint_id == res.checkpoint_id
    assert pg_task.task_version == 2

    pg_inter = task_mgr.get_interaction(res.interaction_id)
    assert pg_inter["status"] == InteractionStatus.PENDING_USER.value
    assert pg_inter["interaction_version"] == 1
    assert pg_inter["verified_checkpoint_id"] == res.checkpoint_id

    outbox = task_mgr.get_outbox_events(task.task_id, OutboxEventType.DELIVER_INTERACTION_PROMPT)
    assert len(outbox) == 1
    assert outbox[0]["status"] == OutboxStatus.PENDING.value

    # Verify audit_events table is append-only (UPDATE/DELETE rejected by PG trigger)
    with open_connection(db_dsn) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute(
                    "UPDATE orbi_app.audit_events SET event_type = 'TAMPERED' WHERE task_id = %s",
                    (task.task_id,),
                )
        conn.rollback()
    w1.close()


# ---------------------------------------------------------------------------
# Experiments 4 & 5: Release worker completely, then resume on different worker
# ---------------------------------------------------------------------------
def test_exp04_05_release_worker_and_resume_on_different_worker(db_dsn: str) -> None:
    _, _, gateway, task_mgr, _ = _make_harness(db_dsn)
    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Ask preference and conclude",
    )

    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Which class?"},
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string", "enum": ["1A", "2A"]}},
                "required": ["answer"],
            },
        },
    )
    interaction_id = r1.interaction_id
    assert interaction_id is not None

    # Exp 4: Worker completely released while waiting
    w1.close()
    assert w1.saver.conn.closed
    del w1
    gc.collect()

    pg_waiting = task_mgr.get_task(task.task_id)
    assert pg_waiting.status == TaskStatus.WAITING_FOR_USER
    assert pg_waiting.claimed_by_worker_id is None
    assert pg_waiting.lease_expires_at is None

    # Also test schema validation failure -> VALIDATION_FAILED_RETRYABLE -> valid retry
    bad_sub = task_mgr.submit_interaction_response(
        interaction_id=interaction_id,
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "INVALID_CLASS"},
        response_idempotency_key="resp-bad-1",
    )
    assert bad_sub.interaction_status == InteractionStatus.VALIDATION_FAILED_RETRYABLE
    assert bad_sub.interaction_version == 4
    assert task_mgr.get_task(task.task_id).status == TaskStatus.WAITING_FOR_USER

    # Exp 5: Valid response + resume on completely fresh worker-2
    good_sub = task_mgr.submit_interaction_response(
        interaction_id=interaction_id,
        actor_principal="user:owner",
        channel="dashboard",
        payload={"answer": "1A"},
        response_idempotency_key="resp-good-2",
    )
    assert good_sub.interaction_status == InteractionStatus.RESOLVED
    assert good_sub.task_status == TaskStatus.QUEUED
    assert good_sub.resume_outbox_event_id is not None
    assert good_sub.resume_token_id is not None

    claimed = task_mgr.claim_resume_outbox_event(
        event_id=good_sub.resume_outbox_event_id,
        worker_id="worker-2",
        expected_claim_epoch=0,
    )
    assert claimed is not None
    assert claimed.claim_epoch == 1

    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(claimed)
    w2.close()

    assert r2.task_status == TaskStatus.COMPLETED
    assert r2.resumed_from_checkpoint is True
    assert "'answer': '1A'" in (r2.final_answer or "")

    final_task = task_mgr.get_task(task.task_id)
    assert final_task.status == TaskStatus.COMPLETED
    assert final_task.resume_token_id is None
    outboxes = task_mgr.get_outbox_events(task.task_id, OutboxEventType.RESUME_TASK)
    assert outboxes[0]["status"] == OutboxStatus.PROCESSED.value


# ---------------------------------------------------------------------------
# Experiment 6: PG authoritative & LangGraph checkpoints subordinate
# ---------------------------------------------------------------------------
def test_exp06_postgres_authoritative_and_checkpoints_subordinate(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, _ = _make_harness(db_dsn)
    calls, _ = _register_email_tool(registry, policy, gateway)

    task = task_mgr.create_task(
        tenant_id="tenant:household",
        owner_principal="user:owner",
        goal="Send sensitive email",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.APPROVAL_REQUEST.value,
            "hitl_prompt": {"question": "Send email to alice@example.com?"},
            "response_schema": {
                "type": "object",
                "properties": {"decision": {"type": "string", "enum": ["APPROVE", "REJECT"]}},
                "required": ["decision"],
            },
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "alice@example.com", "subject": "Hi", "body": "Approved text"},
            "target_resource": "res:gmail_primary",
        },
    )
    w1.close()
    assert r1.interaction_id is not None
    assert r1.approval_id is not None

    # 6a. Unauthorized household member fails Step 0 OpenFGA with ZERO mutation
    with pytest.raises(AuthzDeniedError):
        task_mgr.submit_interaction_response(
            interaction_id=r1.interaction_id,
            actor_principal="user:household_member",
            channel="telegram",
            payload={"decision": "REJECT"},
            response_idempotency_key="unauth-1",
        )
    untouched = task_mgr.get_interaction(r1.interaction_id)
    assert untouched["status"] == InteractionStatus.PENDING_USER.value
    assert untouched["interaction_version"] == 1
    assert task_mgr.get_approval(r1.approval_id)["status"] == ApprovalStatus.PENDING.value

    # 6b. Attempting to resume the LangGraph checkpoint directly without PG authorization fails
    rogue = Worker("rogue-worker", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(StaleClaimEpochFencedError):
        rogue.runtime.graph.invoke(
            Command(resume={"decision": "APPROVE"}),
            config={
                "configurable": {
                    "thread_id": str(task.task_id),
                    "checkpoint_ns": "",
                    "checkpoint_id": r1.checkpoint_id,
                    "worker_id": "rogue-worker",
                    "claim_epoch": 1,
                    "approval_id": str(r1.approval_id),
                }
            },
        )
    rogue.close()
    assert len(calls) == 0

    # 6c. Legitimate owner approves, then args mutate -> Gate 5 INVALIDATES approval
    sub = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,
        actor_principal="user:owner",
        channel="telegram",
        payload={"decision": "APPROVE"},
        response_idempotency_key="owner-approve-1",
    )
    assert sub.resume_outbox_event_id is not None
    claimed = task_mgr.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,
        worker_id="worker-2",
        expected_claim_epoch=0,
    )
    assert claimed is not None

    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(ApprovalInvalidatedError):
        w2.run_resumed_turn(
            claimed,
            override_tool_args={
                "to": "attacker@evil.com",
                "subject": "Hi",
                "body": "Mutated body",
            },
        )
    w2.close()
    assert len(calls) == 0
    assert task_mgr.get_approval(r1.approval_id)["status"] == ApprovalStatus.INVALIDATED.value
    assert task_mgr.get_interaction(r1.interaction_id)["status"] == InteractionStatus.INVALIDATED.value


# ---------------------------------------------------------------------------
# Experiment 7: Duplicate outbox delivery & idempotent resume handling
# ---------------------------------------------------------------------------
def test_exp07_duplicate_outbox_delivery_and_idempotent_resume(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, _ = _make_harness(db_dsn)
    calls, _ = _register_email_tool(
        registry, policy, gateway, sensitivity=SensitivityTier.LOW_RISK_WRITE
    )
    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Idempotent outbox test",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Confirm recipient"},
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "bob@example.com", "subject": "S", "body": "B"},
            "target_resource": "res:gmail_primary",
        },
    )
    w1.close()
    assert r1.interaction_id is not None

    # Duplicate identical response submission returns idempotent replay
    sub1 = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "bob@example.com"},
        response_idempotency_key="same-key-1",
    )
    sub2 = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "bob@example.com"},
        response_idempotency_key="same-key-1",
    )
    assert sub1.idempotent_replay is False
    assert sub2.idempotent_replay is True
    assert sub1.resume_outbox_event_id == sub2.resume_outbox_event_id

    # Stale expected_version / different idempotency key fails CAS with 409
    with pytest.raises(InteractionConflictError):
        task_mgr.submit_interaction_response(
            interaction_id=r1.interaction_id,
            actor_principal="user:owner",
            channel="dashboard",
            payload={"answer": "bob@example.com"},
            response_idempotency_key="different-key-2",
            expected_version=1,
        )

    assert len(task_mgr.get_outbox_events(task.task_id, OutboxEventType.RESUME_TASK)) == 1
    event_id = sub1.resume_outbox_event_id
    assert event_id is not None

    # Two workers receive duplicate Redis delivery of identical event_id
    claim_a = task_mgr.claim_resume_outbox_event(
        event_id=event_id, worker_id="worker-2a", expected_claim_epoch=0
    )
    claim_b = task_mgr.claim_resume_outbox_event(
        event_id=event_id, worker_id="worker-2b", expected_claim_epoch=0
    )
    assert claim_a is not None
    assert claim_b is None

    w2 = Worker("worker-2a", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(claim_a)
    w2.close()
    assert r2.task_status == TaskStatus.COMPLETED
    assert len(calls) == 1

    # Post-completion duplicate redelivery is rejected by task_lifecycle_processed_events
    claim_c = task_mgr.claim_resume_outbox_event(
        event_id=event_id, worker_id="worker-2c", expected_claim_epoch=1
    )
    assert claim_c is None
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Experiment 8: Crash between checkpoint save and PG suspension commit
# ---------------------------------------------------------------------------
def test_exp08_crash_between_checkpoint_and_pg_suspension_commit(db_dsn: str) -> None:
    _, _, gateway, task_mgr, reconciler = _make_harness(db_dsn)

    # 8a: Checkpoint saved in Phase B, worker crashes BEFORE Phase C PG commit -> roll forward
    task_a = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Crash before Phase C",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1.run_initial_turn(
            task_a.task_id,
            initial_state={
                "requires_hitl": True,
                "hitl_type": InteractionType.INFORMATION_REQUEST.value,
                "hitl_prompt": {"question": "Confirm step A?"},
                "response_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            },
            crash_before_phase_c=True,
        )
    w1.close()

    mid_a = task_mgr.get_task(task_a.task_id)
    assert mid_a.status == TaskStatus.SUSPENDING_FOR_USER
    assert mid_a.pending_checkpoint_id is not None

    rec_saver = OrbiPostgresSaver.create(db_dsn)
    summary_a = reconciler.reconcile_incomplete_suspensions(rec_saver, ignore_lease_ttl=True)
    assert summary_a["rolled_forward"] == 1
    assert summary_a["rolled_back"] == 0

    after_a = task_mgr.get_task(task_a.task_id)
    assert after_a.status == TaskStatus.WAITING_FOR_USER
    assert after_a.verified_checkpoint_id == mid_a.pending_checkpoint_id

    # Verify the rolled-forward task actually resumes cleanly!
    with open_connection(db_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT interaction_id FROM orbi_app.interaction_requests WHERE task_id = %s",
                (task_a.task_id,),
            )
            inter_id_a = cur.fetchone()["interaction_id"]
    sub_a = task_mgr.submit_interaction_response(
        interaction_id=inter_id_a,
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "yes"},
        response_idempotency_key="exp8a-ok",
    )
    claimed_a = task_mgr.claim_resume_outbox_event(
        event_id=sub_a.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=0,
    )
    assert claimed_a is not None
    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2_a = w2.run_resumed_turn(claimed_a)
    w2.close()
    assert r2_a.task_status == TaskStatus.COMPLETED

    # 8b: Crash during Phase B checkpoint write (partial write without __interrupt__) -> roll back
    task_b = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Crash during Phase B put_writes",
    )
    w_fail = Worker("worker-fail", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    w_fail.saver.fail_on_put_writes_interrupt = True
    with pytest.raises(SimulatedCrashError):
        w_fail.run_initial_turn(
            task_b.task_id,
            initial_state={
                "requires_hitl": True,
                "hitl_type": InteractionType.INFORMATION_REQUEST.value,
                "hitl_prompt": {"question": "Confirm step B?"},
                "response_schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            },
        )
    w_fail.close()

    mid_b = task_mgr.get_task(task_b.task_id)
    assert mid_b.status == TaskStatus.SUSPENDING_FOR_USER
    assert mid_b.status != TaskStatus.WAITING_FOR_USER

    summary_b = reconciler.reconcile_incomplete_suspensions(rec_saver, ignore_lease_ttl=True)
    assert summary_b["rolled_back"] == 1

    after_b = task_mgr.get_task(task_b.task_id)
    assert after_b.status == TaskStatus.QUEUED
    assert rec_saver.count_checkpoints_for_thread(str(task_b.task_id)) == 0
    rec_saver.close()


# ---------------------------------------------------------------------------
# Experiment 9: Crash after interaction resolution before resume processing
# ---------------------------------------------------------------------------
def test_exp09_crash_after_interaction_resolution_before_resume(db_dsn: str) -> None:
    _, _, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Crash before resume dispatch",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Input?"},
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    )
    w1.close()

    sub = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "continue"},
        response_idempotency_key="exp9-1",
    )
    assert sub.task_status == TaskStatus.QUEUED

    # Simulate dispatcher/Redis crash before publishing RESUME_TASK: reconciler finds PENDING outbox
    rec = reconciler.reconcile_outbox_and_stale_leases()
    assert len(rec["claimable"]) == 1
    ev_id, exp_epoch = rec["claimable"][0]
    assert ev_id == sub.resume_outbox_event_id
    assert exp_epoch == 0

    claimed = task_mgr.claim_resume_outbox_event(
        event_id=ev_id, worker_id="worker-2", expected_claim_epoch=exp_epoch
    )
    assert claimed is not None and claimed.claim_epoch == 1
    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(claimed)
    w2.close()
    assert r2.task_status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Experiment 10: Bounded worker leases, claim_epoch fencing & stale worker
# ---------------------------------------------------------------------------
def test_exp10_bounded_worker_leases_claim_epoch_fencing(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls, _ = _register_email_tool(
        registry, policy, gateway, sensitivity=SensitivityTier.LOW_RISK_WRITE
    )
    task = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Fencing test",
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Proceed?"},
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "c@example.com", "subject": "Fence", "body": "Body"},
            "target_resource": "res:gmail_primary",
        },
    )
    w1.close()

    sub = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "yes"},
        response_idempotency_key="exp10-1",
    )
    assert sub.resume_outbox_event_id is not None

    # W1 claims with short 0.15s lease -> epoch 1, then stalls past expiry
    claimed_w1 = task_mgr.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.15,
    )
    assert claimed_w1 is not None and claimed_w1.claim_epoch == 1
    time.sleep(0.30)

    # Reconciler detects expired lease; W2 reclaims at claim_epoch=2
    rec = reconciler.reconcile_outbox_and_stale_leases()
    assert (sub.resume_outbox_event_id, 1) in rec["claimable"]
    claimed_w2 = task_mgr.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,
        worker_id="worker-2",
        expected_claim_epoch=1,
        lease_ttl_seconds=30.0,
    )
    assert claimed_w2 is not None and claimed_w2.claim_epoch == 2

    # Stale zombie W1 wakes up holding claim_epoch=1 -> fenced everywhere!
    with pytest.raises(StaleClaimEpochFencedError):
        task_mgr.renew_worker_lease(
            task_id=task.task_id, worker_id="worker-1", claim_epoch=1
        )
    w1_zombie = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(StaleClaimEpochFencedError):
        w1_zombie.run_resumed_turn(claimed_w1)
    w1_zombie.close()
    assert len(calls) == 0  # Gateway Gate 1.5/5 fenced zombie before external dispatch

    with pytest.raises(StaleClaimEpochFencedError):
        task_mgr.complete_task_turn(
            task_id=task.task_id,
            worker_id="worker-1",
            claim_epoch=1,
            result_payload={"final_answer": "zombie"},
            outbox_event_id=sub.resume_outbox_event_id,
            resume_token_id=claimed_w1.resume_token_id,
        )

    # Replacement W2 (claim_epoch=2) completes cleanly
    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(claimed_w2)
    w2.close()
    assert r2.task_status == TaskStatus.COMPLETED
    assert len(calls) == 1
    audits = [e["event_type"] for e in task_mgr.get_audit_events(task.task_id)]
    assert "STALE_CLAIM_EPOCH_FENCED" in audits


# ---------------------------------------------------------------------------
# Experiment 11: Crashes after worker claims a resume event
# ---------------------------------------------------------------------------
def test_exp11_crashes_after_worker_claims_resume_event(db_dsn: str) -> None:
    _, _, gateway, task_mgr, reconciler = _make_harness(db_dsn)

    # 11a: Crash mid-turn before completion commit -> W2 reclaims & finishes
    t1 = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Mid-resume crash"
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        t1.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Q?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )
    w1.close()
    sub1 = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "ok"},
        response_idempotency_key="exp11a",
    )
    c1 = task_mgr.claim_resume_outbox_event(
        event_id=sub1.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.15,
    )
    assert c1 is not None
    w1_res = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1_res.run_resumed_turn(c1, crash_before_completion_commit=True)
    w1_res.close()
    assert task_mgr.get_task(t1.task_id).resume_token_id == sub1.resume_token_id

    time.sleep(0.25)
    rec = reconciler.reconcile_outbox_and_stale_leases()
    assert (sub1.resume_outbox_event_id, 1) in rec["claimable"]
    c2 = task_mgr.claim_resume_outbox_event(
        event_id=sub1.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=1,
    )
    assert c2 is not None and c2.claim_epoch == 2
    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(c2)
    w2.close()
    assert r2.task_status == TaskStatus.COMPLETED

    # 11b: Crash AFTER task_lifecycle_processed_events commit BEFORE outbox ack -> zero re-runs
    t2 = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Pre-ack crash"
    )
    w1b = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1b = w1b.run_initial_turn(
        t2.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Q?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )
    w1b.close()
    sub2 = task_mgr.submit_interaction_response(
        interaction_id=r1b.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "ok"},
        response_idempotency_key="exp11b",
    )
    c2_claim = task_mgr.claim_resume_outbox_event(
        event_id=sub2.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.15,
    )
    assert c2_claim is not None
    w1b_res = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1b_res.run_resumed_turn(c2_claim, crash_after_processed_before_ack=True)
    w1b_res.close()

    # Outbox is still RUNNING, yet task is COMPLETED & processed_events recorded
    assert task_mgr.get_task(t2.task_id).status == TaskStatus.COMPLETED
    assert task_mgr.get_outbox_events(t2.task_id, OutboxEventType.RESUME_TASK)[0]["status"] == OutboxStatus.RUNNING.value

    time.sleep(0.25)
    rec2 = reconciler.reconcile_outbox_and_stale_leases()
    assert rec2["acked_already_processed"] == 1
    assert rec2["claimable"] == []
    assert task_mgr.get_outbox_events(t2.task_id, OutboxEventType.RESUME_TASK)[0]["status"] == OutboxStatus.PROCESSED.value

    # 11c: max_attempts exhausted -> DEAD_LETTERED & task FAILED
    t3 = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Dead letter test"
    )
    w1c = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1c = w1c.run_initial_turn(
        t3.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Q?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )
    w1c.close()
    sub3 = task_mgr.submit_interaction_response(
        interaction_id=r1c.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "ok"},
        response_idempotency_key="exp11c",
    )
    c3 = task_mgr.claim_resume_outbox_event(
        event_id=sub3.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.10,
    )
    assert c3 is not None
    # Exhaust max_attempts on expired lease
    with open_connection(db_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE orbi_app.task_lifecycle_outbox SET attempt_count = max_attempts WHERE event_id = %s",
                (sub3.resume_outbox_event_id,),
            )
        conn.commit()
    time.sleep(0.20)
    rec3 = reconciler.reconcile_outbox_and_stale_leases()
    assert rec3["dead_lettered"] == 1
    assert task_mgr.get_outbox_events(t3.task_id, OutboxEventType.RESUME_TASK)[0]["status"] == OutboxStatus.DEAD_LETTERED.value
    failed_t3 = task_mgr.get_task(t3.task_id)
    assert failed_t3.status == TaskStatus.FAILED
    assert failed_t3.error_code == "RESUME_RETRIES_EXHAUSTED"


# ---------------------------------------------------------------------------
# Experiment 12: External action recovery, receipt & UNKNOWN_OUTCOME
# ---------------------------------------------------------------------------
def test_exp12_external_action_recovery_and_unknown_outcome(db_dsn: str) -> None:
    # 12a & 12b: Key stability + CONFIRMED_SUCCESS replay + PROVIDER_IDEMPOTENCY_KEY dedup
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls_idemp, _ = _register_email_tool(
        registry,
        policy,
        gateway,
        mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
        sensitivity=SensitivityTier.SENSITIVE_WRITE,
    )
    task = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Send email idempotent"
    )
    args = {"to": "a@example.com", "subject": "Subj", "body": "Msg"}
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.APPROVAL_REQUEST.value,
            "hitl_prompt": {"question": "Approve send?"},
            "response_schema": {"type": "object", "properties": {"decision": {"type": "string"}}},
            "tool_id": "gmail.send_email",
            "tool_args": args,
            "target_resource": "res:gmail_primary",
        },
    )
    w1.close()
    sub = task_mgr.submit_interaction_response(
        interaction_id=r1.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"decision": "APPROVE"},
        response_idempotency_key="exp12b-approve",
    )
    c1 = task_mgr.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.80,
    )
    assert c1 is not None

    # Inject crash AFTER external provider succeeds, BEFORE CONFIRMED_SUCCESS commit
    gateway.crash_after_provider_before_receipt_commit = True
    w1_res = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1_res.run_resumed_turn(c1)
    w1_res.close()
    gateway.crash_after_provider_before_receipt_commit = False

    assert len(calls_idemp) == 1
    receipt_after_crash = task_mgr.get_receipt(task.task_id, "step-tool-1")
    assert receipt_after_crash is not None
    assert receipt_after_crash["status"] == ReceiptStatus.DISPATCHING.value
    expected_key = compute_action_idempotency_key(
        tenant_id="tenant:personal",
        task_id=task.task_id,
        execution_id=task.execution_id,
        logical_step_id="step-tool-1",
        tool_id="gmail.send_email",
        tool_version="1.0.0",
        canonical_arguments_hash=compute_canonical_arguments_hash(args, ("to", "subject", "body")),
        target_resource="res:gmail_primary",
        approval_id=r1.approval_id,
    )
    assert receipt_after_crash["action_idempotency_key"] == expected_key

    time.sleep(0.95)
    rec = reconciler.reconcile_outbox_and_stale_leases()
    assert rec["dispatched_marked_unknown"] == 1
    assert task_mgr.get_receipt(task.task_id, "step-tool-1")["status"] == ReceiptStatus.UNKNOWN_OUTCOME.value

    # Replacement W2 claims (claim_epoch=2), retries with IDENTICAL action_idempotency_key -> provider deduplicates!
    c2 = task_mgr.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=1,
    )
    assert c2 is not None and c2.claim_epoch == 2
    w2 = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2 = w2.run_resumed_turn(c2)
    w2.close()
    assert r2.task_status == TaskStatus.COMPLETED
    assert len(calls_idemp) == 1  # Zero duplicate external side effects!
    final_receipt = task_mgr.get_receipt(task.task_id, "step-tool-1")
    assert final_receipt is not None
    assert final_receipt["status"] == ReceiptStatus.CONFIRMED_SUCCESS.value
    assert final_receipt["action_idempotency_key"] == expected_key

    # 12c: RECONCILABLE_READ_PROBE reconciles without re-dispatching
    with open_connection(db_dsn) as conn:
        reset_all_tables(conn)
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls_probe, _ = _register_email_tool(
        registry,
        policy,
        gateway,
        mode=ExternalIdempotencyMode.RECONCILABLE_READ_PROBE,
        sensitivity=SensitivityTier.SENSITIVE_WRITE,
        probe_tool_id="gmail.check_sent",
    )
    task_p = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Probe reconcile"
    )
    w1p = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1p = w1p.run_initial_turn(
        task_p.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.APPROVAL_REQUEST.value,
            "hitl_prompt": {"question": "Approve probe test?"},
            "response_schema": {"type": "object", "properties": {"decision": {"type": "string"}}},
            "tool_id": "gmail.send_email",
            "tool_args": args,
            "target_resource": "res:gmail_primary",
        },
    )
    w1p.close()
    subp = task_mgr.submit_interaction_response(
        interaction_id=r1p.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"decision": "APPROVE"},
        response_idempotency_key="exp12c-approve",
    )
    cp1 = task_mgr.claim_resume_outbox_event(
        event_id=subp.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.80,
    )
    assert cp1 is not None
    gateway.crash_after_provider_before_receipt_commit = True
    w1p_res = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1p_res.run_resumed_turn(cp1)
    w1p_res.close()
    gateway.crash_after_provider_before_receipt_commit = False

    time.sleep(0.95)
    reconciler.reconcile_outbox_and_stale_leases()
    cp2 = task_mgr.claim_resume_outbox_event(
        event_id=subp.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=1,
    )
    assert cp2 is not None
    w2p = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2p = w2p.run_resumed_turn(cp2)
    w2p.close()
    assert r2p.task_status == TaskStatus.COMPLETED
    assert len(calls_probe) == 1
    assert "EXTERNAL_OUTCOME_RECONCILED_SUCCESS" in [
        e["event_type"] for e in task_mgr.get_audit_events(task_p.task_id)
    ]

    # 12d: NON_IDEMPOTENT_NO_RECONCILE refuses blind retry and escalates to WAITING_FOR_USER
    with open_connection(db_dsn) as conn:
        reset_all_tables(conn)
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls_no_idemp, _ = _register_email_tool(
        registry,
        policy,
        gateway,
        mode=ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE,
        sensitivity=SensitivityTier.SENSITIVE_WRITE,
    )
    task_n = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Non-idempotent escalate"
    )
    w1n = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r1n = w1n.run_initial_turn(
        task_n.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.APPROVAL_REQUEST.value,
            "hitl_prompt": {"question": "Approve non-idempotent send?"},
            "response_schema": {"type": "object", "properties": {"decision": {"type": "string"}}},
            "tool_id": "gmail.send_email",
            "tool_args": args,
            "target_resource": "res:gmail_primary",
        },
    )
    w1n.close()
    subn = task_mgr.submit_interaction_response(
        interaction_id=r1n.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"decision": "APPROVE"},
        response_idempotency_key="exp12d-approve",
    )
    cn1 = task_mgr.claim_resume_outbox_event(
        event_id=subn.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-1",
        expected_claim_epoch=0,
        lease_ttl_seconds=0.80,
    )
    assert cn1 is not None
    gateway.crash_after_provider_before_receipt_commit = True
    w1n_res = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    with pytest.raises(SimulatedCrashError):
        w1n_res.run_resumed_turn(cn1)
    w1n_res.close()
    gateway.crash_after_provider_before_receipt_commit = False

    time.sleep(0.95)
    reconciler.reconcile_outbox_and_stale_leases()
    cn2 = task_mgr.claim_resume_outbox_event(
        event_id=subn.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=1,
    )
    assert cn2 is not None
    w2n = Worker("worker-2", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r2n = w2n.run_resumed_turn(cn2)
    w2n.close()

    # Refused blind retry, escalated cleanly to WAITING_FOR_USER / UNCERTAIN_EXECUTION_RECONCILIATION
    assert len(calls_no_idemp) == 1
    assert r2n.task_status == TaskStatus.WAITING_FOR_USER
    escalated_task = task_mgr.get_task(task_n.task_id)
    assert escalated_task.waiting_reason == WaitingReason.UNCERTAIN_EXECUTION_RECONCILIATION.value
    assert r2n.interaction_id is not None

    # Human verifies external state and submits CONFIRM_ALREADY_EXECUTED -> W3 completes
    sub_rec = task_mgr.submit_interaction_response(
        interaction_id=r2n.interaction_id,
        actor_principal="user:owner",
        channel="dashboard",
        payload={"reconciliation_action": "CONFIRM_ALREADY_EXECUTED"},
        response_idempotency_key="exp12d-confirm",
    )
    cn3 = task_mgr.claim_resume_outbox_event(
        event_id=sub_rec.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-3",
        expected_claim_epoch=0,
    )
    assert cn3 is not None
    w3n = Worker("worker-3", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r3n = w3n.run_resumed_turn(cn3)
    w3n.close()
    assert r3n.task_status == TaskStatus.COMPLETED
    assert len(calls_no_idemp) == 1


# ---------------------------------------------------------------------------
# Experiment 13: Expired, cancelled, or superseded task cannot resume
# ---------------------------------------------------------------------------
def test_exp13_expired_cancelled_or_superseded_cannot_resume(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    calls, _ = _register_email_tool(
        registry, policy, gateway, sensitivity=SensitivityTier.LOW_RISK_WRITE
    )

    # 13a: EXPIRED task & interaction
    t_exp = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Expire test",
        expires_in_seconds=1,
    )
    w1 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r_exp = w1.run_initial_turn(
        t_exp.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Answer before expiry?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )
    w1.close()
    time.sleep(1.2)
    reconciler.reconcile_outbox_and_stale_leases()
    assert task_mgr.get_task(t_exp.task_id).status == TaskStatus.EXPIRED
    assert task_mgr.get_interaction(r_exp.interaction_id)["status"] == InteractionStatus.EXPIRED.value  # type: ignore[arg-type]
    with pytest.raises(StaleInteractionRejectedError):
        task_mgr.submit_interaction_response(
            interaction_id=r_exp.interaction_id,  # type: ignore[arg-type]
            actor_principal="user:owner",
            channel="telegram",
            payload={"answer": "too late"},
            response_idempotency_key="exp13a",
        )

    # 13b: CANCELLED task cascades to interaction AND non-interruptible external race is honest
    t_can = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Cancel waiting"
    )
    w1c = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r_can = w1c.run_initial_turn(
        t_can.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Will be cancelled"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        },
    )
    w1c.close()
    task_mgr.cancel_task(t_can.task_id, actor="user:owner")
    assert task_mgr.get_task(t_can.task_id).status == TaskStatus.CANCELLED
    assert task_mgr.get_interaction(r_can.interaction_id)["status"] == InteractionStatus.CANCELLED.value  # type: ignore[arg-type]
    with pytest.raises(StaleInteractionRejectedError):
        task_mgr.submit_interaction_response(
            interaction_id=r_can.interaction_id,  # type: ignore[arg-type]
            actor_principal="user:owner",
            channel="telegram",
            payload={"answer": "stale"},
            response_idempotency_key="exp13b",
        )

    # Non-interruptible external call race -> CANCELLED_AFTER_EXTERNAL_COMMIT
    t_race = task_mgr.create_task(
        tenant_id="tenant:personal", owner_principal="user:owner", goal="Cancel during HTTP"
    )

    def cancel_mid_call() -> None:
        task_mgr.cancel_task(t_race.task_id, actor="user:owner", reason="mid_flight_cancel")

    gateway.on_during_external_call = cancel_mid_call
    w_race = Worker("worker-race", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r_race = w_race.run_initial_turn(
        t_race.task_id,
        initial_state={
            "requires_hitl": False,
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "r@example.com", "subject": "Race", "body": "Sent"},
            "target_resource": "res:gmail_primary",
        },
    )
    w_race.close()
    gateway.on_during_external_call = None
    assert r_race.task_status == TaskStatus.CANCELLED_AFTER_EXTERNAL_COMMIT
    assert task_mgr.get_task(t_race.task_id).status == TaskStatus.CANCELLED_AFTER_EXTERNAL_COMMIT
    assert "TOOL_COMPLETED_DURING_CANCELLATION_RACE" in [
        e["event_type"] for e in task_mgr.get_audit_events(t_race.task_id)
    ]

    # 13c: Superseded automation generation blocks both late responses and queued RESUME_TASK
    auto_id = task_mgr.create_automation(
        tenant_id="tenant:personal", owner_principal="user:owner", generation=1
    )
    t_auto = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Superseded automation test",
        automation_id=auto_id,
        automation_generation=1,
    )
    w_auto = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r_auto = w_auto.run_initial_turn(
        t_auto.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Confirm automation run?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "z@example.com", "subject": "Auto", "body": "Msg"},
            "target_resource": "res:gmail_primary",
        },
    )
    w_auto.close()

    # Bump automation generation without cascade -> late response rejected!
    task_mgr.update_or_cancel_automation(
        auto_id, new_status=AutomationStatus.ACTIVE, bump_generation=True, cascade_waiting_tasks=False
    )
    with pytest.raises(StaleAutomationGenerationRejectedError):
        task_mgr.submit_interaction_response(
            interaction_id=r_auto.interaction_id,  # type: ignore[arg-type]
            actor_principal="user:owner",
            channel="telegram",
            payload={"answer": "yes"},
            response_idempotency_key="exp13c-stale-gen",
        )

    # Now test RESUME_TASK already queued in outbox before automation cancelled
    t_auto2 = task_mgr.create_task(
        tenant_id="tenant:personal",
        owner_principal="user:owner",
        goal="Queued resume vs automation cancel",
        automation_id=auto_id,
        automation_generation=2,
    )
    w_auto2 = Worker("worker-1", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
    r_auto2 = w_auto2.run_initial_turn(
        t_auto2.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.INFORMATION_REQUEST.value,
            "hitl_prompt": {"question": "Confirm run 2?"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "tool_id": "gmail.send_email",
            "tool_args": {"to": "z2@example.com", "subject": "Auto2", "body": "Msg2"},
            "target_resource": "res:gmail_primary",
        },
    )
    w_auto2.close()
    sub_auto2 = task_mgr.submit_interaction_response(
        interaction_id=r_auto2.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"answer": "yes"},
        response_idempotency_key="exp13c-queued",
    )
    # Automation is cancelled right after RESUME_TASK enters outbox (without cascade)
    task_mgr.update_or_cancel_automation(
        auto_id, new_status=AutomationStatus.CANCELLED, bump_generation=True, cascade_waiting_tasks=False
    )
    assert task_mgr.claim_resume_outbox_event(
        event_id=sub_auto2.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=0,
    ) is None
    rec_auto = reconciler.reconcile_outbox_and_stale_leases()
    assert rec_auto["cancelled_stale"] >= 1
    assert task_mgr.get_outbox_events(t_auto2.task_id, OutboxEventType.RESUME_TASK)[0]["status"] == OutboxStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Experiment 14: Latency Measurements
# ---------------------------------------------------------------------------
def _summarize(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    idx_95 = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "n": len(ordered),
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(statistics.median(ordered), 3),
        "mean_ms": round(statistics.mean(ordered), 3),
        "p95_ms": round(ordered[idx_95], 3),
        "max_ms": round(ordered[-1], 3),
    }


def test_exp14_latency_measurements(db_dsn: str) -> None:
    policy, registry, gateway, task_mgr, reconciler = _make_harness(db_dsn)
    _register_email_tool(
        registry, policy, gateway, sensitivity=SensitivityTier.LOW_RISK_WRITE
    )

    simple_ckpt_ms: list[float] = []
    suspend_ms: list[float] = []
    resume_ms: list[float] = []
    crash_recovery_ms: list[float] = []

    iterations = 10
    for i in range(iterations):
        # 1. Simple task + checkpoint persistence latency
        t0 = task_mgr.create_task(
            tenant_id="tenant:bench", owner_principal="user:owner", goal=f"bench-simple-{i}"
        )
        w = Worker(f"bench-w-{i}", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
        start = time.perf_counter()
        res0 = w.run_initial_turn(
            t0.task_id,
            initial_state={
                "requires_hitl": False,
                "tool_id": "gmail.send_email",
                "tool_args": {"to": "b@example.com", "subject": "S", "body": f"{i}"},
                "target_resource": "res:gmail_primary",
            },
        )
        simple_ckpt_ms.append((time.perf_counter() - start) * 1000.0)
        assert res0.task_status == TaskStatus.COMPLETED
        w.close()

        # 2. Suspension latency (Phase A + Phase B checkpoint/verify + Phase C commit)
        t1 = task_mgr.create_task(
            tenant_id="tenant:bench", owner_principal="user:owner", goal=f"bench-hitl-{i}"
        )
        w1 = Worker(f"bench-w1-{i}", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
        start = time.perf_counter()
        res1 = w1.run_initial_turn(
            t1.task_id,
            initial_state={
                "requires_hitl": True,
                "hitl_type": InteractionType.INFORMATION_REQUEST.value,
                "hitl_prompt": {"question": "Continue?"},
                "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            },
        )
        suspend_ms.append((time.perf_counter() - start) * 1000.0)
        assert res1.task_status == TaskStatus.WAITING_FOR_USER
        w1.close()

        # 3. Resumption latency (Interaction CAS + Outbox RESUME_TASK + Claim + W2 resume completion)
        w2 = Worker(f"bench-w2-{i}", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
        start = time.perf_counter()
        sub = task_mgr.submit_interaction_response(
            interaction_id=res1.interaction_id,  # type: ignore[arg-type]
            actor_principal="user:owner",
            channel="telegram",
            payload={"answer": "yes"},
            response_idempotency_key=f"bench-{i}",
        )
        claimed = task_mgr.claim_resume_outbox_event(
            event_id=sub.resume_outbox_event_id,  # type: ignore[arg-type]
            worker_id=f"bench-w2-{i}",
            expected_claim_epoch=0,
        )
        assert claimed is not None
        res2 = w2.run_resumed_turn(claimed)
        resume_ms.append((time.perf_counter() - start) * 1000.0)
        assert res2.task_status == TaskStatus.COMPLETED
        w2.close()

        # 4. Crash recovery latency (Phase B saved, crash before Phase C -> reconciler roll-forward)
        t2 = task_mgr.create_task(
            tenant_id="tenant:bench", owner_principal="user:owner", goal=f"bench-crash-{i}"
        )
        w_crash = Worker(f"bench-wc-{i}", task_manager=task_mgr, gateway=gateway, dsn=db_dsn)
        with pytest.raises(SimulatedCrashError):
            w_crash.run_initial_turn(
                t2.task_id,
                initial_state={
                    "requires_hitl": True,
                    "hitl_type": InteractionType.INFORMATION_REQUEST.value,
                    "hitl_prompt": {"question": "Recover?"},
                    "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
                },
                crash_before_phase_c=True,
            )
        w_crash.close()

        rec_saver = OrbiPostgresSaver.create(db_dsn)
        start = time.perf_counter()
        rec_out = reconciler.reconcile_incomplete_suspensions(rec_saver, ignore_lease_ttl=True)
        crash_recovery_ms.append((time.perf_counter() - start) * 1000.0)
        assert rec_out["rolled_forward"] == 1
        rec_saver.close()

    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_dsn": db_dsn.replace("orbi_eval04_pass", "***"),
        "iterations": iterations,
        "metrics": {
            "simple_task_and_checkpoint_ms": _summarize(simple_ckpt_ms),
            "suspension_phase_abc_ms": _summarize(suspend_ms),
            "resumption_phase_d_and_worker2_ms": _summarize(resume_ms),
            "crash_reconciler_roll_forward_ms": _summarize(crash_recovery_ms),
        },
    }
    out_path = Path(__file__).resolve().parent.parent / "benchmark_results.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[EVAL-04 Latency Benchmark]\n{json.dumps(report, indent=2)}")
