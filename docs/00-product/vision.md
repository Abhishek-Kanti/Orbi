# Orbi — Product Vision & Architectural Charter

- **Document ID**: `DOC-PRD-001`
- **Revision**: `1.3` *(Updated with Autonomous Agent-Driven Automation & Ongoing Responsibilities)*
- **Phase**: `00-Product`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-16`

---

## Epistemic Status Legend

Throughout the Orbi engineering documentation suite, every decision, requirement, and component is tagged with one of the following markers to preserve strict distinction between established baseline facts and design proposals:

| Marker | Meaning |
| :--- | :--- |
| `[CONFIRMED]` | Explicitly decided by the project owner in the architectural charter or GLD review decisions. |
| `[PROPOSED]` | Architect-recommended design decision, boundary, or pattern proposed to satisfy confirmed principles. |
| `[EVALUATION REQUIRED]` | Technology choice, sizing assumption, or integration explicitly flagged for spike validation/benchmarking before locking into HLD/LLD. |
| `[ASSUMPTION]` (`ASM-xx`) | Explicit premise adopted where requirements are currently silent; must be validated during review. |
| `[CONFLICT / TENSION]` (`CON-xx`) | Identified architectural tension between stack components or principles requiring explicit mitigation. |
| `[OPEN QUESTION]` (`OQ-xx`) | Unresolved product or engineering decision required before proceeding to HLD/LLD. |

---

## 1. Executive Summary & Vision

`[CONFIRMED]` **Orbi** is a personal AI agent platform designed to connect autonomous reasoning agents, a reusable Autonomous Automation Platform (`MOD-17`, shared across Orbi and future Phabric products), provider-independent structured decision capabilities, deterministic task orchestration, long-term knowledge memory, cloud browsers, external SaaS applications, local desktop environments, and—in future phases—home IoT devices under a single, governed operational spine.

`[CONFIRMED]` The platform adopts a **Governed Modular Monolith with Isolated Execution Harnesses** built on **Python 3.12+, FastAPI, and LangGraph** (`orbi-core`), deployed initially via **Docker Compose on a single Oracle Cloud Infrastructure (OCI) VM** (with resource sizing subject to benchmarking, `[EVALUATION REQUIRED]`).

`[PROPOSED]` **Core Product Thesis**: Most modern AI agent stacks fail in personal automation because they either restrict automation to manual user cron rules, wastefully keep live LLM sessions waiting on future events, or grant probabilistic LLMs direct access to credentials, shells, and external APIs. Orbi separates **cognitive proposal & autonomous planning** from **deterministic governance & durable automation**:

1. **Intelligence proposes and arranges ongoing responsibilities; deterministic systems dispose and time execution.** Reasoning agents (LangGraph via provider-independent LLM adapters) and a provider-independent Decision Service (with deterministic rules, LLM fallbacks, and optional Jev by TypeSafe `[EVALUATION REQUIRED]`) analyze intent, retrieve knowledge, formulate tool-call intents, and independently create or adapt future schedules, event subscriptions, and condition monitors via the Automation Platform (`MOD-17`, `ADR-009`).
2. **Governance enforces outside the model.** The Automation Platform persists triggers and runs LLM-free condition probes via the Gateway; the deterministic Task Manager owns task execution; OpenFGA enforces externalized authorization (including 3-point checks and zero permission inheritance on agent-to-agent automation delegation); the Tool Execution Gateway enforces action-bound approvals, secret injection, and audit logging; and the Result & Delivery Manager (`MOD-18`) delivers outcomes and completion callbacks.
3. **State domains and execution environments are strictly partitioned.** Transactional task/application state (PostgreSQL), `MOD-10: Memory Manager` (owning memory lifecycle, hybrid retrieval orchestration, provenance, bi-temporal validity, and pre-fusion authz, with Cognee abstracted strictly as an internal replaceable `CogneeAdapter` and storage backends `[EVALUATION REQUIRED]`), and versioned Markdown-based skill packages (`SKILL.md` + sandboxed supporting scripts) are isolated into distinct lifecycles, while cloud execution and local Windows execution maintain strict operational boundaries.

---

## 2. Problem Statement

Personal automation currently forces a user to choose between brittle static scripts (e.g., manual webhooks/macros that break on ambiguity) and unsafe autonomous agents (e.g., unconstrained LLM loops holding OAuth tokens and local OS access). Specifically, Orbi addresses five structural failures:

1. **Fragmented Execution Surfaces**: Email/SaaS APIs, authenticated web workflows without APIs, and local Windows desktop applications live in disconnected tools with no shared task tracker or memory.
2. **Prompt-Level Security Fallacy**: Relying on system prompts to stop an agent from deleting emails, spending money, or exfiltrating local files is vulnerable to prompt injection and reasoning drift.
3. **Ephemeral vs. Durable State Confusion**: Agent graph scratchpads and message histories are poor substitutes for deterministic task state machines capable of surviving restarts, enforcing budgets, pausing for immutable approvals, and recovering cleanly.
4. **Uncontrolled Memory & Skill Drift**: Storing raw task logs inside semantic memory pollutes future recall, while letting agents write and immediately execute unverified skills or scripts introduces silent regression and privilege escalation risks.
5. **Cloud-to-Local Opacity**: Running cloud agents directly against a personal Windows desktop without layered fallbacks, confidence thresholds, and local policy enforcement exposes local files, clipboard contents, and sensitive UIs to remote compromise or misclicks.

---

## 3. Target User & Operating Model

- `[CONFIRMED]` **Primary User & Household Extensibility**: Orbi serves a single primary owner initially via **Telegram** and a **Web Dashboard** (Dashboard UI framework remains `[OPEN QUESTION]` `OQ-01` for HLD). However, identity, resource ownership, and OpenFGA schemas are explicitly designed to accommodate **future household members** without requiring a fundamental architectural redesign (`ADR-002`). Multi-tenant commercial SaaS isolation (public self-signup, billing, cross-tenant physical partitioning) remains out of scope.
- `[CONFIRMED]` **Deployment Environment**: Cloud control core and isolated cloud sandboxes deployed via **Docker Compose on a single OCI VM** initially (`EVAL-05` benchmarking required; Always Free capacity is **not** assumed sufficient without verification), paired with an outbound-connected **Windows Companion** running locally on the user's Windows machine.

---

## 4. Core Architectural Principles (`AP-01` – `AP-10`)

`[CONFIRMED]` All product requirements, system modules, ADRs, and future HLD/LLD specifications MUST uphold these ten non-negotiable architectural principles:

| ID | Principle | Architectural Mandate |
| :--- | :--- | :--- |
| **`AP-01`** | **Gateway Exclusivity** | Agents must **never** bypass the Tool Execution Gateway. No agent module or skill may hold raw API credentials, database write handles to external targets, Playwright browser handles, or direct sockets to the Windows Companion. |
| **`AP-02`** | **Externalized Authorization** | Authorization must be enforced **outside the LLM** via OpenFGA. Prompt instructions are advisory only; tool discovery is pre-filtered by permissions, and every tool call, resource access, and skill invocation is checked deterministically before execution. |
| **`AP-03`** | **Task vs. Reasoning State Separation** | The deterministic Task Manager (PostgreSQL + Redis) and agent reasoning state (LangGraph runtime/scratchpad) must remain separate. Task lifecycle, retries, budgets, and immutable approval states live in the Task Manager, not inside LLM graph checkpoints. |
| **`AP-04`** | **Memory, Skills & App State Separation** | Long-term knowledge memory (`MOD-10: Memory Manager`, encapsulating Cognee strictly as an internal, replaceable adapter), reusable capabilities (versioned Markdown Skill Packages in the Skill Lifecycle Manager), and transactional application/task state (PostgreSQL) must remain distinct concepts, schemas, and lifecycles. |
| **`AP-05`** | **Clear Cloud vs. Local Boundary** | Local desktop execution (Windows Companion) and cloud execution (`orbi-core` + isolated cloud sandboxes) must maintain strict network, identity, data-redaction, resource-isolation, and kill-switch boundaries. |
| **`AP-06`** | **Explicit Approval for Sensitive/Irreversible Actions** | Sensitive writes and irreversible operations must pause task execution deterministically and require explicit human approval bound immutably to exact user, task, tool, arguments, target resource, policy version, and expiration (`ADR-004`). |
| **`AP-07`** | **End-to-End Auditability** | Every important operation—task creation, state transitions, decision-service calls, discovery filtering, OpenFGA checks, approval decisions, tool calls, memory commits, and skill publications—must emit immutable, correlated audit records. |
| **`AP-08`** | **Modularity & Independent Testability** | Every logical module must interact through explicit typed contracts and provider-independent ports so components can be unit- and contract-tested independently. |
| **`AP-09`** | **Pragmatic Simplicity** | Do not introduce unnecessary frameworks, message brokers, service meshes, or microservices. Run `orbi-core` as a modular monolith in Python/FastAPI/LangGraph, isolating separate containers/sandboxes only where execution isolation or OS locality requires it. |
| **`AP-10`** | **Extensibility Without Premature Implementation** | Design clean registry, harness, channel, household identity, and policy abstractions now so future capabilities (e.g., Home Assistant/MQTT IoT, household members, additional SaaS connectors) plug in cleanly without building them in Phase 1. |

---

## 5. High-Level Capability Pillars

### Pillar 1: Deterministic Task Spine, Autonomous Automation Platform & Cryptographic Approvals (`[CONFIRMED]`)
- **Deterministic Task Manager (`MOD-03`)**: A durable, PostgreSQL-backed Task Manager coordinated via Redis queues ensures every request or triggered automation becomes a trackable task with explicit states, configurable budgets, cancellation support, and deterministic pause/resume semantics bound to immutable action digests (`ADR-004`).
- **Autonomous Automation Platform (`MOD-17`) & Result Delivery (`MOD-18`) (`[CONFIRMED]`, `ADR-009`)**: Built behind a framework-independent, Phabric-reusable contract, `MOD-17` makes future/recurring scheduling, event subscriptions, non-LLM condition monitors, follow-ups, and multi-schedule ongoing `AgentResponsibility` plans a first-class capability exposed to authorized agents via `MOD-08` (`automation.*`) without keeping sleeping LLM sessions alive. Every triggered run dispatches a normal `MOD-03` task, and `MOD-18` formats/routes user notifications and post-task callbacks back into `MOD-17`.

### Pillar 2: Provider-Independent Cognitive & Decision Architecture (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)
- **System-2 Reasoning (`[CONFIRMED]`)**: **LangGraph** orchestrates a **Manager/Planner Agent** and domain-specialized sub-agents using **provider-independent LLM model adapters** with configurable provider routing and budget controls (`ADR-003`). Agents independently decide when an ongoing objective requires recurring checks, event hooks, condition monitors, or plan adaptation.
- **Provider-Independent Decision Service (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)**: A unified Decision Service handles classification, routing, and triage (`noul`, `choice`, `score` semantics) backed by deterministic rules and LLM-based fallbacks. **Jev by TypeSafe** is an **optional provider** subject to evaluation (`EVAL-01`); core operation never depends exclusively on Jev (`ADR-003`).

### Pillar 3: Permission-Filtered Discovery, Non-Inherited Delegation & Zero-Trust Execution (`[CONFIRMED]`)
Central **Agent** and **Tool Registries** catalog every identity and capability. Before reasoning starts, tool discovery filters the agent's catalog by agent, user, and instance OpenFGA permissions so agents only see authorized tools (`ADR-002`). All invocations—including `automation.*` operations and non-LLM condition monitor probes—route through the **Tool Execution Gateway**, which re-verifies **OpenFGA**, checks action-bound approvals, and injects secrets. When an agent delegates an automation to another agent, OpenFGA enforces authorization at creation, modification, and execution while guaranteeing zero permission inheritance and blocking circular delegation loops (`ADR-009`).

### Pillar 4: Tri-Partite Knowledge, Hybrid Memory Retrieval & Markdown Skill Packages (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)
- **Application State**: Relational operational truth in PostgreSQL.
- **`MOD-10: Memory Manager` & Hybrid Retrieval (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)**: `MOD-10: Memory Manager` is canonically owned by Orbi—**not** Cognee itself or a thin wrapper over Cognee's default retrieval API; it owns memory lifecycle (atomic outbox sync via `memory_sync_outbox`), provenance/quarantine, **bitemporal validity** (`valid_from`/`valid_to` real-world time + `recorded_from`/`recorded_to` system belief time + `superseded_by`), source-level OpenFGA enforcement, and orchestrates a configurable multi-stage hybrid retrieval pipeline (`ADR-008`, [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md)):
  - *Query Understanding*: Provider-independent extraction of intent, entities, keywords, bitemporal mode (`CURRENT`/`HISTORICAL`/`RANGE`), and graph utility to selectively activate retrieval branches without unnecessary LLM calls;
  - *Parallel Retrieval Branches*: Vector semantic retrieval (`CogneeAdapter`), configurable lexical retrieval (separately evaluating PG FTS `tsvector`/`ts_rank_cd` vs. Genuine BM25 `pg_search` `[PROPOSED - EVAL-07]`), Knowledge Graph entity/relationship retrieval (`CogneeAdapter` with source lineage), and selective Bitemporal interval retrieval (`orbi_memory`);
  - *Pre-Fusion & Source-Level OpenFGA Enforcement*: Mandatory resource-level authorization inside **every** branch plus independent authorization of every contributing source `MemoryUnit` on graph entities/paths (zero permission unioning across `private_user`, `shared_tenant`, `custom_acl`);
  - *Candidate Fusion & Reranking*: Configurable rank fusion for incomparable branch scales (initial proposal: Reciprocal Rank Fusion / RRF, `[PROPOSED - EVAL-07]`) deduplicating **strictly by `memory_unit_id`** (`content_hash` never merges distinct records), plus replaceable non-LLM-mandatory reranking (`[PROPOSED - EVAL-07]`), keeping Cognee strictly as an internal replaceable `CogneeAdapter` (`ADR-007`, `EVAL-03`, `EVAL-07`).
- **Skill Lifecycle (`[CONFIRMED]`)**: Versioned **Markdown-based skill packages (`SKILL.md`)** containing optional examples, schemas, tests, and sandboxed supporting scripts—distinguishing instructions from executable code and prohibiting permission elevation or Gateway bypass (`ADR-005`).

### Pillar 5: Isolated Cloud Harnesses & Layered Windows Companion (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)
- **External SaaS APIs**: Stateless typed adapters (e.g., Gmail, Spotify) inside `orbi-core` mediated by the Gateway.
- **Isolated Cloud Sandboxes**: Dedicated container for **Playwright** cloud browser automation and an isolated sandbox for **skill supporting scripts**.
- **Layered Windows Companion (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)**: Local Windows service executing in strict precedence: **(1) Native APIs -> (2) UI Automation (`vercel-labs/agent-desktop`, `EVAL-02`) -> (3) Controlled Vision Fallback**, halting safely on insufficient confidence and enforcing OpenFGA + approval checks regardless of interaction mode (`ADR-006`).
- **Future Home IoT**: Integration boundary preserved for **Home Assistant / MQTT** without prematurely committing to a single IoT protocol (`AP-10`, `ADR-006`).

---

## 6. Product Non-Goals

To preserve focus and enforce `AP-09`, Orbi explicitly defines the following non-goals:
1. **Unrestricted Autonomous Execution**: Orbi will never allow an agent or skill script to execute arbitrary host shell commands, hold raw OAuth tokens inside prompt/agent memory, invent tool IDs, or bypass approval gates on sensitive/irreversible actions.
2. **Exclusive Reliance on Unverified Cloud Vendors**: Core routing and reasoning must never depend exclusively on Jev or a single proprietary LLM API (`ADR-003`).
3. **Premature Microservice or Kubernetes Sprawl**: No Kubernetes, Kafka, or per-entity REST microservices while a single-VM Docker Compose deployment of `orbi-core` + isolated containers satisfies modularity and isolation goals (`AP-09`, `ADR-001`).
4. **Full Home IoT Implementation in Phase 1**: Home Assistant / MQTT integration interfaces are preserved at the registry/gateway/OpenFGA boundary (`AP-10`), with implementation deferred to Phase 5.
