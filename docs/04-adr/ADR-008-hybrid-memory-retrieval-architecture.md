# ADR-008: Hybrid Memory Retrieval Architecture & Per-Branch OpenFGA Authorization

- **Status**: Accepted *(BM25 Backend, RRF Tuning & Reranker Selection Pending `EVAL-03` / `EVAL-07`)*
- **Date**: `2026-11-16`
- **Drivers**: Hybrid Memory Retrieval Mandate, `FR-MS-08`–`FR-MS-13`, `NFR-SEC-04`, Principles `AP-02`, `AP-04`, `AP-08`, `AP-09`
- **Extends**: [ADR-007](file:///f:/Projects/Orbi/docs/04-adr/ADR-007-memory-manager-abstraction-over-cognee.md)
- **Detailed HLD**: [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md)

---

## Context

Relying solely on a thin wrapper over Cognee's default retrieval API cannot satisfy Orbi's accuracy, temporal correctness, provider-independence, infrastructure minimalism, or zero-leakage authorization requirements:
1. Pure vector similarity struggles on exact identifiers, product codes, names, and lexical keywords.
2. Relational queries across people, projects, devices, and household roles require graph traversal.
3. Time-sensitive and validity-sensitive facts ("current preference", "what changed last month") require temporal filtering and supersession tracking—without forcing temporal scans on every non-temporal query.
4. Personal and future household memories (`ADR-002`) demand strict OpenFGA enforcement *before* candidates from different stores mix together or enter prompt construction.
5. `AP-09` forbids prematurely adding external search engines (e.g., Elasticsearch/OpenSearch) if PostgreSQL native search can meet quality and latency targets.

---

## Decision

1. **Memory Manager Owns a Configurable 5-Stage Hybrid Retrieval Pipeline (`MOD-10`)**:
   - **Stage 1 — Provider-Independent Query Understanding**: Extracts intent, entities, keywords/exact terms, temporal constraints, graph-traversal applicability, and active branch selection. Uses deterministic/lexical rules (`MOD-05` or lightweight parser) first and avoids unnecessary LLM calls for straightforward queries (`FR-MS-08`).
   - **Stage 2 — Independently Configurable Parallel Retrieval Branches**:
     - `VectorBranch`: Semantic similarity (via Cognee adapter or verified vector store).
     - `BM25Branch` *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)*: Exact/lexical keyword scoring. **PostgreSQL native full-text search / BM25 extensions must be evaluated (`EVAL-07`) before introducing any dedicated search engine (`AP-09`).**
     - `GraphBranch`: Knowledge graph entity/relationship traversal (via Cognee graph adapter).
     - `TemporalBranch`: Time-range and validity-interval retrieval (`event_time`, `valid_from`, `valid_to`), activated **selectively** only when Query Understanding identifies temporal/validity constraints.
   - **Stage 3 — Mandatory Per-Branch Pre-Fusion OpenFGA Authorization (`FR-MS-12`, `NFR-SEC-04`)**:
     - Every branch executes resource-level OpenFGA checks (`private_user`, `shared_household`, custom ACL scopes) **before** candidates leave the retrieval boundary.
     - Unauthorized memories, graph entities, relationships, documents, and metadata are **never** passed to Candidate Fusion, Reranking, or LLM context construction. Discovery of a memory/entity never implies read permission.
   - **Stage 4 — Configurable Candidate Fusion *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)***:
     - **Reciprocal Rank Fusion (RRF)** is adopted as the **initial proposed rank-fusion strategy** because parallel branches emit scores on incompatible scales (cosine similarity vs. BM25 vs. graph hop distance vs. temporal recency).
     - Deduplicates canonical `MemoryUnit` items across branches, preserves full multi-branch provenance, and uses a pluggable `CandidateFusionPort` so alternative fusion strategies can be benchmarked later.
   - **Stage 5 — Configurable Replaceable Reranking *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)***:
     - Evaluates fused candidates (`candidate_limit`) against the original query and slices to `final_context_limit` while preserving provenance and relevance scores.
     - Backed by a pluggable `RerankerPort` (`NoOpReranker`, lightweight cross-encoder, external API reranker, or conditional LLM reranker). **Expensive LLM reranking must never be mandatory for every retrieval.**

2. **Cognee Isolation & Gap Ownership**:
   - Cognee stays strictly encapsulated behind `MOD-10`'s `CogneeAdapter`. Any retrieval capability not natively/reliably supplied by Cognee in `EVAL-03` (such as BM25, bi-temporal validity, or fine-grained ACL pre-checks) is owned by `MOD-10` using PostgreSQL (`orbi_memory`) or dedicated adapters.

3. **Mandatory Quantitative & Leakage Evaluation (`EVAL-07`)**:
   - Quality and overhead must be benchmarked (`Recall@K`, `Precision@K`, `MRR`, `NDCG@K`, retrieval latency, reranking overhead with vs. without reranker, and $0.0\%$ authorization leakage) across semantic, keyword, graph, and temporal test suites before locking final HLD parameters.

---

## Consequences

### Positive
- Prevents vendor/framework lock-in to Cognee's default single-strategy retrieval behavior.
- Guarantees defense-in-depth privacy for single-owner and future household multi-member isolation (`CON-13`).
- Avoids unnecessary search cluster sprawl (`AP-09`) unless `EVAL-07` proves PostgreSQL search insufficient.
- Controls LLM cost and latency by making Query Understanding selective and Reranking configurable/non-LLM-mandatory.

### Negative / Risks
- **Dual-Store Synchronization (`CON-14`)**: Keeping PostgreSQL's canonical `orbi_memory` catalog (for BM25/temporal/ACL metadata) consistent with Cognee's vector/graph stores requires idempotent outbox/reconciliation handling.
- **OCI VM Resource Contention (`CON-15`)**: Parallel branches and optional local cross-encoder rerankers increase memory/CPU pressure on a single OCI VM, requiring empirical sizing in `EVAL-05` and `EVAL-07`.
- **Proposed Components Flagged**: BM25, RRF, and Reranking remain `[PROPOSED]` / `[EVALUATION REQUIRED]` pending `EVAL-03` and `EVAL-07` results—not assumed capabilities.
