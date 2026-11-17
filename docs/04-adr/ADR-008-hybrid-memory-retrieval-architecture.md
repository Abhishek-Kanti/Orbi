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
     - `VectorBranch`: Semantic similarity (via internal replaceable `CogneeAdapter` or verified vector store).
     - `BM25Branch` *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)*: Lexical keyword branch backed by a configurable `LexicalBackendPort` separately evaluating **PostgreSQL FTS** (`tsvector` + `tsquery` + `ts_rank`/`ts_rank_cd`, never mislabeled as BM25) vs. **Genuine BM25** (e.g., `pg_search` extension, if selected & validated) under `EVAL-07` before introducing any dedicated search engine (`AP-09`).
     - `GraphBranch`: Knowledge graph entity/relationship traversal via replaceable `CogneeAdapter`, preserving `source_memory_unit_ids[]` on every entity, edge, attribute, and triplet.
     - `TemporalBranch`: Selective **bitemporal** interval retrieval over **valid time** (`valid_from`, `valid_to`) and **system/transaction time** (`recorded_from`, `recorded_to`), kept strictly distinct from forward lineage (`superseded_by`), dispatching `CURRENT` (`valid_to IS NULL AND recorded_to IS NULL AND superseded_by IS NULL` alongside `deletion_state = 'ACTIVE'`), `HISTORICAL` (point-in-time queries over valid/system time without excluding later-superseded historical facts), and `RANGE` overlap predicates.
   - **Stage 3 — Mandatory Per-Branch Pre-Fusion OpenFGA Authz & Authoritative Deletion/Tombstone Gate (`FR-MS-12`, `FR-MS-14`, `NFR-SEC-04`)**:
     - Every branch (`VectorBranch`, `BM25Branch`, `GraphBranch`, `TemporalBranch`) enforces **both** resource-level OpenFGA checks (`private_user`, `shared_tenant`, `custom_acl`) and the **authoritative PostgreSQL `orbi_memory` deletion/tombstone gate** (`deletion_state == 'ACTIVE'` and no matching `memory_deletion_requests` tombstone) **before** candidates leave the retrieval boundary—blocking stale `CogneeAdapter` hits even during `PENDING_PROPAGATION` or `FAILED_REQUIRES_RECONCILIATION`.
     - For `GraphBranch`, every contributing `source_memory_unit_id` must pass both `deletion_state == 'ACTIVE'` and OpenFGA `Check` independently with zero permission unioning across convergent shared/private entities (`"Project Apollo"`), multi-hop paths require authorization of every hop's active sources, and missing provenance fails closed.
     - Unauthorized or deleted memories, graph facets, edges, summaries, and metadata are **never** passed to Candidate Fusion, Reranking, or LLM context construction. Discovery of a memory/entity never implies read permission.
   - **Stage 4 — Configurable Candidate Fusion & `memory_unit_id`-Only Deduplication *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)***:
     - Because parallel branches return scores on incompatible scales (`cosine`, `ts_rank_cd` or `BM25`, graph hop weights, temporal overlap), direct raw-score addition is invalid; **Reciprocal Rank Fusion (RRF)** is adopted as the **initial proposed rank-fusion strategy** behind pluggable `CandidateFusionPort`.
     - **`memory_unit_id` is the SOLE authoritative identity-based deduplication / merge key** (`content_hash` must never merge records across different owners, tenants, permissions, provenance, timelines, or lifecycle states). Preserves full multi-branch provenance.
   - **Stage 5 — Configurable Replaceable Reranking *(`[PROPOSED]` / `[EVALUATION REQUIRED]`)***:
     - Evaluates fused candidates (`candidate_limit`) against the original query and slices to `final_context_limit` while preserving provenance and relevance scores.
     - Backed by a pluggable `RerankerPort` (`NoOpReranker`, lightweight cross-encoder, external API reranker, or conditional LLM reranker). **Expensive LLM reranking must never be mandatory for every retrieval.**

2. **Cognee Isolation, Resolution Lifecycle (`NEEDS_CLARIFICATION` HITL), Deletion & Gap Ownership**:
   - Cognee stays strictly encapsulated behind `MOD-10`'s `CogneeAdapter`. `MOD-10` owns the internal 5-outcome **Memory Resolution** stage (`CREATE`, `REINFORCE`, `REFINE`, `SUPERSEDE`, `NEEDS_CLARIFICATION`), separating semantic LLM proposals from deterministic `MOD-10` validation and atomic PostgreSQL + `memory_sync_outbox` commits. Unresolved `NEEDS_CLARIFICATION` items reside in `orbi_memory.memory_resolution_candidates` (zero premature retrieval visibility) and solicit clarification via `MOD-03` (`HumanInteractionManager`) -> `MOD-18` -> `MOD-01`/`MOD-02`. When resolved, `MOD-03` persists `InteractionStatus.RESOLVED` atomically with a `MEMORY_CLARIFICATION_DECIDED` event in `orbi_app.task_lifecycle_outbox` (`idempotency_key = sha256(memory_candidate_id, interaction_id)`), delivered by the leased outbox dispatcher (with bounded retries + `DEAD_LETTERED` quarantine) to `MOD-10.submit_clarification_decision()`, deduplicated via `orbi_memory.memory_sync_processed_events`, and backed by a periodic cross-schema reconciler matching `RESOLVED` interactions against `PENDING_CLARIFICATION` candidates while preserving `original_source_provenance` and `original_trust_tier`.
   - `MOD-10` owns the 9-step **Explicit Memory Deletion** lifecycle (`REQUESTED -> PENDING_PROPAGATION -> COMPLETED | FAILED_REQUIRES_RECONCILIATION`), capturing a non-plaintext `deletion_dependency_manifest` **prior** to scrubbing plaintext (`content`/`summary`/`source_provenance`), maintaining immediate `deletion_state != 'ACTIVE'` suppression across all 4 branches, pruning/recomputing graph sources, and setting terminal `memory_units.deletion_state = 'COMPLETED'` tombstones. Any retrieval capability not natively/reliably supplied by Cognee in `EVAL-03` (such as BM25, bitemporal validity, or fine-grained ACL pre-checks) is owned by `MOD-10` using PostgreSQL (`orbi_memory`) or dedicated adapters.

3. **Mandatory Quantitative, Resolution, Deletion & Leakage Evaluation (`EVAL-07`)**:
   - Quality, lifecycle integrity, and security overhead must be benchmarked (`Recall@K`, `Precision@K`, `MRR`, `NDCG@K`, retrieval latency, reranking overhead with vs. without reranker, and $0.0\%$ authorization leakage) across retrieval (`MEM-EVAL-01`–`14`), internal memory resolution (`MEM-RES-01`–`10`), and explicit deletion propagation/audit (`MEM-DEL-01`–`06`) test suites before locking final HLD parameters.

---

## Consequences

### Positive
- Prevents vendor/framework lock-in to Cognee's default single-strategy retrieval behavior.
- Guarantees defense-in-depth privacy for single-owner and future household multi-member isolation (`CON-13`).
- Avoids unnecessary search cluster sprawl (`AP-09`) unless `EVAL-07` proves PostgreSQL search insufficient.
- Controls LLM cost and latency by making Query Understanding selective and Reranking configurable/non-LLM-mandatory.

### Negative / Risks
- **Dual-Store Synchronization (`CON-14`)**: Keeping PostgreSQL's canonical `orbi_memory` catalog (for bitemporal intervals, lexical search, and ACL metadata) consistent with `CogneeAdapter` vector/graph stores requires atomic PostgreSQL **`memory_sync_outbox`** commits, leased dispatcher retries, idempotent consumers, and `DEAD_LETTERED` poison-event handling.
- **OCI VM Resource Contention (`CON-15`)**: Parallel branches and optional local cross-encoder rerankers increase memory/CPU pressure on a single OCI VM, requiring empirical sizing in `EVAL-05` and `EVAL-07`.
- **Proposed Components Flagged**: BM25, RRF, and Reranking remain `[PROPOSED]` / `[EVALUATION REQUIRED]` pending `EVAL-03` and `EVAL-07` results—not assumed capabilities.
