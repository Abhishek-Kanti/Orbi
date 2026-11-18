"""Comprehensive automated tests for EVAL-08: Automation Idempotency & Version Fencing."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from eval08.canonical import (
    TriggerType,
    build_execution_identity_payload,
    build_trigger_occurrence_id,
    compute_automation_idempotency_key,
    jcs_canonicalize,
    rfc8785_canonicalize,
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
    ExternalIdempotencyMode,
    ReceiptStatus,
    SimulatedCrashError,
    StaleClaimEpochFencedError,
    StaleTaskVersionError,
    TaskStatus,
    UncertainOutcomeError,
    naive_select_then_insert_race,
)


@pytest.fixture(scope="session")
def db_dsn() -> str:
    ensure_eval08_database()
    dsn = get_database_url()
    with open_connection(dsn) as conn:
        init_schemas(conn)
    return dsn


@pytest.fixture(autouse=True)
def clean_db(db_dsn: str) -> None:
    with open_connection(db_dsn) as conn:
        reset_all_tables(conn)


# ---------------------------------------------------------------------------
# 1. Canonical RFC 8785 (JCS) Serialization & Idempotency Key Tests
# ---------------------------------------------------------------------------
def test_01_rfc8785_jcs_canonical_vectors_and_type_safety() -> None:
    aid = "A1B2C3D4-E5F6-4789-ABCD-0123456789AB"
    occ = build_trigger_occurrence_id(
        TriggerType.CRON,
        scheduled_at_utc=datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc),
    )
    assert occ == "cron:2026-11-18T08:00:00+00:00"

    # Identical logical identity regardless of case/whitespace/key-order differences
    k1, c1 = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid.lower(),
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ,
    )
    k2, c2 = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid.upper(),
        schedule_version=1,
        generation=1,
        trigger_type="CRON",
        trigger_occurrence_id=occ,
    )
    assert k1 == k2
    assert c1 == c2
    expected_canonical = (
        '{"automation_id":"a1b2c3d4-e5f6-4789-abcd-0123456789ab",'
        '"generation":1,'
        '"schedule_version":1,'
        '"schema":"orbi.automation.execution_idempotency.v1",'
        '"tenant_id":"tenant:personal",'
        '"trigger_occurrence_id":"cron:2026-11-18T08:00:00+00:00",'
        '"trigger_type":"CRON"}'
    )
    assert c1 == expected_canonical
    assert k1 == hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()

    # Key-order & whitespace invariance on identical logical dicts
    d_order1 = {
        "trigger_type": "CRON",
        "tenant_id": "tenant:personal",
        "generation": 1,
        "automation_id": aid.lower(),
        "schedule_version": 1,
        "trigger_occurrence_id": occ,
        "schema": "orbi.automation.execution_idempotency.v1",
    }
    d_order2 = {
        "schema": "orbi.automation.execution_idempotency.v1",
        "automation_id": aid.lower(),
        "schedule_version": 1,
        "generation": 1,
        "tenant_id": "tenant:personal",
        "trigger_occurrence_id": occ,
        "trigger_type": "CRON",
    }
    assert jcs_canonicalize(d_order1) == jcs_canonicalize(d_order2) == expected_canonical
    assert hashlib.sha256(jcs_canonicalize(d_order1).encode("utf-8")).hexdigest() == k1

    # Key-order & whitespace invariance in arbitrary JCS objects
    assert jcs_canonicalize({"b": 2, "a": {"z": True, "m": None}}) == '{"a":{"m":null,"z":true},"b":2}'

    # RFC 8785 UTF-16-BE key sort order: U+1F680 (D8 3D DE 80) MUST precede U+FFFF (FF FF)
    utf16_sorted = jcs_canonicalize({"\uffff": 1, "🚀": 2})
    assert utf16_sorted == '{"🚀":2,"\uffff":1}'

    # Control characters & raw Unicode preservation (no \/ escaping)
    escaped = jcs_canonicalize({"txt": "café\t東京\n/path\"\x01\\"})
    assert escaped == '{"txt":"café\\t東京\\n/path\\"\\u0001\\\\"}'

    # Strict type separation: int 1 vs str "1", float rejected, bool rejected as int
    assert jcs_canonicalize({"v": 1}) != jcs_canonicalize({"v": "1"})
    with pytest.raises(TypeError):
        jcs_canonicalize({"v": 1.0})
    with pytest.raises(TypeError):
        compute_automation_idempotency_key(
            tenant_id="tenant:personal",
            automation_id=aid,
            schedule_version=True,  # type: ignore[arg-type]
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id=occ,
        )
    with pytest.raises(ValueError):
        jcs_canonicalize({"bad": "\ud800"})

    # Different trigger occurrence -> different key
    occ_next = build_trigger_occurrence_id(
        TriggerType.CRON,
        scheduled_at_utc=datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc),
    )
    k_diff_occ, _ = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ_next,
    )
    assert k_diff_occ != k1

    # Different tenant scope -> different logical identity key even with same UUID
    k_diff_tenant, _ = compute_automation_idempotency_key(
        tenant_id="tenant:household_2",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ,
    )
    assert k_diff_tenant != k1

    # All 6 trigger types produce distinct, stable occurrence strings
    assert build_trigger_occurrence_id(
        TriggerType.INTERVAL, anchor_epoch_sec=1700000000, interval_sec=300, tick_index=4
    ) == "interval:1700000000:every:300:tick:4"
    assert build_trigger_occurrence_id(
        TriggerType.ONE_TIME, scheduled_at_utc=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    ) == "onetime:2026-11-18T12:00:00+00:00"
    assert build_trigger_occurrence_id(TriggerType.EVENT, source_event_id="evt-991") == "event:evt-991"
    assert build_trigger_occurrence_id(TriggerType.CONDITION, monitor_id="mon-1", rising_edge_seq=1) == "condition:mon-1:edge:1"
    assert build_trigger_occurrence_id(TriggerType.MANUAL, request_idempotency_id="req-abc") == "manual:req-abc"

    # Full RFC 8785 §3.2.2.3 / Appendix B IEEE-754 number formatting (when allow_floats=True)
    rfc8785_number_vectors = [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (4.50, "4.5"),
        (-4.50, "-4.5"),
        (2e-3, "0.002"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1e30, "1e+30"),
        (-1e21, "-1e+21"),
        (333333333.33333329, "333333333.3333333"),
    ]
    for num, expected_num in rfc8785_number_vectors:
        assert jcs_canonicalize({"v": num}, allow_floats=True) == f'{{"v":{expected_num}}}'
        assert rfc8785_canonicalize({"v": num}) == f'{{"v":{expected_num}}}'
    for bad_float in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            rfc8785_canonicalize({"v": bad_float})

    # Stage 1 (application normalization) vs. Stage 2 (pure RFC 8785 JCS):
    # 1. Pure JCS never case-folds strings and rejects non-JSON raw UUID objects.
    assert jcs_canonicalize({"id": aid.upper()}) == f'{{"id":"{aid.upper()}"}}'
    with pytest.raises(TypeError):
        jcs_canonicalize({"id": UUID(aid)})

    # 2. Stage 1 normalizes automation_id (UUID object or hex string) to lowercase RFC 9562 hex.
    p_uuid_obj = build_execution_identity_payload(
        tenant_id="tenant:personal",
        automation_id=UUID(aid),
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ,
    )
    p_uuid_upper = build_execution_identity_payload(
        tenant_id="tenant:personal",
        automation_id=aid.upper(),
        schedule_version=1,
        generation=1,
        trigger_type="CRON",
        trigger_occurrence_id=occ,
    )
    assert p_uuid_obj == p_uuid_upper
    assert p_uuid_obj["automation_id"] == aid.lower()

    # 3. Stage 1 normalizes timezone-aware CRON/ONE_TIME instants to UTC while preserving
    #    sub-second precision (no silent truncation) and rejecting naive datetimes.
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    est_tz = timezone(timedelta(hours=-5))
    occ_ist = build_trigger_occurrence_id(
        TriggerType.CRON,
        scheduled_at_utc=datetime(2026, 11, 18, 13, 30, 0, tzinfo=ist_tz),
    )
    assert occ_ist == occ == "cron:2026-11-18T08:00:00+00:00"
    assert build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime(2026, 11, 18, 17, 30, 0, tzinfo=ist_tz),
    ) == "onetime:2026-11-18T12:00:00+00:00"

    # 3a. Sub-second precision preservation: distinct fractional timestamps produce distinct
    #     trigger_occurrence_ids and distinct SHA-256 idempotency keys.
    for ttype, prefix in ((TriggerType.ONE_TIME, "onetime"), (TriggerType.CRON, "cron")):
        occ_whole = build_trigger_occurrence_id(
            ttype,
            scheduled_at_utc=datetime(2026, 11, 18, 8, 0, 0, 0, tzinfo=timezone.utc),
        )
        occ_half = build_trigger_occurrence_id(
            ttype,
            scheduled_at_utc=datetime(2026, 11, 18, 8, 0, 0, 500000, tzinfo=timezone.utc),
        )
        occ_micro = build_trigger_occurrence_id(
            ttype,
            scheduled_at_utc=datetime(2026, 11, 18, 8, 0, 0, 500001, tzinfo=timezone.utc),
        )
        assert occ_whole == f"{prefix}:2026-11-18T08:00:00+00:00"
        assert occ_half == f"{prefix}:2026-11-18T08:00:00.500000+00:00"
        assert occ_micro == f"{prefix}:2026-11-18T08:00:00.500001+00:00"

        k_whole, _ = compute_automation_idempotency_key(
            tenant_id="tenant:personal",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=ttype,
            trigger_occurrence_id=occ_whole,
        )
        k_half, _ = compute_automation_idempotency_key(
            tenant_id="tenant:personal",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=ttype,
            trigger_occurrence_id=occ_half,
        )
        k_micro, _ = compute_automation_idempotency_key(
            tenant_id="tenant:personal",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=ttype,
            trigger_occurrence_id=occ_micro,
        )
        assert len({k_whole, k_half, k_micro}) == 3

    # 3b. Equivalent timezone + trailing-zero fractional representations normalize identically.
    occ_utc_frac = build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime.fromisoformat("2026-11-18T08:00:00.123456+00:00"),
    )
    occ_ist_frac = build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime(2026, 11, 18, 13, 30, 0, 123456, tzinfo=ist_tz),
    )
    occ_est_frac = build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime(2026, 11, 18, 3, 0, 0, 123456, tzinfo=est_tz),
    )
    assert occ_utc_frac == occ_ist_frac == occ_est_frac == "onetime:2026-11-18T08:00:00.123456+00:00"
    k_frac_utc, _ = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.ONE_TIME,
        trigger_occurrence_id=occ_utc_frac,
    )
    k_frac_est, _ = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.ONE_TIME,
        trigger_occurrence_id=occ_est_frac,
    )
    assert k_frac_utc == k_frac_est

    assert build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime.fromisoformat("2026-11-18T08:00:00.5+00:00"),
    ) == build_trigger_occurrence_id(
        TriggerType.ONE_TIME,
        scheduled_at_utc=datetime.fromisoformat("2026-11-18T13:30:00.500000+05:30"),
    ) == "onetime:2026-11-18T08:00:00.500000+00:00"

    assert build_trigger_occurrence_id(
        TriggerType.CRON,
        scheduled_at_utc=datetime.fromisoformat("2026-11-18T08:00:00.000000+00:00"),
    ) == occ

    # 3c. Naive datetimes (missing tzinfo) must be rejected fail-closed.
    naive_dt = datetime(2026, 11, 18, 8, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_trigger_occurrence_id(TriggerType.CRON, scheduled_at_utc=naive_dt)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_trigger_occurrence_id(TriggerType.ONE_TIME, scheduled_at_utc=naive_dt)

    # 4. Case-sensitive external identifiers are NEVER collapsed by normalization.
    k_evt_upper, _ = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.EVENT,
        trigger_occurrence_id=build_trigger_occurrence_id(
            TriggerType.EVENT, source_event_id="Webhook_AbC"
        ),
    )
    k_evt_lower, _ = compute_automation_idempotency_key(
        tenant_id="tenant:personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.EVENT,
        trigger_occurrence_id=build_trigger_occurrence_id(
            TriggerType.EVENT, source_event_id="webhook_abc"
        ),
    )
    assert k_evt_upper != k_evt_lower

    k_ten_cap, _ = compute_automation_idempotency_key(
        tenant_id="tenant:Personal",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ,
    )
    assert k_ten_cap != k1


# ---------------------------------------------------------------------------
# 2. Prove Naive SELECT-then-INSERT Fails vs. Atomic PG UNIQUE Claim
# ---------------------------------------------------------------------------
def test_02_naive_select_then_insert_fails_under_concurrency(db_dsn: str) -> None:
    inserted_rows = naive_select_then_insert_race(
        idempotency_key="naive-race-key-001",
        worker_ids=["w1", "w2", "w3"],
        dsn=db_dsn,
    )
    # Without atomic INSERT ... ON CONFLICT + UNIQUE constraint, all 3 create duplicates!
    assert inserted_rows == 3


def test_03_duplicate_delivery_and_concurrent_workers_atomic_claim(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid = auto["automation_id"]
    occ = build_trigger_occurrence_id(
        TriggerType.CRON,
        scheduled_at_utc=datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
    )

    # 3a. Concurrent barrier: 6 workers try to claim the exact same occurrence simultaneously
    worker_count = 6
    barrier = threading.Barrier(worker_count)
    results = []
    lock = threading.Lock()

    def worker_claim(wid: str) -> None:
        res = engine.claim_and_dispatch_occurrence(
            tenant_id="tenant:p1",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id=occ,
            worker_id=wid,
            start_barrier=barrier,
        )
        with lock:
            results.append(res)

    threads = [threading.Thread(target=worker_claim, args=(f"worker-{i}",)) for i in range(worker_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    winners = [r for r in results if r.outcome == ClaimOutcome.CLAIMED_AND_DISPATCHED]
    dups = [r for r in results if r.outcome == ClaimOutcome.DUPLICATE_DEDUPLICATED]
    assert len(winners) == 1
    assert len(dups) == worker_count - 1
    assert all(d.execution_id == winners[0].execution_id for d in dups)
    assert all(d.task_id == winners[0].task_id for d in dups)
    assert engine.count_executions_for_automation(aid) == 1
    assert engine.count_tasks_for_automation(aid) == 1

    # 3b. Repeated sequential delivery after successful claim returns existing execution/task
    replay = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id=occ,
        worker_id="worker-late",
    )
    assert replay.outcome == ClaimOutcome.DUPLICATE_DEDUPLICATED
    assert replay.execution_id == winners[0].execution_id
    assert replay.task_id == winners[0].task_id
    assert engine.count_tasks_for_automation(aid) == 1


# ---------------------------------------------------------------------------
# 3. Version Fencing: schedule_version vs generation & Concurrent Update Race
# ---------------------------------------------------------------------------
def test_04_schedule_version_and_generation_lifecycle_fencing(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid = auto["automation_id"]

    # Update schedule 8AM -> 9AM: bumps BOTH schedule_version (1->2) and generation (1->2)
    updated = engine.update_automation(
        aid, trigger_spec={"cron": "0 9 * * *"}, actor="user:owner", reason="move_to_9am"
    )
    assert updated["schedule_version"] == 2
    assert updated["generation"] == 2

    # Old schedule_version=1 / generation=1 rejected
    r_old_sched = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T08:00:00+00:00",
        worker_id="w1",
    )
    assert r_old_sched.outcome == ClaimOutcome.REJECTED_FENCED
    assert r_old_sched.reason == "SKIPPED_STALE_GENERATION"

    # Pause: bumps generation (2->3), keeps schedule_version=2
    paused = engine.pause_automation(aid, actor="user:owner")
    assert paused["status"] == AutomationStatus.PAUSED.value
    assert paused["schedule_version"] == 2
    assert paused["generation"] == 3

    # Paused automation rejects event carrying schedule_version=2, generation=2
    r_paused = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=2,
        generation=2,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T09:00:00+00:00",
        worker_id="w1",
    )
    assert r_paused.outcome == ClaimOutcome.REJECTED_FENCED
    assert r_paused.reason == "SKIPPED_STATUS_PAUSED"

    # Resume: bumps generation (3->4), keeps schedule_version=2
    resumed = engine.resume_automation(aid, actor="user:owner")
    assert resumed["status"] == AutomationStatus.ACTIVE.value
    assert resumed["schedule_version"] == 2
    assert resumed["generation"] == 4

    # Old generation=2 rejected EVEN THOUGH schedule_version=2 still matches!
    r_old_gen = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=2,
        generation=2,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T09:00:00+00:00",
        worker_id="w1",
    )
    assert r_old_gen.outcome == ClaimOutcome.REJECTED_FENCED
    assert r_old_gen.reason == "SKIPPED_STALE_GENERATION"
    assert engine.count_tasks_for_automation(aid) == 0

    # Cancel: bumps generation (4->5) and rejects old event
    cancelled = engine.cancel_automation(aid, actor="user:owner")
    assert cancelled["status"] == AutomationStatus.CANCELLED.value
    assert cancelled["generation"] == 5
    r_cancelled = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=2,
        generation=4,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T09:00:00+00:00",
        worker_id="w1",
    )
    assert r_cancelled.outcome == ClaimOutcome.REJECTED_FENCED
    assert r_cancelled.reason == "SKIPPED_STATUS_CANCELLED"

    # 6-step DELETE on a fresh automation rejects pending old event & scrubs spec
    auto2 = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid2 = auto2["automation_id"]
    deleted = engine.delete_automation_6step(aid2, actor="user:owner")
    assert deleted["status"] == AutomationStatus.DELETED.value
    assert deleted["generation"] == 2
    assert deleted["trigger_spec"] == {}
    r_deleted = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid2,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T08:00:00+00:00",
        worker_id="w1",
    )
    assert r_deleted.outcome == ClaimOutcome.REJECTED_FENCED
    assert r_deleted.reason == "SKIPPED_STATUS_DELETED"

    # Pause cascades CANCELLED (task_version + 1) to WAITING_FOR_USER tasks & INVALIDATED to approvals
    auto3 = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid3 = auto3["automation_id"]
    dispatched = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid3,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T11:00:00+00:00",
        worker_id="w-cascade",
    )
    assert dispatched.task_id is not None
    engine.cas_transition_task(
        task_id=dispatched.task_id,
        expected_task_version=1,
        expected_status=TaskStatus.QUEUED,
        new_status=TaskStatus.RUNNING,
        actor="w-cascade",
    )
    inter_id, app_id, waiting_ver = engine.create_waiting_approval(
        task_id=dispatched.task_id, expected_task_version=2
    )
    assert waiting_ver == 3
    engine.pause_automation(aid3, actor="user:owner")
    waiting_task = engine.get_task(dispatched.task_id)
    assert waiting_task["status"] == TaskStatus.CANCELLED.value
    assert waiting_task["task_version"] == 4
    assert waiting_task["cancellation_requested"] is True
    with open_connection(db_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, interaction_version FROM orbi_app.interaction_requests WHERE interaction_id = %s",
                (inter_id,),
            )
            inter_row = cur.fetchone()
            cur.execute(
                "SELECT status FROM orbi_app.approval_requests WHERE approval_id = %s",
                (app_id,),
            )
            app_row = cur.fetchone()
    assert inter_row is not None and inter_row["status"] == "CANCELLED"
    assert inter_row["interaction_version"] == 2
    assert app_row is not None and app_row["status"] == "INVALIDATED"
    with pytest.raises(StaleTaskVersionError):
        engine.resolve_approval_with_version_fence(
            approval_id=app_id, expected_task_version=waiting_ver, actor="user:owner"
        )


def test_05_concurrent_schedule_update_and_event_claim_serializability(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    # Repeat across 10 races to exercise both lock arrival orders deterministically
    for i in range(10):
        auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
        aid = auto["automation_id"]
        barrier = threading.Barrier(2)
        claim_holder: list[Any] = []

        def run_update() -> None:
            engine.update_automation(
                aid,
                trigger_spec={"cron": "0 10 * * *"},
                actor="user:owner",
                reason=f"race-{i}",
                start_barrier=barrier,
            )

        def run_claim() -> None:
            res = engine.claim_and_dispatch_occurrence(
                tenant_id="tenant:p1",
                automation_id=aid,
                schedule_version=1,
                generation=1,
                trigger_type=TriggerType.CRON,
                trigger_occurrence_id=f"cron:race-{i}",
                worker_id="w-race",
                start_barrier=barrier,
            )
            claim_holder.append(res)

        t_u = threading.Thread(target=run_update)
        t_c = threading.Thread(target=run_claim)
        t_u.start()
        t_c.start()
        t_u.join(timeout=10.0)
        t_c.join(timeout=10.0)

        res = claim_holder[0]
        assert res.outcome in (ClaimOutcome.CLAIMED_AND_DISPATCHED, ClaimOutcome.REJECTED_FENCED)
        # After update committed (v2/g2), a subsequent v1/g1 delivery MUST ALWAYS be rejected
        post_res = engine.claim_and_dispatch_occurrence(
            tenant_id="tenant:p1",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id=f"cron:post-race-{i}",
            worker_id="w-post",
        )
        assert post_res.outcome == ClaimOutcome.REJECTED_FENCED


# ---------------------------------------------------------------------------
# 4. Atomic Claim + Task Creation & Crash Recovery Boundaries
# ---------------------------------------------------------------------------
def test_06_crash_between_claim_and_task_creation_atomic_and_two_phase_recovery(
    db_dsn: str,
) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid = auto["automation_id"]

    # 6a. Single-transaction atomic mode: crash before task creation rolls back cleanly
    with pytest.raises(SimulatedCrashError):
        engine.claim_and_dispatch_occurrence(
            tenant_id="tenant:p1",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id="cron:2026-11-18T08:00:00+00:00",
            worker_id="w-crash-tx",
            crash_after_claim_before_task_in_tx=True,
        )
    assert engine.count_executions_for_automation(aid) == 0
    assert engine.count_tasks_for_automation(aid) == 0

    # Retry succeeds atomically with 1 execution and 1 task
    ok = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T08:00:00+00:00",
        worker_id="w-retry",
    )
    assert ok.outcome == ClaimOutcome.CLAIMED_AND_DISPATCHED
    assert engine.count_executions_for_automation(aid) == 1
    assert engine.count_tasks_for_automation(aid) == 1

    # 6b. Two-phase recoverable protocol: worker crashes after claiming lease before creating task
    with pytest.raises(SimulatedCrashError):
        engine.claim_occurrence_two_phase(
            tenant_id="tenant:p1",
            automation_id=aid,
            schedule_version=1,
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id="cron:2026-11-18T09:00:00+00:00",
            worker_id="w1-abandoned",
            lease_ttl_seconds=0.15,
            crash_between_claim_and_task=True,
        )
    # Execution exists as CLAIMED_PENDING_TASK (task_id IS NULL); duplicate redelivery sees it & avoids 2nd claim
    dup_while_abandoned = engine.claim_occurrence_two_phase(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.CRON,
        trigger_occurrence_id="cron:2026-11-18T09:00:00+00:00",
        worker_id="w-dup",
    )
    assert dup_while_abandoned.outcome == ClaimOutcome.DUPLICATE_DEDUPLICATED
    abandoned_exec_id = dup_while_abandoned.execution_id
    assert abandoned_exec_id is not None

    # Lease expires -> recovery worker reclaims with claim_epoch=2 & creates single task
    time.sleep(0.25)
    recovered = engine.recover_abandoned_executions(recovery_worker_id="w2-reconciler")
    assert len(recovered) == 1
    assert recovered[0].outcome == ClaimOutcome.RECOVERED_ABANDONED
    assert recovered[0].claim_epoch == 2
    assert recovered[0].task_id is not None

    # Stale worker W1 wakes up with old claim_epoch=1 -> fenced cleanly!
    with pytest.raises(StaleClaimEpochFencedError):
        engine.stale_worker_finalize_claimed_execution(
            execution_id=abandoned_exec_id,
            stale_worker_id="w1-abandoned",
            stale_claim_epoch=1,
        )
    assert engine.count_tasks_for_automation(aid) == 2  # Exactly 1 from 6a + 1 from 6b

    # 6c. Superseded abandoned claim: update bumps version/generation while CLAIMED_PENDING_TASK waits ->
    #     recover_abandoned_executions re-checks FOR UPDATE and marks ABORTED_SUPERSEDED (0 tasks)
    auto3 = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid3 = auto3["automation_id"]
    with pytest.raises(SimulatedCrashError):
        engine.claim_occurrence_two_phase(
            tenant_id="tenant:p1",
            automation_id=aid3,
            schedule_version=1,
            generation=1,
            trigger_type=TriggerType.CRON,
            trigger_occurrence_id="cron:2026-11-18T10:00:00+00:00",
            worker_id="w1-superseded",
            lease_ttl_seconds=0.15,
            crash_between_claim_and_task=True,
        )
    engine.update_automation(
        aid3,
        trigger_spec={"cron": "0 11 * * *"},
        actor="user:owner",
        reason="supersede_before_recovery",
    )
    time.sleep(0.25)
    superseded_rec = engine.recover_abandoned_executions(recovery_worker_id="w3-reconciler")
    assert len(superseded_rec) == 1
    assert superseded_rec[0].outcome == ClaimOutcome.REJECTED_FENCED
    assert superseded_rec[0].reason == "ABORTED_SUPERSEDED_DURING_RECOVERY"
    assert superseded_rec[0].execution_id is not None
    assert engine.get_execution(superseded_rec[0].execution_id)["status"] == "ABORTED_SUPERSEDED"
    assert engine.count_tasks_for_automation(aid3) == 0


# ---------------------------------------------------------------------------
# 5. Task-Level task_version Optimistic Concurrency & Stale Approvals/Cancels
# ---------------------------------------------------------------------------
def test_07_task_version_cas_concurrent_updates_and_stale_approval_cancel(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid = auto["automation_id"]
    claimed = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.MANUAL,
        trigger_occurrence_id="manual:req-1",
        worker_id="w1",
    )
    tid = claimed.task_id
    assert tid is not None

    # Two concurrent workers race to transition QUEUED (v=1) -> RUNNING (v=2)
    barrier = threading.Barrier(2)
    winners: list[dict] = []
    losers: list[Exception] = []
    lock = threading.Lock()

    def race_claim_task(wid: str) -> None:
        try:
            res = engine.cas_transition_task(
                task_id=tid,
                expected_task_version=1,
                expected_status=TaskStatus.QUEUED,
                new_status=TaskStatus.RUNNING,
                actor=wid,
                start_barrier=barrier,
            )
            with lock:
                winners.append(res)
        except StaleTaskVersionError as exc:
            with lock:
                losers.append(exc)

    t1 = threading.Thread(target=race_claim_task, args=("worker-a",))
    t2 = threading.Thread(target=race_claim_task, args=("worker-b",))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0]["task_version"] == 2

    # Transition RUNNING (v=2) -> WAITING_FOR_USER (v=3) with approval
    _, app_id, waiting_ver = engine.create_waiting_approval(task_id=tid, expected_task_version=2)
    assert waiting_ver == 3

    # Newer transition cancels the waiting task (v=3 -> v=4 CANCELLED)
    cancelled_task = engine.cas_transition_task(
        task_id=tid,
        expected_task_version=3,
        expected_status=TaskStatus.WAITING_FOR_USER,
        new_status=TaskStatus.CANCELLED,
        actor="user:owner",
    )
    assert cancelled_task["task_version"] == 4

    # Stale approval arriving referencing task_version=3 is rejected cleanly!
    with pytest.raises(StaleTaskVersionError):
        engine.resolve_approval_with_version_fence(
            approval_id=app_id, expected_task_version=3, actor="user:owner"
        )

    # Stale cancellation attempt referencing old task_version=2 cannot overwrite CANCELLED v=4
    with pytest.raises(StaleTaskVersionError):
        engine.cas_transition_task(
            task_id=tid,
            expected_task_version=2,
            expected_status=TaskStatus.RUNNING,
            new_status=TaskStatus.CANCELLED,
            actor="stale-cancel",
        )
    assert engine.get_task(tid)["status"] == TaskStatus.CANCELLED.value
    assert engine.get_task(tid)["task_version"] == 4


# ---------------------------------------------------------------------------
# 6. Condition Trigger Rising-Edge Transitions
# ---------------------------------------------------------------------------
def test_08_condition_trigger_edge_transition_semantics(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(
        tenant_id="tenant:p1",
        owner_principal="user:owner",
        trigger_type=TriggerType.CONDITION,
        trigger_spec={"predicate": "moisture_pct < 24.0"},
    )
    aid = auto["automation_id"]

    # Poll sequence: False -> True (edge 1) -> True -> True (0 new) -> False (reset) -> True (edge 2)
    polls = [False, True, True, True, False, True]
    fired: list[ClaimResult] = []
    for idx, val in enumerate(polls):
        res = engine.evaluate_condition_poll(
            monitor_id="mon-soil-balcony",
            tenant_id="tenant:p1",
            automation_id=aid,
            condition_value=val,
            worker_id=f"poll-{idx}",
        )
        if res is not None:
            fired.append(res)

    assert len(fired) == 2
    assert fired[0].outcome == ClaimOutcome.CLAIMED_AND_DISPATCHED
    assert fired[1].outcome == ClaimOutcome.CLAIMED_AND_DISPATCHED
    assert fired[0].idempotency_key != fired[1].idempotency_key
    assert engine.count_executions_for_automation(aid) == 2
    assert engine.count_tasks_for_automation(aid) == 2


# ---------------------------------------------------------------------------
# 7. Two-Layer Idempotency: MOD-17 Occurrence vs MOD-08 Tool-Step Receipts
# ---------------------------------------------------------------------------
def test_09_two_layer_idempotency_mod17_vs_mod08_and_uncertain_outcome(db_dsn: str) -> None:
    engine = AutomationFencingEngine(db_dsn)
    auto = engine.create_automation(tenant_id="tenant:p1", owner_principal="user:owner")
    aid = auto["automation_id"]

    claim = engine.claim_and_dispatch_occurrence(
        tenant_id="tenant:p1",
        automation_id=aid,
        schedule_version=1,
        generation=1,
        trigger_type=TriggerType.EVENT,
        trigger_occurrence_id="event:email-vip-001",
        worker_id="w1",
    )
    assert claim.task_id is not None and claim.execution_id is not None

    # 7a. MOD-08 PROVIDER_IDEMPOTENCY_KEY handles crash after provider execution without duplicate side effect
    provider_calls_idemp: list[str] = []
    provider_dedup_store: dict[str, dict] = {}

    def idemp_provider(action_key: str, args: dict) -> dict:
        if action_key in provider_dedup_store:
            return provider_dedup_store[action_key]
        provider_calls_idemp.append(action_key)
        res = {"ref": f"idemp-{len(provider_calls_idemp)}", "to": args["to"]}
        provider_dedup_store[action_key] = res
        return res

    with pytest.raises(SimulatedCrashError):
        engine.execute_tool_step_with_receipt(
            tenant_id="tenant:p1",
            task_id=claim.task_id,
            execution_id=claim.execution_id,
            logical_step_id="step-email-1",
            tool_id="gmail.send_email",
            tool_version="1.0.0",
            arguments={"to": "vip@example.com", "subject": "Alert"},
            target_resource="res:gmail_primary",
            mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
            provider_fn=idemp_provider,
            crash_after_provider_before_confirm=True,
        )
    assert len(provider_calls_idemp) == 1

    # Retry reuses identical action_idempotency_key; provider deduplicates (still 1 external execution)
    retry_out = engine.execute_tool_step_with_receipt(
        tenant_id="tenant:p1",
        task_id=claim.task_id,
        execution_id=claim.execution_id,
        logical_step_id="step-email-1",
        tool_id="gmail.send_email",
        tool_version="1.0.0",
        arguments={"to": "vip@example.com", "subject": "Alert"},
        target_resource="res:gmail_primary",
        mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
        provider_fn=idemp_provider,
    )
    assert len(provider_calls_idemp) == 1
    assert retry_out["output"]["ref"] == "idemp-1"

    # Subsequent replay returns stored receipt with 0 provider calls
    replay_out = engine.execute_tool_step_with_receipt(
        tenant_id="tenant:p1",
        task_id=claim.task_id,
        execution_id=claim.execution_id,
        logical_step_id="step-email-1",
        tool_id="gmail.send_email",
        tool_version="1.0.0",
        arguments={"to": "vip@example.com", "subject": "Alert"},
        target_resource="res:gmail_primary",
        mode=ExternalIdempotencyMode.PROVIDER_IDEMPOTENCY_KEY,
        provider_fn=idemp_provider,
    )
    assert replay_out["replayed"] is True
    assert len(provider_calls_idemp) == 1

    # 7b. MOD-08 NON_IDEMPOTENT_NO_RECONCILE prohibits blind retry after crash -> UncertainOutcomeError
    non_idemp_calls: list[str] = []

    def non_idemp_provider(action_key: str, args: dict) -> dict:
        non_idemp_calls.append(action_key)
        return {"ref": f"nonidemp-{len(non_idemp_calls)}"}

    with pytest.raises(SimulatedCrashError):
        engine.execute_tool_step_with_receipt(
            tenant_id="tenant:p1",
            task_id=claim.task_id,
            execution_id=claim.execution_id,
            logical_step_id="step-sms-2",
            tool_id="sms.send_raw",
            tool_version="1.0.0",
            arguments={"to": "+15550100", "text": "Ping"},
            target_resource="res:sms",
            mode=ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE,
            provider_fn=non_idemp_provider,
            crash_after_provider_before_confirm=True,
        )
    assert len(non_idemp_calls) == 1

    with pytest.raises(UncertainOutcomeError):
        engine.execute_tool_step_with_receipt(
            tenant_id="tenant:p1",
            task_id=claim.task_id,
            execution_id=claim.execution_id,
            logical_step_id="step-sms-2",
            tool_id="sms.send_raw",
            tool_version="1.0.0",
            arguments={"to": "+15550100", "text": "Ping"},
            target_resource="res:sms",
            mode=ExternalIdempotencyMode.NON_IDEMPOTENT_NO_RECONCILE,
            provider_fn=non_idemp_provider,
        )
    # Blind duplicate side effect strictly prevented!
    assert len(non_idemp_calls) == 1

    # 7c. MOD-08 RECONCILABLE_READ_PROBE reconciles UNKNOWN_OUTCOME via probe_fn without re-executing provider
    reconcilable_calls: list[str] = []
    probe_calls: list[str] = []

    def reconcilable_provider(action_key: str, args: dict) -> dict:
        reconcilable_calls.append(action_key)
        return {"ref": f"cal-{len(reconcilable_calls)}", "event": args["title"]}

    def read_probe(action_key: str) -> dict:
        probe_calls.append(action_key)
        return {"executed": True, "output": {"ref": "cal-1", "status": "already_created"}}

    with pytest.raises(SimulatedCrashError):
        engine.execute_tool_step_with_receipt(
            tenant_id="tenant:p1",
            task_id=claim.task_id,
            execution_id=claim.execution_id,
            logical_step_id="step-calendar-3",
            tool_id="gcal.create_event",
            tool_version="1.0.0",
            arguments={"title": "Review Sync"},
            target_resource="res:gcal_primary",
            mode=ExternalIdempotencyMode.RECONCILABLE_READ_PROBE,
            provider_fn=reconcilable_provider,
            probe_fn=read_probe,
            crash_after_provider_before_confirm=True,
        )
    assert len(reconcilable_calls) == 1
    assert len(probe_calls) == 0

    reconciled_out = engine.execute_tool_step_with_receipt(
        tenant_id="tenant:p1",
        task_id=claim.task_id,
        execution_id=claim.execution_id,
        logical_step_id="step-calendar-3",
        tool_id="gcal.create_event",
        tool_version="1.0.0",
        arguments={"title": "Review Sync"},
        target_resource="res:gcal_primary",
        mode=ExternalIdempotencyMode.RECONCILABLE_READ_PROBE,
        provider_fn=reconcilable_provider,
        probe_fn=read_probe,
    )
    assert reconciled_out["reconciled"] is True
    assert reconciled_out["output"]["status"] == "already_created"
    assert len(probe_calls) == 1
    assert len(reconcilable_calls) == 1

    replay_reconciled = engine.execute_tool_step_with_receipt(
        tenant_id="tenant:p1",
        task_id=claim.task_id,
        execution_id=claim.execution_id,
        logical_step_id="step-calendar-3",
        tool_id="gcal.create_event",
        tool_version="1.0.0",
        arguments={"title": "Review Sync"},
        target_resource="res:gcal_primary",
        mode=ExternalIdempotencyMode.RECONCILABLE_READ_PROBE,
        provider_fn=reconcilable_provider,
        probe_fn=read_probe,
    )
    assert replay_reconciled["replayed"] is True
    assert len(reconcilable_calls) == 1
    assert len(probe_calls) == 1
