# ADR-007: Memory Manager Facade Encapsulating Cognee

- **Status**: Accepted *(Supported Storage Backends Pending `EVAL-03` / `OQ-06`)*
- **Date**: `2026-11-14`
- **Drivers**: Decision 6, Principles `AP-04`, `AP-08`, `AP-09`

---

## Context

Orbi requires associative long-term knowledge memory powered by **Cognee** (`MOD-10`), kept strictly distinct from transactional task/application state in PostgreSQL and versioned skills (`AP-04`). At the same time, Cognee supports multiple relational, vector, and graph backends whose operational stability and container footprint on a single OCI VM (`ADR-001`, `AP-09`) must be verified empirically rather than assumed.

---

## Decision

1. **Memory Manager Facade (`MOD-10`)**:
   - All Core modules (`MOD-04` agents for scoped read/proposal, `MOD-02` Dashboard for owner inspection/editing/deletion) interact exclusively with Orbi's **Memory Manager** domain interface—never directly with Cognee internal tables, graph APIs, or vector indices.
   - The Memory Manager enforces OpenFGA `user_id` + `household_id` visibility scoping (`ADR-002`), source provenance tagging (`user_explicit` vs. `untrusted_external`), and untrusted memory quarantine (`CON-07`).
2. **Storage Backend Deferral (`EVAL-03`, `OQ-06`)**:
   - Minimal storage infrastructure (e.g., consolidated PostgreSQL with `pgvector` + minimal graph store) is **preferred** to align with `AP-09` and `EVAL-05` single-VM limits.
   - However, **no specific Cognee vector/graph backend combination is marked confirmed** until `EVAL-03` verifies actual officially supported, stable storage backends in Cognee and measures their memory footprint.

---

## Consequences

### Positive
- Shields `orbi-core`, LangGraph agents, and Dashboard APIs from Cognee SDK/schema churn or potential future memory backend swaps.
- Prevents unverified infrastructure assumptions from locking HLD into unsupported Cognee storage drivers.

### Negative / Risks
- Introduces a thin translation layer (`MOD-10`) between Orbi's provenance/household metadata and Cognee's native entity/dataset abstractions, which must be designed cleanly during `EVAL-03` and HLD.
