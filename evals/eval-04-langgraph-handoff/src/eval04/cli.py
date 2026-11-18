"""Runnable CLI demonstration and quick sanity benchmark for EVAL-04."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from eval04.checkpointer import OrbiPostgresSaver
from eval04.db import get_database_url, init_schemas, open_connection, reset_all_tables
from eval04.gateway import ToolDefinition, ToolExecutionGateway, ToolRegistry
from eval04.models import (
    ExternalIdempotencyMode,
    InMemoryPolicyEngine,
    InteractionType,
    SensitivityTier,
)
from eval04.runtime import Worker
from eval04.task_manager import CrashReconciler, DeterministicTaskManager


def main() -> None:
    dsn = get_database_url()
    print(f"[EVAL-04] Connecting to PostgreSQL: {dsn}")
    with open_connection(dsn) as conn:
        init_schemas(conn)
        reset_all_tables(conn)

    # Initialize LangGraph checkpoint tables once before running
    setup_saver = OrbiPostgresSaver.create(dsn)
    setup_saver.close()

    policy_engine = InMemoryPolicyEngine(initial_policy_version=1)
    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            tool_id="calendar.book_train",
            tool_version="1.0.0",
            sensitivity_tier=SensitivityTier.SENSITIVE_WRITE,
            semantic_fields=("destination", "train_class"),
            external_idempotency_mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
        )
    )
    policy_engine.grant_tool_access("user:owner", "calendar.book_train", "res:rail_api")

    gateway = ToolExecutionGateway(registry=registry, policy_engine=policy_engine, dsn=dsn)
    external_calls: list[dict[str, str]] = []

    def book_train_provider(*, action_idempotency_key: str, arguments: dict, target_resource: str) -> dict:
        external_calls.append(
            {
                "action_idempotency_key": action_idempotency_key,
                "target_resource": target_resource,
                "arguments": json.dumps(arguments, sort_keys=True),
            }
        )
        return {"booking_ref": "PNR-EVAL04-001", "status": "booked"}

    gateway.register_provider("calendar.book_train", book_train_provider)
    task_manager = DeterministicTaskManager(policy_engine=policy_engine, dsn=dsn)
    reconciler = CrashReconciler(task_manager, dsn=dsn)

    # 1. Create task in PostgreSQL
    task = task_manager.create_task(
        tenant_id="tenant:personal_owner",
        owner_principal="user:owner",
        goal="Book evening train to Mumbai",
    )
    print(f"[1] Created task {task.task_id} (execution_id={task.execution_id}) -> {task.status}")

    # 2. Worker W1 runs initial turn -> suspends via Phase A/B/C -> releases completely
    t0 = time.perf_counter()
    w1 = Worker("worker-1", task_manager=task_manager, gateway=gateway, dsn=dsn)
    r1 = w1.run_initial_turn(
        task.task_id,
        initial_state={
            "requires_hitl": True,
            "hitl_type": InteractionType.APPROVAL_REQUEST.value,
            "hitl_prompt": {"question": "Approve booking First Class train to Mumbai?"},
            "response_schema": {
                "type": "object",
                "properties": {"decision": {"type": "string", "enum": ["APPROVE", "REJECT"]}},
                "required": ["decision"],
            },
            "tool_id": "calendar.book_train",
            "tool_args": {"destination": "Mumbai", "train_class": "1A"},
            "target_resource": "res:rail_api",
        },
    )
    w1.close()
    del w1
    t_suspend_ms = (time.perf_counter() - t0) * 1000.0
    print(
        f"[2] Worker W1 suspended & released in {t_suspend_ms:.2f} ms: "
        f"task_status={r1.task_status}, verified_checkpoint={r1.checkpoint_id}, "
        f"interaction_id={r1.interaction_id}, approval_id={r1.approval_id}"
    )

    # 3. User approves via HumanInteractionManager
    t1 = time.perf_counter()
    sub = task_manager.submit_interaction_response(
        interaction_id=r1.interaction_id,  # type: ignore[arg-type]
        actor_principal="user:owner",
        channel="telegram",
        payload={"decision": "APPROVE"},
        response_idempotency_key="cli-approve-1",
    )
    print(
        f"[3] Human approved -> interaction={sub.interaction_status}, "
        f"task={sub.task_status}, resume_outbox={sub.resume_outbox_event_id}"
    )

    # 4. Different worker W2 claims RESUME_TASK and resumes from verified checkpoint
    claimed = task_manager.claim_resume_outbox_event(
        event_id=sub.resume_outbox_event_id,  # type: ignore[arg-type]
        worker_id="worker-2",
        expected_claim_epoch=0,
    )
    assert claimed is not None
    w2 = Worker("worker-2", task_manager=task_manager, gateway=gateway, dsn=dsn)
    r2 = w2.run_resumed_turn(claimed)
    w2.close()
    t_resume_ms = (time.perf_counter() - t1) * 1000.0
    print(
        f"[4] Worker W2 resumed & completed in {t_resume_ms:.2f} ms: "
        f"status={r2.task_status}, final_answer={r2.final_answer}, external_calls={len(external_calls)}"
    )

    # 5. Reconciler purges terminal task checkpoints
    cleanup_saver = OrbiPostgresSaver.create(dsn)
    purged = reconciler.purge_orphaned_checkpoints(cleanup_saver)
    cleanup_saver.close()
    print(f"[5] Terminal task checkpoints purged: {purged} row(s) at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
