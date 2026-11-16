# Orbi — General Level Design (GLD): Module Catalog

- **Document ID**: `DOC-GLD-002`
- **Revision**: `1.3` *(Updated with Autonomous Automation Platform `MOD-17` & Result & Delivery Manager `MOD-18`)*
- **Phase**: `01-GLD`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-16`
- **Traceability**: Defines modules referenced in [requirements.md](file:///f:/Projects/Orbi/docs/00-product/requirements.md), [system-overview.md](file:///f:/Projects/Orbi/docs/01-gld/system-overview.md), [system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md), [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md), and [docs/04-adr/](file:///f:/Projects/Orbi/docs/04-adr/README.md).

---

## 1. Module Taxonomy & Resource Isolation Summary

| ID | Module Name | Execution Zone (`Clarification E`) | Primary Tech | Status | Governing Principles / ADRs |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`MOD-01`** | Channel Gateway & Telegram Adapter | Zone 1 (`orbi-core` OCI) | Python 3.12+ / FastAPI / Telegram | `[CONFIRMED]` | `AP-06`, `AP-10`, `ADR-001` |
| **`MOD-02`** | Admin, Monitoring, Approval & Memory Dashboard | Zone 2b (Dedicated Web UI Container) + `orbi-core` BFF | Frontend `[OPEN QUESTION OQ-01]` | `[CONFIRMED]` *(UI Tech Open)* | `AP-04`, `AP-06`, `AP-07` |
| **`MOD-03`** | Deterministic Task Manager | Zone 1 (`orbi-core` OCI) | PostgreSQL + Redis | `[CONFIRMED]` | `AP-03`, `AP-06`, `ADR-004` |
| **`MOD-04`** | Agent Orchestrator & Runtime | Zone 1 (`orbi-core` OCI) | LangGraph + Model Adapters | `[CONFIRMED]` | `AP-01`, `AP-03`, `ADR-002`, `ADR-003` |
| **`MOD-05`** | Provider-Independent Decision Service | Zone 1 (`orbi-core` OCI) + Optional External | Rules / LLM Fallback / Optional Jev | `[CONFIRMED]` *(Jev: `[EVALUATION REQUIRED]`)* | `AP-08`, `AP-09`, `ADR-003` |
| **`MOD-06`** | Central Agent Registry | Zone 1 (`orbi-core` OCI) | PostgreSQL (`orbi_app`) | `[CONFIRMED]` | `AP-02`, `AP-08`, `ADR-002` |
| **`MOD-07`** | Central Tool Registry | Zone 1 (`orbi-core` OCI) | PostgreSQL (`orbi_app`) + JSON Schema | `[CONFIRMED]` | `AP-01`, `AP-06`, `ADR-002` |
| **`MOD-08`** | Tool Execution Gateway | Zone 1 (`orbi-core` OCI) | Python Gateway Service | `[CONFIRMED]` | `AP-01`, `AP-02`, `ADR-004` |
| **`MOD-09`** | Authorization & Policy Engine | Zone 2b (`orbi-openfga` Container) | OpenFGA (Household-Ready Model) | `[CONFIRMED]` | `AP-02`, `AP-10`, `ADR-002` |
| **`MOD-10`** | Memory Manager | Zone 1 (`orbi-core`) + Zone 2b (`orbi-postgres` `orbi_memory` & Internal `CogneeAdapter` Backends) | PostgreSQL (`orbi_memory`, PG FTS/BM25* `EVAL-07`) + Hybrid Retrieval Pipeline + Replaceable `CogneeAdapter` (`EVAL-03`) | `[CONFIRMED]` *(PG FTS/BM25, RRF, Rerank: `[PROPOSED - EVAL]`)* | `AP-02`, `AP-04`, `AP-09`, `ADR-007`, `ADR-008` |
| **`MOD-11`** | Skill Lifecycle Manager (`SKILL.md`) | Zone 1 (`orbi-core`) + **`MOD-11-SANDBOX`** (Zone 2a) | Versioned Markdown Packages + Sandbox (`EVAL-06`) | `[CONFIRMED]` *(Sandbox: `EVAL-06`)* | `AP-04`, `AP-06`, `ADR-005` |
| **`MOD-12`** | External API Integration Connectors | Zone 1 (`orbi-core` OCI, Gateway-Mediated) | Gmail, Spotify, SaaS Adapters | `[CONFIRMED]` | `AP-01`, `AP-10` |
| **`MOD-13`** | Cloud Browser Harness | Zone 2a (`orbi-browser` Container) | Playwright + Chromium | `[CONFIRMED]` | `AP-01`, `AP-05`, `ADR-001` |
| **`MOD-14`** | Windows Companion | Zone 3 (Local Windows PC, Off-Cloud) | Native API -> `agent-desktop` UIA (`EVAL-02`) -> Vision | `[CONFIRMED]` *(UIA/Vision: `EVAL-02`)* | `AP-01`, `AP-05`, `ADR-006` |
| **`MOD-15`** | Audit, Telemetry & Provenance Service | Zone 1 (`orbi-core` OCI) | PostgreSQL Append-Only Ledger | `[CONFIRMED]` | `AP-07` |
| **`MOD-16-EXT`** | Home IoT Bridge *(Future Extension)* | Zone 2/3 Bridge Boundary (`Phase 5`) | Home Assistant / MQTT Boundary | `[CONFIRMED]` (Boundary Only) | `AP-10`, `ADR-006` |
| **`MOD-17`** | Autonomous Automation Platform *(Schedules, Events, Conditions & Responsibilities)* | Zone 1 (`orbi-core` OCI, Phabric-Reusable Domain Port) | PostgreSQL (`orbi_automation`) + Redis + Deterministic Evaluator | `[CONFIRMED]` *(Quotas/DSL/Pkg: `EVAL-08`)* | `AP-01`, `AP-02`, `AP-03`, `AP-08`, `ADR-009` |
| **`MOD-18`** | Result & Delivery Manager | Zone 1 (`orbi-core` OCI) | Python Delivery & Callback Service | `[CONFIRMED]` | `AP-07`, `AP-08`, `ADR-009` |

---

## 2. Detailed Module Specifications (`MOD-01` – `MOD-18`)

### `MOD-01`: Channel Gateway & Telegram Adapter
- **Purpose**: Normalizes inbound interactions from Telegram into channel-agnostic platform requests, maps Telegram sender identities into household-ready user principals (`ADR-002`, `ASM-07`), and renders task notifications and action-bound approval prompts.
- **Status**: `[CONFIRMED]` (Zone 1: Python 3.12+ / FastAPI inside `orbi-core`).
- **Owned State**: Channel identity bindings (`telegram_user_id`, `telegram_chat_id` <-> `user:<id>`, `household:<id>`, `active_task_id`) in `orbi_app`.
- **Public Interfaces**:
  - *Inbound*: Telegram Bot webhooks / updates (`text`, attachments, inline approval callbacks carrying `approval_id` and `payload_digest`), formatted notifications & interactive prompts pushed by `MOD-18` (from `MOD-03`/`MOD-17`).
  - *Outbound*: Submits normalized `TaskRequest` and `ApprovalDecision` commands to `MOD-03`; consumes delivery events from `MOD-18`.
- **Allowed Dependencies**: `MOD-03`, `MOD-09` (channel/user verification), `MOD-15`, `MOD-18`.
- **Forbidden Dependencies**: Must never invoke `MOD-04` agents directly, execute `MOD-08` tools, or read raw Memory Manager/SaaS secrets.
- **Independent Testability (`AP-08`)**: Tested via mocked Telegram webhook payloads against fake `MOD-03`/`MOD-09`/`MOD-18` interfaces.

### `MOD-02`: Admin, Monitoring, Approval & Memory Dashboard
- **Purpose**: Operator control plane providing visibility into tasks, **autonomous automations & ongoing `AgentResponsibility` work plans**, schedule version history, condition monitors, delegation lineage, audit trails, OpenFGA household/agent policies, pending action-bound approvals (full canonical arguments & target inspection), `MOD-10: Memory Manager` memory, provenance & quarantine curation, and Markdown skill package review/publication.
- **Status**: `[CONFIRMED]` *(Frontend framework open for HLD under `OQ-01`; BFF endpoints in FastAPI `orbi-core`)*.
- **Owned State**: UI session tokens only (zero duplicate copies of tasks, automations, memories, or skills).
- **Public Interfaces**:
  - *Inbound*: Authenticated HTTPS requests from authorized household/owner browsers + live updates from `MOD-18`.
  - *Outbound*: Calls BFF REST/WebSocket endpoints backed by `MOD-03`, `MOD-06`, `MOD-07`, `MOD-09`, `MOD-10`, `MOD-11`, `MOD-15`, `MOD-17`, `MOD-18`.
- **Allowed Dependencies**: Read/governance APIs of `MOD-03`, `MOD-06`, `MOD-07`, `MOD-09`, `MOD-10`, `MOD-11`, `MOD-15`, `MOD-17`, `MOD-18`.
- **Forbidden Dependencies**: Must never bypass `MOD-03`/`MOD-17` to mutate tasks/automations directly or call execution harnesses (`MOD-12`–`MOD-14`) directly.
- **Independent Testability (`AP-08`)**: Frontend tested against OpenAPI contracts; FastAPI BFF routes tested via `TestClient` integration tests.

### `MOD-03`: Deterministic Task Manager
- **Purpose**: Authoritative orchestrator of task lifecycle, durable Redis queuing, configurable execution budgets (steps, wall-clock TTL, LLM spend/token caps), retries, and **cryptographically action-bound approval pause/resume** (`ADR-004`). Serves as the single task execution engine for both user-initiated requests and automation-triggered runs from `MOD-17`.
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core`).
- **Owned State**: Authoritative `tasks`, `task_steps`, `task_budgets`, and `approval_requests` (with `payload_digest`, `policy_version`, `expires_at`) in PostgreSQL (`orbi_app`); Redis queues and distributed locks.
- **Public Interfaces**:
  - *Inbound*: `create_task()`, `dispatch_automation_task()` (from `MOD-17` with `automation_id`, `execution_id`, `responsibility_id`, scoped grant), `cancel_task()`, `submit_approval_decision()`, `suspend_for_approval()` (from `MOD-08`), `report_turn_outcome()` (from `MOD-04`).
  - *Outbound*: Dispatches bounded cognitive turns to `MOD-04`, pre-triage queries to `MOD-05`, completed/failed task outcomes to `MOD-18` (which notifies `MOD-01`/`MOD-02` and triggers `MOD-17` callbacks), and lifecycle events to `MOD-15`.
- **Allowed Dependencies**: PostgreSQL (`orbi_app`), Redis, `MOD-04`, `MOD-05`, `MOD-09` (approver & automation execution verification), `MOD-15`, `MOD-18`.
- **Forbidden Dependencies**: Must never embed LLM reasoning prompts, LangGraph checkpoint internals, own long-term cron/event/condition trigger catalogs (`MOD-17`), or embed execution harness SDKs (`MOD-12`–`MOD-14`).
- **Independent Testability (`AP-08`)**: Deterministic FSM transition, budget exhaustion, expiration TTL, and approval-digest mismatch tests with mocked `MOD-04`/`MOD-08`/`MOD-17`/`MOD-18`.

### `MOD-04`: Agent Orchestrator & Runtime (LangGraph + Model Adapters)
- **Purpose**: Executes System-2 cognitive workflows via a **Manager/Planner Agent** and domain **Specialized Agents** inside LangGraph (`ADR-001`). Enforces **Provider-Independent Model Adapters** (`ADR-003`), executes the mandatory **Agent & Tool Discovery Sequence** (`Clarification A`, `ADR-002`), and empowers authorized agents to autonomously reason over ongoing responsibilities and arrange or adapt future/recurring/event/condition work via `automation.*` `ToolCallIntent` calls (`ADR-009`).
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core`).
- **Owned State**: Ephemeral turn-scoped reasoning scratchpad only; zero ownership of macro-task lifecycle (`AP-03`) or persistent automation schedules (`ADR-009`). Must never hold live LLM sessions sleeping to wait for future times or events.
- **Public Interfaces**:
  - *Inbound*: `execute_cognitive_turn(task_context, turn_budget, user_principal, household_context, scoped_responsibility_ctx)` from `MOD-03`.
  - *Outbound*: Queries `MOD-05` (Decision Service), `MOD-06` (Agent Registry), `MOD-07` (Tool Registry), `MOD-09` (OpenFGA Stage-1 discovery filter), `MOD-10` (Memory Manager), `MOD-11` (published skill instruction loader), and submits all `ToolCallIntent` requests (including `automation.*` calls) strictly to `MOD-08`.
- **Allowed Dependencies**: `MOD-05`, `MOD-06`, `MOD-07`, `MOD-08`, `MOD-09` (read-only discovery filter), `MOD-10`, `MOD-11` (published skill instruction loader), `MOD-15`, configured `ModelAdapter` providers.
- **Forbidden Dependencies**: **Strictly prohibited** from inventing tool identifiers, granting self-permissions, mutating `MOD-17` automations directly bypassing `MOD-08`, inheriting creator permissions on delegated automations, importing `MOD-12` SaaS SDKs, importing `MOD-13` Playwright, connecting to `MOD-14`, executing skill scripts directly, or reading credential vaults (`AP-01`, `AP-03`).
- **Independent Testability (`AP-08`)**: Tested deterministically using stubbed `ModelAdapter` responses, mocked OpenFGA discovery filters, and fake `MOD-08` Gateway results.

### `MOD-05`: Provider-Independent Decision Service
- **Purpose**: Structured System-1 decision abstraction (`noul`, `choice`, `score`) supporting intent triage, agent routing, risk pre-scoring, and loop termination (`Clarification B`, `ADR-003`). Operates cleanly **without** Jev via deterministic rule evaluators and LLM-based fallback providers; **Jev by TypeSafe** is integrated strictly as an **optional provider** pending `EVAL-01`.
- **Status**: `[CONFIRMED]` Architecture / `[EVALUATION REQUIRED]` Jev Provider (`EVAL-01`).
- **Owned State**: Provider priority chains, per-provider timeout budgets, and per-provider confidence threshold profiles (`CON-12`).
- **Public Interfaces**:
  - *Inbound*: `decide_noul(ctx, timeout_ms)`, `decide_choice(ctx, options, timeout_ms)`, `decide_score(ctx, rubric, timeout_ms)` called by `MOD-03`, `MOD-04`, or `MOD-08` (advisory risk escalation only).
  - *Outbound*: Returns typed `DecisionResult(value, confidence, provider_used, fallback_triggered, latency_ms)` and logs every call/fallback to `MOD-15`.
- **Fallback & Timeout Contract (`Clarification B`)**:
  1. Attempt configured primary provider (e.g., `DeterministicRuleProvider` for exact heuristics, or optional `JevDecisionProvider` if enabled) under a strict hard timeout (`timeout_ms`).
  2. On timeout, HTTP error, circuit-breaker open, or sub-threshold confidence, automatically fall back to configured secondary provider (`DeterministicRuleProvider` or `LLMDecisionProvider` via `MOD-04` `ModelAdapter`).
  3. On total decision failure, return deterministic safe default (e.g., route to Manager LLM or escalate risk tier—never lower risk or skip authorization).
- **Forbidden Dependencies**: Cannot authorize access (`AP-02` belongs exclusively to `MOD-09`), mutate tasks, or invoke tools.
- **Independent Testability (`AP-08`)**: Contract test suite across `DeterministicRuleProvider`, `LLMDecisionProvider`, and optional `JevDecisionProvider` simulating timeouts, network faults, and low-confidence fallbacks.

### `MOD-06`: Central Agent Registry
- **Purpose**: Declarative catalog of all agent personas (`ManagerAgent`, `EmailAgent`, `MediaAgent`, `BrowserAgent`, `DesktopAgent`, `SkillAuthorAgent`), defining role metadata, `ModelAdapter` profile/budget policy, configured tool/skill allowlists, and OpenFGA principal IDs (`agent:<id>`).
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core` / `orbi_app`).
- **Public Interfaces**: `select_agent_candidates()`, `get_agent_definition(agent_id)`, `register_agent()`.
- **Allowed Dependencies**: PostgreSQL (`orbi_app`), `MOD-15`.
- **Forbidden Dependencies**: Never executes LangGraph loops or overrides OpenFGA policies.
- **Independent Testability (`AP-08`)**: CRUD and schema validation tests against isolated test database.

### `MOD-07`: Central Tool Registry
- **Purpose**: Authoritative metadata catalog of every callable tool (including the `automation.*` management tool family), native/UIA/vision Windows capability, and published Markdown skill entry: versioned JSON Input/Output schemas, target isolation zone (`ZONE_1_API`, `ZONE_1_AUTOMATION`, `ZONE_2_BROWSER`, `ZONE_2_SANDBOX`, `ZONE_3_WINDOWS`, `FUTURE_IOT`), semantic vs. volatile argument metadata (`CON-10`), idempotency flags, and static **Sensitivity Tier** (`READ_ONLY`, `LOW_RISK_WRITE`, `SENSITIVE_WRITE`, `IRREVERSIBLE`).
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core` / `orbi_app`).
- **Public Interfaces**: `get_tool_definition(tool_id, version)`, `get_tools_for_agent_config(agent_id)`, `register_tool()`.
- **Allowed Dependencies**: PostgreSQL (`orbi_app`), `MOD-15`.
- **Forbidden Dependencies**: Holds zero runtime credentials and executes zero tool logic.
- **Independent Testability (`AP-08`)**: Schema compatibility, semantic-field tagging, and risk-tier integrity unit tests.

### `MOD-08`: Tool Execution Gateway
- **Purpose**: **The non-bypassable execution chokepoint (`AP-01`)**. Receives `ToolCallIntent` from `MOD-04` (or `MOD-11-SANDBOX` script sub-intents) and `READ_ONLY` `ProbeIntent` calls from `MOD-17`'s non-LLM `ConditionEvaluator`, validates tool existence and schemas against `MOD-07`, executes Stage-2 OpenFGA authorization (`MOD-09`), verifies **cryptographically action-bound approvals (`ADR-004`, `Clarification C`)**, injects credentials, dispatches to execution zones (`MOD-12`, `MOD-13`, `MOD-11-SANDBOX`, `MOD-14`, `MOD-17`), sanitizes results, and logs to `MOD-15`.
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core`).
- **Owned State**: Idempotency keys and active mTLS relay session routing for `MOD-14`.
- **Public Interfaces**:
  - *Inbound*: `execute_tool_intent(ToolCallIntent)`, `execute_readonly_probe(ProbeIntent)` (from `MOD-17` `ConditionEvaluator`), `resume_approved_intent(approval_id, action_digest)` (from `MOD-03`).
  - *Outbound*: `MOD-09.check()`, `MOD-03.suspend_for_approval()`, harness dispatch (`MOD-12`, `MOD-13`, `MOD-11-SANDBOX`, `MOD-14`), `MOD-17` (`automation.*` adapter), `MOD-15.record_audit()`.
- **Allowed Dependencies**: `MOD-03`, `MOD-05` (advisory risk escalation only), `MOD-07`, `MOD-09`, `MOD-11-SANDBOX`, `MOD-12`, `MOD-13`, `MOD-14`, `MOD-15`, `MOD-17`, Secret Vault.
- **Forbidden Dependencies**: Never asks an LLM whether to waive OpenFGA or bypass an expired/mismatched approval, and never permits condition probes to execute side-effecting write tools.
- **Independent Testability (`AP-08`)**: Contract tests proving fail-closed denial on OpenFGA errors, rejection on invented tool IDs, and automatic invalidation when arguments/target/policy_version differ from approved digest.

### `MOD-09`: Authorization & Policy Engine (OpenFGA)
- **Purpose**: Externalized Relationship-Based Access Control (ReBAC) engine (`AP-02`) running as an isolated container (`orbi-openfga`), modeling **tenant-extensible ownership (`ADR-002`: `personal`, `household`, and extensible `organization` boundary)** across `tenant`, `user`, `agent`, `tool`, `skill`, `instance`, `resource`, `environment`, `memory_unit`, **`responsibility`**, **`automation`**, and **`permission_envelope`** entities (`ADR-009`). Serves Stage-1 Tool Discovery filtering, Stage-2 Gateway Execution checks, independent source-level checks for graph entities/edges, and **3-Point Automation Authorization** (Creation/Delegation, Modification, and Execution Dispatch — strictly blocking creator permission inheritance).
- **Status**: `[CONFIRMED]` (Zone 2b: Dedicated OpenFGA container).
- **Owned State**: OpenFGA authorization model (with tracked `policy_version` referenced by `ADR-004`) and relation tuples in isolated `openfga` PostgreSQL database.
- **Public Interfaces**: `filter_authorized_tools(tenant, actor, agent, instance, candidate_tools)`, `check(subject, relation, object)`, `check_automation_action(phase, actor, execution_target, envelope, automation_scope)`, tuple write/read APIs (admin-gated via `MOD-02`/`MOD-11`).
- **Independent Testability (`AP-08`)**: Standalone `.fga.yaml` model assertion suites covering single-owner, multi-member household/tenant isolation, source-level convergent graph entity authorization (`"Project Apollo"`), cross-agent delegation without privilege inheritance, envelope expansion rejection, and policy version invalidation.

### `MOD-10`: Memory Manager
- **Purpose**: Canonical memory domain owner (`ADR-007`, `ADR-008`, [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md))—explicitly **not** Cognee itself or a thin wrapper over Cognee's default retrieval API. Owns end-to-end **memory lifecycle** (ingestion, belief-preserving correction, quarantine/curation via `MOD-02`, and cascading deletion via transactional `memory_sync_outbox`), the **5-stage Hybrid Retrieval Pipeline**, canonical `MemoryUnit` identity, **provenance & trust quarantine** (`TrustTier`), **true bitemporal validity** (**valid time** `valid_from`/`valid_to` + **system time** `recorded_from`/`recorded_to` + `superseded_by`), and **mandatory per-branch & per-source pre-fusion OpenFGA authorization enforcement**. **Cognee** is encapsulated strictly as an internal, replaceable adapter (**`CogneeAdapter`**) rather than defining `MOD-10` itself (`EVAL-03`, `EVAL-07`).
- **Status**: `[CONFIRMED]` Memory Lifecycle, Pipeline Orchestration, Provenance, Bitemporal Semantics & Per-Branch Authz / `[PROPOSED - EVALUATION REQUIRED]` PG FTS vs. Genuine BM25, RRF, Reranking & `CogneeAdapter`/PG Backends (`EVAL-03`, `EVAL-07`).
- **Owned Internal Sub-Components (`orbi-core` Zone 1)**:
  1. **`QueryUnderstandingEngine` (`QueryUnderstandingPort`)**: Provider-independent 3-tier analyzer (Tier 1 deterministic lexical/date parser `< 5 ms`, Tier 2 optional `MOD-05` `choice`/`noul`, Tier 3 conditional LLM fallback) extracting `intent`, `entities`, `keywords`, bitemporal `temporal_constraint` (`CURRENT | HISTORICAL | RANGE` + optional `as_of_recorded_ts`), `use_graph_traversal`, and `activated_branches` without mandatory LLM calls.
  2. **`BranchOrchestrator` & 4 Retrieval Adapters (`RetrievalBranchPort`)**: Runs activated branches concurrently with per-branch `timeout_ms`: `VectorBranch` (via replaceable `CogneeAdapter`), `BM25Branch` (`[PROPOSED]`, configurable `LexicalBackendPort` separately evaluating **PostgreSQL FTS** [`tsvector`, `tsquery`, `ts_rank`/`ts_rank_cd` — never labeled BM25] vs. **Genuine BM25** [`pg_search`] in `EVAL-07`), `GraphBranch` (bounded 1–2 hop traversal via replaceable `CogneeAdapter` preserving `source_memory_unit_ids[]`), and selective `TemporalBranch` (PostgreSQL `orbi_memory` interval SQL handling `CURRENT`, `HISTORICAL` without `superseded_by IS NULL`, and `RANGE` overlap).
  3. **`BranchAuthzFilter`**: Enforces two-layer OpenFGA (`MOD-09`) authorization inside **every** branch (`private_user`, `shared_tenant`, `custom_acl`) *before* candidates exit to Fusion: checks every `MemoryUnit` and enforces the **7-rule Source-Level Graph Authorization Invariant** (independent checks on every contributing source `MemoryUnit`, zero permission unioning across `"Project Apollo"`-style shared entities, fail-closed on missing provenance or `MOD-09` error, `CON-13`, `FR-MS-12`).
  4. **`CandidateFusionEngine` (`CandidateFusionPort`)**: Normalizes/fuses incompatible branch scales (initial proposal: weighted Reciprocal Rank Fusion / `RRF`, $k = 60$, `[PROPOSED - EVAL-07]`) using **`memory_unit_id` as the SOLE authoritative deduplication/merge key** (`content_hash` is never used to merge records), preserving multi-branch provenance/ranks/scores and capping at `candidate_limit`.
  5. **`RerankingEngine` (`RerankerPort`)**: Replaceable reranker (`NoOpReranker`, `LocalCrossEncoderReranker`, `APIReranker`, `ConditionalLLMReranker` disabled by default) re-scoring fused candidates to `final_context_limit` with selective bypass rules and automatic RRF fallback on timeout (`FR-MS-11`).
  6. **`PostgresMemoryCatalog`, `MemorySyncOutboxDispatcher` & Internal Replaceable `CogneeAdapter`**: `orbi_memory` owns canonical `MemoryUnit` IDs, `tenant_id`/`owner_principal` ACL scopes, `TrustTier`, bitemporal intervals (`CON-16`), and lexical indexes; atomic **`memory_sync_outbox`** events publish via leased worker -> Redis -> idempotent sync consumers with bounded retries and `DEAD_LETTERED` handling (`CON-14`).
- **Owned State**: Canonical `MemoryUnit` catalog, provenance/quarantine state, bitemporal validity & belief records, lexical indexes, `memory_sync_outbox`, and `memory_sync_processed_events` in PostgreSQL `orbi_memory` (distinct from `orbi_app` transactional tables per `AP-04`), plus internal replaceable `CogneeAdapter` vector/graph stores (`EVAL-03`).
- **Public Interfaces**: `retrieve_hybrid_memory(MemoryQueryRequest)`, `propose_memory_commit(content, provenance, valid_interval)`, `record_bitemporal_correction()`, `curate_or_promote_quarantine()`, `delete_memory_cascade()` (via `MOD-02` atomic delete + `memory_sync_outbox` + `BlobStoragePort` + `MOD-09`), and `run_retrieval_eval_suite()` (`EVAL-07`).
- **Allowed Dependencies**: `MOD-05` (Tier-2 query classification), `MOD-09` (mandatory per-branch/per-source OpenFGA checks & ACL tuple cleanup), `MOD-15` (retrieval, fallback & mutation audit), PostgreSQL `orbi_memory`, Redis (transport only), `BlobStoragePort`, and internal replaceable `CogneeAdapter`.
- **Forbidden Dependencies**: Never exposes `CogneeAdapter` directly to `MOD-04`, `MOD-02`, or external callers; never merges fusion candidates by `content_hash`; never applies universal `superseded_by IS NULL` to historical/range queries; never unions graph source permissions; never leaks unauthorized entities/edges/summaries into Fusion, Reranking, or `MOD-04`; never allows `untrusted_external` facts to silently close `recorded_to` or set `superseded_by` on `user_explicit` facts (`CON-16`); never stores task state, OpenFGA tuples, OAuth secrets, or executable skill code (`AP-04`).
- **Independent Testability (`AP-08`)**: Stage-by-stage contract tests (Bitemporal Historical + Belief-Correction Tests -> PG FTS vs. Genuine BM25 Benchmark -> OpenFGA Multi-Hop & Convergent Entity `"Project Apollo"` Leakage Tests -> `memory_unit_id`-Only Deduplication -> Transactional Outbox Recovery -> Graceful `CogneeAdapter` Outage Fallback) plus `EVAL-07` (`MEM-EVAL-01`–`14`).

### `MOD-11`: Skill Lifecycle Manager (`SKILL.md`) & Script Sandbox (`MOD-11-SANDBOX`)
- **Purpose**: Manages reusable capabilities packaged as **versioned Markdown skill packages (`SKILL.md` + optional schemas, examples, tests, and supporting scripts)** (`ADR-005`, `Clarification D`). Enforces clear separation between declarative instructions (loaded into agent context) and supporting scripts (executed solely inside **`MOD-11-SANDBOX`**, Zone 2a).
- **Status**: `[CONFIRMED]` *(Sandbox runtime selected via `EVAL-06`)*.
- **Owned State**: `skill_packages`, `skill_versions`, and `skill_eval_runs` tables in `orbi_app`; immutable versioned package artifacts.
- **Public Interfaces**:
  - *Control Plane (`MOD-11` in Zone 1)*: `create_skill_draft()`, `run_evaluation_suite()`, `submit_for_approval()`, `approve_and_publish()` (via `MOD-02`), `load_skill_instructions()`.
  - *Execution Sandbox (`MOD-11-SANDBOX` in Zone 2a)*: `run_supporting_script(skill_id, version, script_name, validated_input)` invoked **only** by `MOD-08` after OpenFGA + approval verification.
- **Allowed Dependencies**: `MOD-07` (registration upon publication), `MOD-08` (sandboxed test/execution boundary), `MOD-09`, `MOD-15`.
- **Forbidden Dependencies**: Skill Markdown files and scripts **cannot** grant permissions, read secrets, access host filesystems, or bypass `MOD-08` (`AP-01`, `AP-02`). Unapproved skills cannot execute in production tasks.
- **Independent Testability (`AP-08`)**: Package parser/validator tests, deterministic golden fixture evaluation tests, and sandbox escape/network-isolation security tests (`EVAL-06`).

### `MOD-12`: External API Integration Connectors
- **Purpose**: Stateless typed adapters inside `orbi-core` (Zone 1) for external SaaS services (initially Gmail, Spotify; extensible to Calendar, etc.), invoked strictly by `MOD-08` with runtime-injected secrets.
- **Status**: `[CONFIRMED]`
- **Independent Testability (`AP-08`)**: HTTP record/replay contract tests per connector without live credentials.

### `MOD-13`: Cloud Browser Harness (Playwright)
- **Purpose**: Dedicated container (`orbi-browser`, Zone 2a) running Playwright + Chromium for web workflows lacking APIs. Enforces domain allowlists, SSRF protection (blocks cloud metadata IPs), resource limits, and Gateway-only mediation.
- **Status**: `[CONFIRMED]`
- **Independent Testability (`AP-08`)**: Tested in CI against local HTML/JS Playwright fixture servers.

### `MOD-14`: Windows Companion (Layered Executor)
- **Purpose**: Local Windows service (Zone 3, Off-Cloud) initiating an outbound mTLS tunnel (`ASM-05`) to `MOD-08` and executing desktop automation via a strict **3-tier precedence hierarchy (`ADR-006`)**:
  1. **Tier 1 — Native OS / App APIs** (preferred: file/COM/app-native APIs under allowlist).
  2. **Tier 2 — UI Automation (UIA)** via Vercel Labs `agent-desktop` (`EVAL-02`) producing structured JSON element trees and `ref` actions.
  3. **Tier 3 — Controlled Vision Fallback** (used only when Tier 1 & Tier 2 cannot locate elements; window-cropped, locally redacted, confidence-gated).
- **Safety Invariants**: Immediately halts (`STOP_INSUFFICIENT_CONFIDENCE`) when UIA or vision confidence is below threshold; enforces local app allow/deny lists and instant local kill-switch; requires cloud OpenFGA + action-bound approvals (`ADR-004`) across **all three interaction tiers**.
- **Status**: `[CONFIRMED]` Architecture / `[EVALUATION REQUIRED]` UIA & Vision thresholds (`EVAL-02`).
- **Independent Testability (`AP-08`)**: Local unit/integration suites mocking each tier independently plus confidence-abort boundary tests.

### `MOD-15`: Audit, Telemetry & Provenance Service
- **Purpose**: Append-only PostgreSQL ledger recording correlated events (`correlation_id`, `task_id`) across discovery filtering, Decision Service provider/fallback traces, OpenFGA checks, action-bound approval digests, tool invocations, memory mutations, and skill publications (`AP-07`).
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core` / `orbi_app`).
- **Independent Testability (`AP-08`)**: Append-only constraint verification and secret/PII redaction unit tests.

### `MOD-16-EXT`: Future Home IoT Bridge *(Boundary Reserved for Phase 5)*
- **Purpose**: Preserves architectural integration slots in `MOD-07` (Tool Registry harness type `FUTURE_IOT`), `MOD-08` (Gateway adapter interface), `MOD-09` (OpenFGA `iot_device` / `household` ownership tuples), and `MOD-17` (event/condition sensor feeds) for future **Home Assistant / MQTT** integrations (`AP-10`, `ADR-006`) without committing prematurely to a single protocol or implementing IoT now.
- **Status**: `[CONFIRMED]` (Interface boundary preserved; implementation deferred to Phase 5).

### `MOD-17`: Autonomous Automation Platform *(Schedules, Events, Condition Monitors & Ongoing Responsibilities)*
- **Purpose**: Framework-independent automation orchestration platform (`ADR-009`) built behind a Phabric-reusable domain boundary (`AutomationPlatformPort` / `phabric_automation`, decoupled from LangGraph so future Phabric products can adopt it seamlessly while Orbi agents are its first consumers). Enables authorized agents (and users) to establish **ongoing `AgentResponsibility` work plans**, register **six unified trigger types** (`CRON`, `INTERVAL`, `ONE_TIME`, `EVENT`, `CONDITION`, `MANUAL`), run **LLM-free deterministic condition evaluations** via `MOD-08` `READ_ONLY` probes, enforce immutable **`PermissionEnvelope`** bounds, propagate **cohesive 8-control loop protection**, publish durably via **transactional `automation_outbox`**, and dispatch due work strictly as normal `MOD-03` tasks.
- **Status**: `[CONFIRMED]` Architecture / `[EVALUATION REQUIRED]` Quotas, Condition DSL & Phabric Packaging (`EVAL-08`, `OQ-15`–`OQ-17`).
- **Owned State**: Durable PostgreSQL `orbi_automation` tables (`agent_responsibilities`, `permission_envelopes`, `automation_definitions`, `automation_schedule_versions`, `automation_event_subscriptions`, `automation_condition_monitors`, `automation_executions`, `automation_outbox`, `automation_processed_events`); Redis transport streams/timers (`Redis` is transport only). Separates `tenant_id` (`PERSONAL | HOUSEHOLD | ORGANIZATION_FUTURE`), `owner_principal`, `creator_principal`, typed **`execution_target`** (`AGENT` initially enabled; `WORKFLOW` only when explicitly registered; `SCRIPT` disabled until `EVAL-06`), `trigger_definition`, `task_or_workflow_definition`, immutable `permission_envelope`, `execution_budgets`, and `approval_policy`.
- **Public Interfaces**:
  - *Runtime & Dashboard APIs*: `create_automation()`, `schedule()`, `subscribe_to_event()`, `create_condition_monitor()`, `schedule_followup()`, `update_automation()` (envelope-checked), `pause()`, `resume()`, `cancel()`, `get_execution_history()`, `manage_responsibility()` (agent calls mediated via `MOD-08`; admin governance via `MOD-02`).
  - *Internal Dispatch & Callback Ports*: Dispatches `dispatch_automation_task()` to `MOD-03`; queries `MOD-08.execute_readonly_probe()` for condition monitors; consumes outbox-backed completion callbacks from `MOD-18`.
- **Allowed Dependencies**: PostgreSQL (`orbi_automation`), Redis (transport only), `MOD-03` (task creation), `MOD-06` (execution target validation), `MOD-08` (read-only condition probes), `MOD-09` (3-point OpenFGA authorization & envelope checks), `MOD-15` (audit ledger).
- **Forbidden Dependencies**: **Strictly prohibited** from running its own task execution engine (`MOD-03`), holding sleeping LLM sessions, letting `execution_target` inherit creator permissions or expand its own `PermissionEnvelope`, enabling `SCRIPT` targets before `EVAL-06`, executing writes directly bypassing `MOD-08`, or embedding LangGraph runtime types inside its domain package (`AP-01`, `AP-03`, `AP-08`, `AP-09`).
- **Independent Testability (`AP-08`)**: Pure domain unit tests for trigger firing math, condition predicate evaluation, cohesive loop circuit breaker (`PAUSED_SAFETY_TRIP`), `PermissionEnvelope` expansion rejection, transactional outbox recovery/DLQ, misfire reconciliation, and 3-point authz contract tests with mocked `MOD-03`/`MOD-08`/`MOD-09`.

### `MOD-18`: Result & Delivery Manager
- **Purpose**: Normalizes and routes completed task outcomes (`ADR-009`), bridging `MOD-03` task completion/failure states with user-facing notification channels (`MOD-01` Telegram, `MOD-02` Dashboard SSE/inbox) and internal automation callbacks to `MOD-17` (enabling `schedule_followup` registration, `task.completed` event matching, and responsibility state progression).
- **Status**: `[CONFIRMED]` (Zone 1: `orbi-core` OCI).
- **Owned State**: Ephemeral delivery receipts, deduplication keys (`task_id`, `channel_id`), and notification formatting templates.
- **Public Interfaces**:
  - *Inbound*: `deliver_task_result(task_id, outcome, delivery_policy, correlation_id)` from `MOD-03`.
  - *Outbound*: Dispatches redacted messages/prompts to `MOD-01`/`MOD-02`; posts completion events/callbacks to `MOD-17`; logs delivery outcomes to `MOD-15`.
- **Allowed Dependencies**: `MOD-01`, `MOD-02`, `MOD-09` (recipient authorization verify), `MOD-15`, `MOD-17`, Redis.
- **Forbidden Dependencies**: Must never execute tools (`MOD-08`), alter task FSM states (`MOD-03`), or expose unredacted secrets (`FR-AU-02`).
- **Independent Testability (`AP-08`)**: Delivery routing, idempotent retry, secret redaction, and `MOD-17` callback emission tests with fake channel adapters.
