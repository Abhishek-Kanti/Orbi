# Orbi — High-Level Design (HLD) Documentation

- **Phase**: `02-HLD`
- **Last Updated**: `2026-11-16`
- **Traceability**: Realizes Product Baseline ([docs/00-product/](file:///f:/Projects/Orbi/docs/00-product/requirements.md)), GLD ([docs/01-gld/](file:///f:/Projects/Orbi/docs/01-gld/system-overview.md)), and ADRs ([docs/04-adr/](file:///f:/Projects/Orbi/docs/04-adr/README.md)).

---

## HLD Document Index

| Document ID | Title | Scope | Status |
| :--- | :--- | :--- | :--- |
| **`DOC-HLD-002`** | **[system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md)** | Overall Orbi System HLD: Context, 4-Plane Logical Architecture, Deployment & Trust Boundaries, Agent Architecture, Tool Platform, Task Orchestration, Identity/OpenFGA/Action-Bound Approvals, Memory Integration, Skill Lifecycle (`MARKDOWN_ONLY` -> Sandboxed Scripts), Cloud Browser & Takeover, Windows Local Companion, Integrations, Dashboard, Data Architecture, Security/Reliability, 11 Diagrams + 5 Sequence Flows, and Open Trade-Offs (`OQ-01`–`OQ-14`). | `Draft - For Review` |
| **`DOC-HLD-001`** | **[memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md)** | Dedicated Memory Subsystem HLD: 5-Stage Hybrid Retrieval Pipeline (`MOD-10`), `MemoryUnit` Canonical Schema, Cognee vs. PostgreSQL Split (`EVAL-03`), Typed Component Ports, Per-Branch Pre-Fusion OpenFGA Boundaries, Failure Handling, and `EVAL-07` Quantitative & Authorization Leakage Evaluation Plan (`MEM-EVAL-01`–`10`). | `Draft - For Review` |

---

## Pre-Implementation Gate Summary

All `[EVALUATION REQUIRED]` items (`EVAL-01`–`EVAL-07`) and `[OPEN QUESTION]` choices (`OQ-01`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-08`, `OQ-11`–`OQ-14`) remain explicitly open and must be validated before locking LLD schemas (`docs/03-lld/`) or starting application implementation.
