# Orbi — High-Level Design (HLD) Documentation

- **Phase**: `02-HLD`
- **Last Updated**: `2026-11-17`
- **Traceability**: Realizes Product Baseline ([docs/00-product/](file:///f:/Projects/Orbi/docs/00-product/requirements.md)), GLD ([docs/01-gld/](file:///f:/Projects/Orbi/docs/01-gld/system-overview.md)), and ADRs ([docs/04-adr/](file:///f:/Projects/Orbi/docs/04-adr/README.md)).

---

## HLD Document Index

| Document ID | Title | Scope | Status |
| :--- | :--- | :--- | :--- |
| **`DOC-HLD-002`** | **[system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md)** | Overall Orbi System HLD (`Rev 1.4`): Context, 4-Plane Logical Architecture, Deployment & Trust Boundaries, Agent Architecture, Tool Platform (`MOD-08` Gate 2 `action_idempotency_key` + write-ahead `PREPARED -> DISPATCHING -> CONFIRMED_SUCCESS | UNKNOWN_OUTCOME` receipts + Gate 5 `claim_epoch`/cancellation/reconciliation gate), Task Orchestration (`SUSPENDING_FOR_USER` 2-Phase Checkpoint Protocol, `WAITING_FOR_USER`, `HumanInteractionManager`, `task_lifecycle_outbox` & stale-`RUNNING` `RESUME_TASK` lease recovery), **Autonomous Automation Platform (`MOD-17`) with Tenant/Principal Model, Typed Targets, Permission Envelopes, Transactional Outbox, Cohesive Loop Protection, Full Lifecycle (`CREATE`–`DELETE` 6-Step Ordering), Generation-Fenced Cancellation Safety + HITL Integration & Result Delivery (`MOD-18`)**, **Centralized Human-in-the-Loop Interaction & Execution Suspension (`§E.5`: 5 Interaction Types, 3-Step `interaction_version` CAS, Pre-Mutation `AUTHZ_DENIED` vs. `REJECTED` vs. `INVALIDATED`, Bounded-Lease `claim_epoch` Recovery & Uncertain-Outcome Escalation, Multi-Channel Telegram/Dashboard Handoff, Bypass-Free `SECURE_INPUT_REQUEST`, `HUMAN_TAKEOVER`, `HITL-EVAL-01`–`26`)**, Identity/OpenFGA/Approvals, `MOD-10` Integration, Skill Lifecycle, Cloud Browser, Windows Companion, Integrations, Dashboard, Data Architecture, Security/Reliability, 14 Diagrams + 1 ER + 11 Sequence Flows, `AUTO-EVAL-01`–`12`, and Open Trade-Offs (`OQ-01`–`OQ-17`). | `Draft - For Review` |
| **`DOC-HLD-001`** | **[memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md)** | Dedicated Memory Subsystem HLD (`Rev 1.3`): **`MOD-10: Memory Manager`** 5-Stage Hybrid Retrieval Pipeline, Internal Replaceable `CogneeAdapter`, **Authoritative 4-Branch Pre-Fusion Deletion/Tombstone Gate**, 5-Outcome Memory Resolution Lifecycle & Outbox-Backed Idempotent HITL `NEEDS_CLARIFICATION` Callback + Reconciliation (`§5`), Correction vs. 9-Step Explicit Deletion Workflow with Pre-Scrub `deletion_dependency_manifest` & Terminal `COMPLETED` Tombstone State (`§6`), Bitemporal (`valid_from`/`valid_to` + `recorded_from`/`recorded_to` + distinct `superseded_by`) Combined Queries, PG FTS vs. Genuine BM25, `memory_unit_id`-Only Deduplication, Source-Level Graph Provenance Authz, Transactional `memory_sync_outbox`, and `EVAL-07` Catalogue (`MEM-EVAL-01`–`14`, `MEM-RES-01`–`10`, `MEM-DEL-01`–`06`). | `Draft - For Review` |

---

## Pre-Implementation Gate Summary

All `[EVALUATION REQUIRED]` items (`EVAL-01`–`EVAL-08`) and `[OPEN QUESTION]` choices (`OQ-01`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-08`, `OQ-11`–`OQ-17`) remain explicitly open and must be validated before locking LLD schemas (`docs/03-lld/`) or starting application implementation.
