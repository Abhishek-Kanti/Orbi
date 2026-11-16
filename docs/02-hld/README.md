# Orbi — High-Level Design (HLD) Documentation

- **Phase**: `02-HLD`
- **Last Updated**: `2026-11-17`
- **Traceability**: Realizes Product Baseline ([docs/00-product/](file:///f:/Projects/Orbi/docs/00-product/requirements.md)), GLD ([docs/01-gld/](file:///f:/Projects/Orbi/docs/01-gld/system-overview.md)), and ADRs ([docs/04-adr/](file:///f:/Projects/Orbi/docs/04-adr/README.md)).

---

## HLD Document Index

| Document ID | Title | Scope | Status |
| :--- | :--- | :--- | :--- |
| **`DOC-HLD-002`** | **[system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md)** | Overall Orbi System HLD (`Rev 1.2`): Context, 4-Plane Logical Architecture, Deployment & Trust Boundaries, Agent Architecture, Tool Platform, Task Orchestration, **Autonomous Automation Platform (`MOD-17`) with Tenant/Principal Model, Typed Execution Targets, Permission Envelopes, Transactional Outbox & Cohesive Loop Protection + Result Delivery (`MOD-18`)**, Identity/OpenFGA/Action-Bound Approvals, Memory Integration (`MOD-10: Memory Manager`), Skill Lifecycle, Cloud Browser, Windows Companion, Integrations, Dashboard, Data Architecture, Security/Reliability, 12 Architecture/State Diagrams + 1 ER + 7 End-to-End Sequence Flows, and Open Trade-Offs (`OQ-01`–`OQ-17`). | `Draft - For Review` |
| **`DOC-HLD-001`** | **[memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md)** | Dedicated Memory Subsystem HLD (`Rev 1.1`): **`MOD-10: Memory Manager`** 5-Stage Hybrid Retrieval Pipeline, Internal Replaceable `CogneeAdapter`, Bitemporal (`valid_from`/`valid_to` + `recorded_from`/`recorded_to`) Schema & `CURRENT`/`HISTORICAL`/`RANGE` Queries, PG FTS vs. Genuine BM25 Evaluation, `memory_unit_id`-Only Deduplication, Source-Level Graph Provenance Authz, Transactional `memory_sync_outbox`, and `EVAL-07` Quantitative & Leakage Suite (`MEM-EVAL-01`–`14`). | `Draft - For Review` |

---

## Pre-Implementation Gate Summary

All `[EVALUATION REQUIRED]` items (`EVAL-01`–`EVAL-08`) and `[OPEN QUESTION]` choices (`OQ-01`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-08`, `OQ-11`–`OQ-17`) remain explicitly open and must be validated before locking LLD schemas (`docs/03-lld/`) or starting application implementation.
