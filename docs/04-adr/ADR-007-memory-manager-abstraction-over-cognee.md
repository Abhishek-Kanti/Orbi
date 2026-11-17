# ADR-007: `MOD-10: Memory Manager` Ownership & Replaceable `CogneeAdapter` Boundary

- **Status**: Accepted *(Capabilities & Storage Backends Pending `EVAL-03` / `OQ-06`; Extended by [ADR-008](file:///f:/Projects/Orbi/docs/04-adr/ADR-008-hybrid-memory-retrieval-architecture.md))*
- **Date**: `2026-11-16` *(Revised)*
- **Drivers**: Decision 6, Hybrid Memory Retrieval Mandate, Principles `AP-04`, `AP-08`, `AP-09`

---

## Context

Orbi requires associative long-term knowledge memory managed by **`MOD-10: Memory Manager`**, kept strictly distinct from transactional task/application state in PostgreSQL and versioned skills (`AP-04`). **Cognee** is treated strictly as an internal, replaceable adapter (**`CogneeAdapter`**)—never as `MOD-10` itself—because its vector and graph backends, operational stability, and container footprint on a single OCI VM (`ADR-001`, `AP-09`) must be verified empirically rather than assumed, and Cognee cannot be assumed to own Orbi's memory lifecycle, hybrid retrieval, provenance, temporal validity, or authorization enforcement.

---

## Decision

1. **Canonical `MOD-10: Memory Manager` Ownership & Replaceable `CogneeAdapter` Boundary**:
   - **`MOD-10: Memory Manager`** is the canonical module name and **not** Cognee itself or a thin wrapper around Cognee's default retrieval API. As expanded in [ADR-008](file:///f:/Projects/Orbi/docs/04-adr/ADR-008-hybrid-memory-retrieval-architecture.md) and [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md), `MOD-10: Memory Manager` owns the complete memory lifecycle: internal **Memory Resolution** (`CREATE`, `REINFORCE`, `REFINE`, `SUPERSEDE`, `NEEDS_CLARIFICATION`), separating **real-world succession** (`valid_to`) from **belief correction** (`recorded_to`) and **forward lineage** (`superseded_by`), **Explicit Memory Deletion** (`REQUESTED -> PENDING_PROPAGATION -> COMPLETED | FAILED_REQUIRES_RECONCILIATION` with immediate read-gate suppression and graph source recomputation), quarantine/curation, atomic PostgreSQL + **`memory_sync_outbox`** commits, the 5-stage Hybrid Retrieval Pipeline, provenance (`TrustTier`), and mandatory per-branch & per-source pre-fusion OpenFGA authorization enforcement (`MOD-09`).
   - **Cognee** is encapsulated strictly as an internal, replaceable adapter (`CogneeAdapter`) backing vector/graph operations inside `MOD-10`. All Core modules (`MOD-04` agents for scoped read/proposal, `MOD-02` Dashboard for owner inspection/editing/deletion) interact exclusively with **`MOD-10: Memory Manager`** domain interfaces—never directly with Cognee internal tables, graph APIs, or vector indices.
   - `MOD-10: Memory Manager` enforces per-branch OpenFGA `tenant_id` + `owner_principal` visibility scoping (`ADR-002`, `FR-MS-12`), source provenance tagging (`user_explicit`, `system_verified`, `untrusted_external`), and untrusted memory quarantine (`CON-07`, `CON-16`).
2. **6-Dimension Cognee Audit & Storage Backend Deferral (`EVAL-03`, `OQ-06`)**:
   - Minimal storage infrastructure (e.g., evaluating PostgreSQL native search + `pgvector` before introducing dedicated search/graph engines) is **preferred** to align with `AP-09` and `EVAL-05` single-VM limits.
   - Do **not** assume Cognee automatically delivers our entire hybrid retrieval pipeline. Before HLD finalization, `EVAL-03` must evaluate the target Cognee version and backend across six explicit dimensions:
     1. **Vector retrieval**
     2. **Graph retrieval**
     3. **Lexical retrieval parity** *(distinguishing PG FTS vs. Genuine BM25)*
     4. **Filtering, `source_memory_unit_ids[]` provenance preservation, and authorization**
     5. **Metadata and trust provenance preservation**
     6. **Bitemporal retrieval** *(valid time + system time)*
   - Missing or insufficient capabilities in Cognee shall be implemented natively in `MOD-10: Memory Manager` over `orbi_memory`.

---

## Consequences

### Positive
- Shields `orbi-core`, LangGraph agents, and Dashboard APIs from Cognee SDK/schema churn or potential future memory backend swaps.
- Prevents unverified infrastructure assumptions from locking HLD into unsupported Cognee storage drivers.

### Negative / Risks
- Encapsulating `CogneeAdapter` as a replaceable internal adapter inside `MOD-10: Memory Manager` requires maintaining deterministic identity, provenance, bitemporal, and ACL synchronization between PostgreSQL's canonical `orbi_memory` catalog and Cognee's internal vector/graph stores via transactional **`memory_sync_outbox`** (`CON-14`), which must be validated cleanly during `EVAL-03` and `EVAL-07`.
