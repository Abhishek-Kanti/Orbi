# Orbi — Scope, Evaluation Spikes & Delivery Roadmap

- **Document ID**: `DOC-PRD-003`
- **Revision**: `1.4` *(Updated with Centralized HITL Interaction Evaluation `HITL-EVAL-01`–`26` & Crash-Recovery / Receipt Reconciliation Refinements)*
- **Phase**: `00-Product`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-17`

---

## 1. Engineering Documentation Roadmap (Pre-Implementation Gate)

`[CONFIRMED]` No application code, dependency installation, or framework initialization will begin until the architecture blueprint is reviewed, reconciled, and approved across these documentation phases:

```
[Phase 0: Product & GLD + Initial ADRs]  ──▶  [Phase 1: Spike Evals]  ──▶  [Phase 2: HLD]  ──▶  [Phase 3: LLD]  ──▶  [Implementation]
        (Current Milestone)                      (EVAL-01..08)            (docs/02-hld/)       (docs/03-lld/)
```

| Folder | Target Artifacts | Gate Criterion |
| :--- | :--- | :--- |
| `docs/00-product/` | `vision.md`, `requirements.md`, `scope-and-roadmap.md` *(Rev 1.4)* | Owner alignment on Decisions 1–10, Centralized 5-Type HITL (`FR-TM-06`, `FR-CH-05`, `NFR-SEC-06`), Hybrid Memory (`FR-MS-08`–`14`), Autonomous Agent Automation (`FR-AT-01`–`10`), and open HLD questions (`OQ-01`, `OQ-04`–`06`, `OQ-08`, `OQ-11`–`17`). |
| `docs/01-gld/` | `system-overview.md`, `module-catalog.md`, `functional-responsibilities.md` *(Rev 1.4)* | Sign-off on 3-zone resource isolation, 7-step discovery flow, Centralized `HumanInteractionManager` (`MOD-03`), hybrid retrieval + tombstone gate boundaries, 5-way automation/delivery separation (`MOD-17`/`MOD-18`), Gateway pre-dispatch cancellation checks, non-inherited delegation, action-bound approvals, and `CON-01`–`CON-27` resolutions. |
| `docs/04-adr/` | `README.md`, `ADR-001` through `ADR-009` | Formal capture of confirmed architectural trade-offs and evaluation prerequisites. |
| `docs/02-hld/` | [README.md](file:///f:/Projects/Orbi/docs/02-hld/README.md), [system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md) *(Drafted Rev 1.4)*, [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md) *(Drafted Rev 1.3)* | Verified end-to-end HITL, memory & automation flows, trust boundaries, Dashboard stack (`OQ-01`), sensitivity/TTL matrix (`OQ-08`), automation quotas/condition DSL (`OQ-15`–`17`), and spike outputs (`EVAL-01`–`08`). |
| `docs/03-lld/` | PostgreSQL DDL (`orbi_app`, `orbi_memory`, `orbi_automation`), Redis queue contracts, Tool/Agent Registry schemas, `SKILL.md` package spec, Task FSM, Gateway & Harness APIs | Implementation-ready specifications enabling modular, test-driven coding. |

---

## 2. Mandatory Technology Evaluation Spikes (`EVAL-01` – `EVAL-08`)

`[CONFIRMED]` / `[EVALUATION REQUIRED]` Unverified technology assumptions must **not** be treated as confirmed until small, time-boxed validation spikes verify them against Orbi's architectural principles (`AP-01`–`AP-10`):

### `EVAL-01` — Optional Jev Provider vs. Deterministic & LLM Fallbacks (`MOD-05`, `ADR-003`)
- **Role in Architecture**: Optional System-1 decision provider (`noul`, `choice`, `score`) behind the provider-independent Decision Service (`MOD-05`).
- **Why Evaluation Is Required**: Core platform operation must not depend exclusively on Jev. We must empirically determine whether Jev improves speed/calibration enough over deterministic rules + fast fallback LLMs to justify adding an external vendor dependency.
- **Key Evaluation Questions**:
  1. Benchmark latency, accuracy, and probability calibration across representative Orbi routing/triage/risk prompts compared to deterministic heuristics and lightweight LLM structured outputs.
  2. Define cross-provider confidence normalization rules (`CON-12`) and timeout budgets (`MOD-05` Clarification B).
- **Exit Criteria**: Benchmark table + timeout/fallback specification feeding HLD and updating `ADR-003`.

### `EVAL-02` — Layered Windows Companion (`MOD-14`, `ADR-006`)
- **Role in Architecture**: Local Windows service enforcing strict execution precedence: **(1) Native APIs -> (2) UI Automation (`vercel-labs/agent-desktop`) -> (3) Controlled Vision Fallback**.
- **Why Evaluation Is Required**: `agent-desktop` UIA coverage varies across Win32, WPF, Electron, and custom-rendered UIs, while vision fallbacks introduce coordinate drift, latency, and screen privacy concerns (`CON-09`).
- **Key Evaluation Questions**:
  1. Matrix of which target Windows workflows succeed cleanly via Native APIs vs. `agent-desktop` UIA vs. requiring Vision Fallback.
  2. JSON UIA payload size, cloud-local round-trip latency, and local PII/sensitive-window filtering effectiveness.
  3. Safe confidence thresholds for `STOP_INSUFFICIENT_CONFIDENCE` across UIA matching and Vision grounding (`OQ-12`).
- **Exit Criteria**: Windows target-app compatibility matrix + confidence/redaction specification.

### `EVAL-03` — Cognee Capability Audit & Storage Backends (`MOD-10`, `ADR-007`, `ADR-008`)
- **Role in Architecture**: Internal adapter behind the Memory Manager (`MOD-10`); must **not** be assumed to supply our entire hybrid retrieval pipeline automatically.
- **Why Evaluation Is Required**: Minimal infrastructure is preferred (`AP-09`), **but we must empirically verify what the target Cognee version and storage backends actually support** before finalizing HLD (`OQ-06`):
  1. **Vector retrieval**: Supported vector backends (`pgvector` vs. Qdrant/LanceDB) and recall/filtering accuracy.
  2. **Graph retrieval**: Supported graph backends (embedded Kuzu/NetworkX vs. Neo4j), multi-hop traversal APIs, and edge metadata exposure.
  3. **Lexical retrieval parity**: Whether Cognee provides genuine lexical/BM25 scoring or lacks it (evaluated against `MOD-10`'s configurable `LexicalBackendPort` candidates: **PG FTS** `tsvector`/`ts_rank_cd` vs. **Genuine BM25** `pg_search`).
  4. **Filtering and authorization**: Whether metadata filters can push down securely AND return per-entity/edge **`source_memory_unit_ids[]`** for independent OpenFGA source checks inside `MOD-10`.
  5. **Metadata and provenance**: Survival of `memory_unit_id`, `tenant_id`, `owner_principal`, `source_provenance`, and `trust_tier` across `CogneeAdapter` ingestion.
  6. **Bitemporal retrieval**: Native support vs. missing bitemporal (**valid time** `valid_from`/`valid_to` + **system time** `recorded_from`/`recorded_to` + `superseded_by`) querying.
- **Exit Criteria**: Verified Cognee 6-dimension capability matrix + storage backend footprint fed into `EVAL-05`, `EVAL-07`, and HLD.

### `EVAL-04` — LangGraph & Deterministic Task Manager Turn Handoff & HITL Resumption (`MOD-03`/`MOD-04`, `AP-03`)
- **Role in Architecture**: Ensuring `MOD-03` (including internal `HumanInteractionManager`) owns authoritative task lifecycle (`SUSPENDING_FOR_USER`, `WAITING_FOR_USER`), `task_lifecycle_outbox`, bounded-lease `claim_epoch` recovery, write-ahead `step_execution_receipts` (`action_idempotency_key`, `UNKNOWN_OUTCOME` reconciliation), and immutable approval bindings while LangGraph (`MOD-04`) executes bounded interruptible turns with zero long-lived waiting workers.
- **Why Evaluation Is Required**: Validates that 2-phase checkpoint-first suspension (`SUSPENDING_FOR_USER` -> verified checkpoint -> `WAITING_FOR_USER`), persisting `orbi_app.interaction_requests`, collecting multi-channel / LLM-bypassing inputs, 3-step `interaction_version` CAS (`RESPONSE_RECEIVED -> VALIDATING -> RESOLVED | REJECTED | INVALIDATED`), crash recovery across prepare/outbox/stale-`RUNNING` lease/post-resume-ack and post-dispatch `DISPATCHING -> UNKNOWN_OUTCOME` boundaries, and resuming from `langgraph_checkpoints` across restarts cause zero split-brain conflict or duplicate side effects (`CON-01`, `CON-27`).
- **Exit Criteria**: Prototype turn-state serialization, LangGraph interrupt/resume & crash-reconciler test specification, and verified pass across all 26 HITL evaluation scenarios (`HITL-EVAL-01`–`26` in `DOC-HLD-002` §E.5.7).
- **Validation Status (`2026-11-18`) — `CONDITIONAL PASS`**: Isolated PostgreSQL 16 + `langgraph==1.2.22` prototype ([EVAL-04-REPORT.md](file:///f:/Projects/Orbi/evals/eval-04-langgraph-handoff/EVAL-04-REPORT.md), `12/12` pytest passing) validated core checkpoint handoff, crash recovery, `claim_epoch` fencing, and write-ahead receipt reconciliation. Six empirical constraints were folded into `DOC-HLD-002` `Rev 1.5` (`§E.5.8`); real `orbi-openfga`, `orbi-redis`, `SECURE_INPUT_REQUEST`, `HUMAN_TAKEOVER`, and `MEMORY_CLARIFICATION_DECIDED` remain unvalidated integration boundaries tracked for Phases 1–3.

### `EVAL-05` — OCI Single-VM Docker Compose Resource Benchmarking (`NFR-INF-01`, `ADR-001`)
- **Role in Architecture**: Validate CPU, RAM, disk IOPS, and swap behavior of the full modular Docker Compose stack on a single OCI VM.
- **Why Evaluation Is Required**: **OCI Always Free capacity is explicitly NOT assumed sufficient without benchmarking (`OQ-05`).** Running PostgreSQL, Redis, OpenFGA, `orbi-core`, Playwright Chromium (`MOD-13`), Dashboard (`MOD-02`), Skill Script Sandbox (`MOD-11-SANDBOX`), and Cognee's verified backends (`EVAL-03`) concurrently can easily exceed modest free-tier memory budgets.
- **Exit Criteria**: Measured idle and peak RAM/CPU budget per container + concrete OCI VM shape recommendation for HLD.

### `EVAL-06` — Sandboxed Skill Script Execution Mechanism (`MOD-11-SANDBOX`, `ADR-005`)
- **Role in Architecture**: Isolated execution environment for optional supporting scripts bundled inside Markdown skill packages (`SKILL.md`).
- **Why Evaluation Is Required**: Supporting scripts must execute without host filesystem access, ambient credentials, direct external network access, or Gateway bypass (`CON-08`, `OQ-11`).
- **Exit Criteria**: Selection and threat-model verification of sandbox isolation technology (e.g., ephemeral unprivileged container / gVisor / WASM runtime) prior to HLD.

### `EVAL-07` — Hybrid Memory Retrieval, PostgreSQL Search, RRF & Reranking Benchmark (`MOD-10`, `ADR-008`)
- **Role in Architecture**: Empirical validation of the configurable 5-stage Hybrid Retrieval Pipeline specified in [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md).
- **Why Evaluation Is Required**: BM25, Reciprocal Rank Fusion (RRF), and configurable reranking are **proposed** architectural choices—not verified defaults—and we must avoid introducing a separate search engine (e.g., Elasticsearch/Meilisearch) unless PostgreSQL-native search fails to meet quality/latency targets (`AP-09`).
- **Key Evaluation Questions**:
  1. Separately benchmark **PostgreSQL native FTS** (`tsvector`/`ts_rank_cd` — never mislabeled BM25) vs. **Genuine BM25** (`pg_search` extension) to evaluate lexical recall/ranking without a separate search engine (`AP-09`).
  2. Measure RRF (`k`, branch weights) and replaceable rerankers (`NoOp` vs. Cross-Encoder vs. API reranker) across `Recall@K`, `Precision@K`, `MRR`, `NDCG@K`, end-to-end latency, and CPU/RAM overhead (`CON-15`).
  3. Verify bitemporal interval correctness (`CURRENT` with `valid_to IS NULL AND recorded_to IS NULL AND superseded_by IS NULL`, `HISTORICAL` returning superseded 2023 facts, `RANGE` overlap, and combined system-time `recorded_from`/`recorded_to` + valid-time queries), `memory_unit_id`-only deduplication (`content_hash` never merging distinct scopes), convergent entity source-level isolation (`"Project Apollo"`), $0.0\%$ leakage (`MEM-EVAL-01`–`14`), 5-outcome internal Memory Resolution & HITL `NEEDS_CLARIFICATION` round-trip (`MEM-RES-01`–`10`), and 9-step Explicit Memory Deletion with pre-scrub `deletion_dependency_manifest`, 4-branch authoritative pre-fusion tombstone gate, terminal `COMPLETED` tombstone state, graph source recomputation, and redacted audit (`MEM-DEL-01`–`06`).
- **Exit Criteria**: Completed benchmark and lifecycle verification report across [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md) §8 (`MEM-EVAL-01`–`14`, `MEM-RES-01`–`10`, `MEM-DEL-01`–`06`) before locking final HLD/LLD retrieval configuration.

### `EVAL-08` — Autonomous Automation Platform, Non-LLM Condition Evaluation, Outbox & Loop Safety (`MOD-17`, `MOD-18`, `ADR-009`)
- **Role in Architecture**: Framework-independent, Phabric-reusable Autonomous Automation Platform (`MOD-17`) and Result & Delivery Manager (`MOD-18`) supporting generalized `(tenant_id, owner_principal, creator_principal, typed execution_target)` ownership, immutable `PermissionEnvelope` bounds, 6 trigger types, non-LLM condition monitors, complete `CREATE`–`UPDATE`–`PAUSE`–`RESUME`–`CANCEL`–`DELETE` (6-step authoritative PG ordering) lifecycle, generation-fenced outbox & `MOD-08` Gateway pre-dispatch cancellation checks, HITL `WAITING_FOR_USER` integration, and cohesive 8-control loop protection.
- **Why Evaluation Is Required**: Must validate transactional outbox dispatch/recovery/dead-letter handling, worker + `MOD-08` Gateway PostgreSQL generation/cancellation revalidation (`SKIPPED_CANCELLED` / `SKIPPED_STALE_GENERATION` / `CANCELLED_AFTER_EXTERNAL_COMMIT`), authoritative 6-step `DELETE` ordering before async cleanup, cascade cancellation when an automation is cancelled while waiting on HITL, non-LLM condition predicate expressiveness (`OQ-16`), 3-point OpenFGA + `PermissionEnvelope` expansion rejection, indirect-loop circuit-breaker thresholds (`OQ-15`), single-VM headroom (`EVAL-05`), and clean packaging for future Phabric products (`OQ-17`).
- **Key Evaluation Questions**:
  1. Benchmark atomic `automation_outbox` PostgreSQL commit + leased dispatcher -> Redis transport, idempotent consumer dedup, `DEAD_LETTERED` handling, generation/version worker + Gateway revalidation against stale queued Redis events, and stale-lease/`misfire_policy` recovery with zero live LLM wait overhead (`AUTO-EVAL-01`–`05`).
  2. Validate that deterministic `ConditionEvaluator` predicates over `MOD-08` `READ_ONLY` probes avoid unnecessary LLM invocations while respecting envelope frequency limits (`AUTO-EVAL-06`–`07`).
  3. Verify `PermissionEnvelope` breach rejection (requiring renewed approval on scope/budget/frequency expansion), `SCRIPT` target lockout prior to `EVAL-06`, cycle/`max_trigger_depth`/circuit-breaker (`PAUSED_SAFETY_TRIP`) safety, 3-point OpenFGA checks, cooperative/Gateway cancellation race honesty, 6-step `DELETE` ordering, and automation cancellation while awaiting HITL (`AUTO-EVAL-08`–`12`).
  4. Confirm clean domain package boundaries (`AutomationPlatformPort` / `orbi_automation`) reusable outside Orbi without LangGraph imports.
- **Exit Criteria**: Prototype trigger/condition evaluator benchmark + `AUTO-EVAL-01`–`12` lifecycle/cancellation/leakage test report + quota/DSL defaults (`OQ-15`–`OQ-17`).

---

## 3. Proposed Phased Delivery Roadmap (`[PROPOSED]`)

| Phase | Milestone Focus | Included Capabilities & Modules | Exit Gate |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Core Spine, Authz, Centralized HITL, Approvals & Core Automation Dispatch** | OCI Docker Compose spine (`orbi-postgres`, `orbi-redis`, `orbi-openfga`), `orbi-core` (Python 3.12+/FastAPI/LangGraph), `MOD-01` Telegram Adapter, `MOD-03` Task Manager + centralized **`HumanInteractionManager`** (`SUSPENDING_FOR_USER` two-phase checkpoint suspension -> `WAITING_FOR_USER` across all 5 interaction types + `UNCERTAIN_EXECUTION_RECONCILIATION`, 3-step `interaction_version` CAS, `AUTHZ_DENIED` non-invalidation vs. `REJECTED` vs. `INVALIDATED`, `task_lifecycle_outbox` + bounded-lease `claim_epoch` recovery & write-ahead `step_execution_receipts`, `EVAL-04` / `HITL-EVAL-01`–`26`), `MOD-04` Provider-Independent LLM Adapters + 7-step Tool Discovery, `MOD-05` Decision Service (Deterministic + LLM fallback), `MOD-06`/`MOD-07` Registries, `MOD-08` Tool Execution Gateway with `action_idempotency_key` + `PREPARED -> DISPATCHING -> CONFIRMED_SUCCESS | UNKNOWN_OUTCOME` receipts, canonical action-bound approvals (`ADR-004`) & pre-dispatch cancellation/generation/`claim_epoch` gate, `MOD-15` Audit Ledger, `MOD-18` Result & Delivery Manager (incl. Dashboard handoff links), and `MOD-17` Autonomous Automation Platform foundation (`CRON`, `INTERVAL`, `ONE_TIME`, `MANUAL`, `schedule_followup` dispatching into `MOD-03` with 3-point OpenFGA checks). | End-to-end governed task & crash-consistent HITL lifecycle (`HITL-EVAL-01`–`26`, checkpoint-first suspension, stale-`RUNNING` lease recovery, `DISPATCHING -> UNKNOWN_OUTCOME` reconciliation/no-blind-retry escalation, `0` sleeping workers) with verified OpenFGA household-ready filtering, unauthorized non-invalidation, immutable approval invalidation on argument change, Telegram/Dashboard multi-channel handoff, Gateway pre-dispatch cancel/epoch checks, and durable non-LLM schedule/follow-up dispatch into `MOD-03`. |
| **Phase 2** | **Memory, Full Autonomous Responsibilities, SaaS APIs, Dashboard & Optional Jev** | `MOD-02` Web Dashboard (stack from `OQ-01`, hosting `SECURE_INPUT_REQUEST`, quarantine curation & approval inspection), `MOD-10` Memory Manager 5-stage Hybrid Retrieval + 4-branch tombstone gate + HITL `NEEDS_CLARIFICATION` (`EVAL-03`/`EVAL-07`), `MOD-12` SaaS API Connectors (Gmail, Spotify), `MOD-17` full capabilities (`EVENT` subscriptions, LLM-free `CONDITION` monitors via `MOD-08` probes, ongoing `AgentResponsibility` work plans, non-inherited agent-to-agent delegation, 6-step `DELETE` ordering & cycle/budget guards), `MOD-18` completion callbacks, and optional Jev in `MOD-05` (`EVAL-01`). | Gmail/Spotify tasks governed by action-bound approvals & LLM-bypassing secret input; hybrid memory passes `EVAL-07` & $0\%$ leakage checks; agents create/adapt multi-schedule responsibilities and condition monitors without sleeping LLM sessions or privilege escalation. |
| **Phase 3** | **Isolated Cloud Browser (`HUMAN_TAKEOVER`) & Layered Windows Companion** | `MOD-13` Isolated Playwright Container (authenticated noVNC/WebRTC `HUMAN_TAKEOVER` + CAPTCHA handoff without bypass), `MOD-14` Local Windows Companion implementing Native API -> UIA (`agent-desktop`, `EVAL-02`) -> Controlled Vision Fallback with confidence halts, mTLS outbound tunnel, local guardrails & kill-switch, plus `MOD-17` event/condition triggers over browser/desktop outcomes. | Verified browser automation, cookie-preserving `HUMAN_TAKEOVER` handback with post-takeover probes, cross-boundary Windows automation with safe confidence stops, and action-bound HITL approvals across all interaction layers. |
| **Phase 4** | **Markdown Skill Packages & Script Sandbox** | `MOD-11` Skill Lifecycle Manager (`SKILL.md` packages in initial `MARKDOWN_ONLY` mode), `MOD-11-SANDBOX` (`EVAL-06`) isolated runner for future supporting scripts, Dashboard skill review/approval UI, semantic version pinning & rollback, plus `skill.published` event triggers in `MOD-17`. | Candidate Markdown skill packages evaluated cleanly, explicitly approved by owner, published, and invoked via `MOD-08` without privilege escalation. |
| **Phase 5** | **Home IoT, Plant/Domain Sensors & Multi-Member Household** | `MOD-16-EXT` Home Assistant / MQTT / ESP32 Bridge behind `MOD-07`/`MOD-08`/`MOD-09`, OpenFGA household member tuples activated in `MOD-01`/`MOD-02`, IoT sensor event & condition monitor feeds into `MOD-17` (e.g., autonomous plant care & physical home actions under `ADR-004` approvals), optional secondary user channels (`AP-10`). | Multi-member OpenFGA isolation verified + IoT sensors drive ongoing `AgentResponsibility` condition monitors while physical actuators remain gated by sensitivity tiers and approvals. |

---

## 4. Scope Boundary Matrix (`[PROPOSED]`)

| Capability | Phase 1 (Core) | Phase 2 (Memory, Full Auto & SaaS) | Phase 3 (Harnesses) | Phase 4 (Skills) | Phase 5 (IoT & Household) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Python/FastAPI/LangGraph Core & Task Manager (`HumanInteractionManager`) | **In Scope** | Enhanced | Enhanced | Enhanced | Enhanced |
| Centralized 5-Type HITL (`WAITING_FOR_USER` & Handoff) | **In Scope** *(Telegram + Dash Handoff)* | **`MOD-02` Secure Input UI & `MOD-10` Clarify** | **`MOD-13`/`14` `HUMAN_TAKEOVER`** | Skill Prompts | IoT Confirmations |
| Result & Delivery Manager (`MOD-18`) | **In Scope** | Full Callbacks | Enhanced | Enhanced | Household Routing |
| Automation Platform (`MOD-17`: Cron/Interval/One-time/Follow-up) | **In Scope** *(Foundation)* | **Event, Condition, 6-Step Delete & Responsibilities** | Cross-Harness | Skill Triggers | IoT Sensor Triggers |
| Household-Ready OpenFGA Schema & 3-Point Automation Authz | **In Scope** (Owner Active) | Delegation Scopes | Expanded | Skill Scopes | **Household Active** |
| Action-Bound Approval Digests & Invalidation | **In Scope** | Expanded | Cross-Boundary | Skill Approvals | IoT Approvals |
| Provider-Independent LLM & Decision Service (Rules/LLM) | **In Scope** | Optional Jev (`EVAL-01`) | Tuned | Tuned | Tuned |
| Web Dashboard (`MOD-02`, `OQ-01`) | — | **In Scope** *(Incl. Responsibilities)* | Trace/Browser Views | Skill Approval UI | Household/IoT UI |
| Memory Manager & Hybrid Retrieval Pipeline (`EVAL-03`, `EVAL-07`) | — | **In Scope** | Cross-Harness | Grounding | Context Awareness |
| External SaaS APIs (Gmail, Spotify) | — | **In Scope** | Event Feeds | Skill-Composed | — |
| Playwright Cloud Browser Container (`MOD-13`) | — | — | **In Scope** | Evaluated | — |
| Layered Windows Companion (`Native/UIA/Vision`, `EVAL-02`) | — | — | **In Scope** | Desktop Skills | — |
| Markdown Skill Packages & Script Sandbox (`EVAL-06`) | — | — | — | **In Scope** | IoT Skills |
| Home Assistant / MQTT Boundary (`MOD-16-EXT`) | Interface Only (`AP-10`) | Interface Only | Interface Only | Interface Only | **In Scope** |
| Commercial Multi-Tenant SaaS Isolation / Billing | Out of Scope | Out of Scope | Out of Scope | Out of Scope | Out of Scope |
