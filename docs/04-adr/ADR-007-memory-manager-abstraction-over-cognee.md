# ADR-007: Memory Manager Facade Encapsulating Cognee

- **Status**: Accepted *(Capabilities & Storage Backends Pending `EVAL-03` / `OQ-06`; Extended by [ADR-008](file:///f:/Projects/Orbi/docs/04-adr/ADR-008-hybrid-memory-retrieval-architecture.md))*
- **Date**: `2026-11-16` *(Revised)*
- **Drivers**: Decision 6, Hybrid Memory Retrieval Mandate, Principles `AP-04`, `AP-08`, `AP-09`

---

## Context

Orbi requires associative long-term knowledge memory powered by **Cognee** (`MOD-10`), kept strictly distinct from transactional task/application state in PostgreSQL and versioned skills (`AP-04`). At the same time, Cognee supports multiple relational, vector, and graph backends whose operational stability and container footprint on a single OCI VM (`ADR-001`, `AP-09`) must be verified empirically rather than assumed.

---

## Decision

1. **Memory Manager Ownership & Cognee Boundary (`MOD-10`)**:
   - `MOD-10` is **not** a thin wrapper around Cognee's default retrieval API. As expanded in [ADR-008](file:///f:/Projects/Orbi/docs/04-adr/ADR-008-hybrid-memory-retrieval-architecture.md), `MOD-10` owns and orchestrates the configurable Hybrid Retrieval Pipeline, keeping Cognee strictly behind an internal adapter boundary (`CogneeAdapter`).
   - All Core modules (`MOD-04` agents for scoped read/proposal, `MOD-02` Dashboard for owner inspection/editing/deletion) interact exclusively with Orbi's **Memory Manager** domain interface—never directly with Cognee internal tables, graph APIs, or vector indices.
   - The Memory Manager enforces per-branch OpenFGA `user_id` + `household_id` visibility scoping (`ADR-002`, `FR-MS-12`), source provenance tagging (`user_explicit` vs. `untrusted_external`), and untrusted memory quarantine (`CON-07`).
2. **6-Dimension Cognee Audit & Storage Backend Deferral (`EVAL-03`, `OQ-06`)**:
   - Minimal storage infrastructure (e.g., evaluating PostgreSQL native search + `pgvector` before introducing dedicated search/graph engines) is **preferred** to align with `AP-09` and `EVAL-05` single-VM limits.
   - Do **not** assume Cognee automatically delivers our entire hybrid retrieval pipeline. Before HLD finalization, `EVAL-03` must evaluate the target Cognee version and backend across six explicit dimensions:
     1. **Vector retrieval**
     2. **Graph retrieval**
     3. **Keyword/BM25 retrieval**
     4. **Filtering and authorization**
     5. **Metadata and provenance preservation**
     6. **Temporal retrieval**
   - Missing or insufficient capabilities in Cognee shall be implemented natively in `MOD-10` or dedicated adapters (e.g., PostgreSQL `orbi_memory` catalog).

---

## Consequences

### Positive
- Shields `orbi-core`, LangGraph agents, and Dashboard APIs from Cognee SDK/schema churn or potential future memory backend swaps.
- Prevents unverified infrastructure assumptions from locking HLD into unsupported Cognee storage drivers.

### Negative / Risks
- Introduces a thin translation layer (`MOD-10`) between Orbi's provenance/household metadata and Cognee's native entity/dataset abstractions, which must be designed cleanly during `EVAL-03` and HLD.
