# Orbi — Product & Engineering Requirements

- **Document ID**: `DOC-PRD-002`
- **Revision**: `1.1` *(Updated with GLD Review Decisions & Clarifications)*
- **Phase**: `00-Product`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-14`
- **Traceability**: Links to Architectural Principles (`AP-01`–`AP-10`) in [vision.md](file:///f:/Projects/Orbi/docs/00-product/vision.md), Modules (`MOD-01`–`MOD-15`) in [module-catalog.md](file:///f:/Projects/Orbi/docs/01-gld/module-catalog.md), and ADRs in [docs/04-adr/](file:///f:/Projects/Orbi/docs/04-adr/README.md).

---

## 1. Functional Requirements (`FR`)

### 1.1 User Interaction Channels & Dashboard (`FR-CH`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-CH-01`** | `[CONFIRMED]` | **Telegram Primary Interaction Channel**: The platform shall receive user commands, queries, and follow-up clarifications via Telegram and deliver task status updates, summaries, and results back to authenticated Telegram chats. Identity mapping shall support the primary owner initially and scale cleanly to future household members (`ADR-002`). | `AP-07`, `AP-10` | `MOD-01` |
| **`FR-CH-02`** | `[PROPOSED]` | **Telegram Interactive Approval Callbacks**: The Telegram adapter shall render pending sensitive/irreversible approval requests with immutable action summary payloads (tool name, canonical arguments preview, target resource, policy version, expiration) and interactive Approve/Deny actions verified against the bound OpenFGA user identity (`ASM-07`). | `AP-02`, `AP-06` | `MOD-01`, `MOD-03` |
| **`FR-CH-03`** | `[CONFIRMED]` | **Administration & Monitoring Dashboard**: The platform shall provide a web Dashboard (frontend stack selected in HLD, `OQ-01`) enabling authorized users to inspect active/historical tasks, step traces, registered agents/tools, OpenFGA authorization policies, system health, and audit logs. | `AP-07`, `AP-08` | `MOD-02` |
| **`FR-CH-04`** | `[CONFIRMED]` | **Dashboard Approval, Memory & Skill Curation**: The Dashboard shall provide dedicated views to approve/reject pending tasks, inspect/correct/delete long-term memory entities via the Memory Manager, and review/approve candidate Markdown skill packages in the Skill Lifecycle Manager. | `AP-04`, `AP-06` | `MOD-02`, `MOD-10`, `MOD-11` |

### 1.2 Deterministic Task Management (`FR-TM`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-TM-01`** | `[CONFIRMED]` | **Durable Task State Machine**: Every accepted user request shall materialize as a persistent Task record in PostgreSQL governed by a deterministic finite state machine (`CREATED`, `QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED`). | `AP-03`, `AP-07` | `MOD-03` |
| **`FR-TM-02`** | `[CONFIRMED]` | **Queue-Driven Asynchronous Execution**: The Task Manager shall dispatch runnable task steps and harness work via Redis queues so long-running or blocked operations do not stall ingress channels. | `AP-03`, `AP-09` | `MOD-03` |
| **`FR-TM-03`** | `[CONFIRMED]` | **Strict Isolation from Reasoning State**: Task lifecycle state, step counters, retry policies, and approval bindings shall be owned exclusively by the Task Manager in PostgreSQL and must never rely on LangGraph internal memory or checkpoints as the source of truth. | `AP-03` | `MOD-03`, `MOD-04` |
| **`FR-TM-04`** | `[CONFIRMED]` | **Configurable Execution Budgets & Deadlines**: Tasks shall enforce deterministic, configurable guardrails—including maximum step counts, wall-clock timeouts, per-task/daily LLM token/cost budgets (`ADR-003`), and retry limits—and transition deterministically upon breach. | `AP-03`, `AP-08` | `MOD-03` |
| **`FR-TM-05`** | `[CONFIRMED]` | **Idempotent Pause & Action-Bound Resume on Approval**: When a task pauses at `AWAITING_APPROVAL`, the Task Manager shall persist an immutable Approval Request record bound to the exact action digest (`FR-GW-07`, `ADR-004`); resuming execution shall verify identity, expiration, and digest match with idempotent replay protection. | `AP-06`, `AP-07` | `MOD-03`, `MOD-08` |

### 1.3 Agent Orchestration, Discovery & Decision Service (`FR-OR`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-OR-01`** | `[CONFIRMED]` | **Hierarchical Manager & Specialized Agents**: A Manager/Planner Agent shall decompose complex user goals and select domain-specialized agents registered in the Central Agent Registry (`MOD-06`). | `AP-08`, `AP-10` | `MOD-04`, `MOD-06` |
| **`FR-OR-02`** | `[CONFIRMED]` | **LangGraph Runtime & Provider-Independent LLM Adapters**: Agent reasoning loops and tool-intent planning shall execute within LangGraph (`MOD-04`) using provider-independent LLM model adapters (`ADR-003`), allowing configurable model selection and budget policies per agent/task without coupling core logic to one LLM vendor. | `AP-03`, `AP-08` | `MOD-04` |
| **`FR-OR-03`** | `[CONFIRMED]` *(Jev: `[EVALUATION REQUIRED]`)* | **Provider-Independent Decision Service**: The platform shall implement a provider-independent Decision Service (`MOD-05`, `ADR-003`) supporting structured classification/routing (`noul`, `choice`, `score`) with deterministic rule-based and LLM-based fallback strategies and strict timeouts. **Jev by TypeSafe** is integrated strictly as an **optional provider** subject to evaluation (`EVAL-01`); core operation must never depend exclusively on Jev. | `AP-08`, `AP-09` | `MOD-05` |
| **`FR-OR-04`** | `[CONFIRMED]` | **Central Agent Registry**: All agents must be declaratively registered in a Central Agent Registry specifying identity (`agent:<id>`), role description, configurable model profile, configured tool/skill bindings, and status (`ACTIVE`, `DEPRECATED`, `DISABLED`). | `AP-02`, `AP-08` | `MOD-06` |
| **`FR-OR-05`** | `[CONFIRMED]` | **Intent-Only Agent Output**: Agents may only produce final user responses, authorized memory queries, skill invocation intents, or structured `ToolCallIntent` requests from their authorized catalog; agents must not invent tool IDs, grant themselves access, or execute tools directly. | `AP-01`, `AP-02` | `MOD-04`, `MOD-08` |
| **`FR-OR-06`** | `[CONFIRMED]` | **Permission-Filtered Agent & Tool Discovery**: When the Manager selects an agent from `MOD-06`, the Runtime (`MOD-04`) shall initialize the agent, retrieve its configured tool catalog from `MOD-07`, filter available tools via `MOD-09` (OpenFGA) against agent, user, and instance permissions, and provide only authorized tool definitions/schemas to LangGraph (`ADR-002`). | `AP-01`, `AP-02` | `MOD-04`, `MOD-06`, `MOD-07`, `MOD-09` |

### 1.4 Tool Registry, Tool Execution Gateway & Authorization (`FR-GW`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-GW-01`** | `[CONFIRMED]` | **Central Tool Registry**: Every callable tool and published skill wrapper shall be registered in a Central Tool Registry with versioned JSON Input/Output schemas, target harness/isolation tier, idempotency semantics, and a mandatory risk tier (`READ_ONLY`, `LOW_RISK_WRITE`, `SENSITIVE_WRITE`, `IRREVERSIBLE`). | `AP-01`, `AP-06` | `MOD-07` |
| **`FR-GW-02`** | `[CONFIRMED]` | **Single Tool Execution Chokepoint**: 100% of tool executions—across SaaS APIs, Playwright browser actions, sandboxed skill scripts, Windows Companion commands, and future IoT calls—shall route through the Tool Execution Gateway. | `AP-01` | `MOD-08` |
| **`FR-GW-03`** | `[CONFIRMED]` | **Externalized Household-Extensible OpenFGA Enforcement**: Before dispatching any tool or exposing sensitive memory/resource scope, the Gateway shall query OpenFGA (`MOD-09`) outside the LLM using deterministic subject, ownership/household, agent, tool, and target resource tuples (`ADR-002`). Fail-closed on error or denial. | `AP-02`, `AP-10` | `MOD-08`, `MOD-09` |
| **`FR-GW-04`** | `[CONFIRMED]` | **Mandatory Human Approval Gate**: Any tool call classified as `SENSITIVE_WRITE` or `IRREVERSIBLE` (or escalated by OpenFGA policy / Decision Service risk signal) shall be blocked by the Gateway and routed to `MOD-03` to obtain explicit human approval before execution. | `AP-06` | `MOD-08`, `MOD-03` |
| **`FR-GW-05`** | `[PROPOSED]` | **Schema Validation & Credential Injection**: The Gateway shall strictly validate `ToolCallIntent` payloads against `MOD-07` JSON Schema and inject OAuth tokens, API keys, or session secrets inside the Gateway/Connector boundary so agents and skill scripts never observe raw credentials. | `AP-01`, `AP-02` | `MOD-08`, `MOD-12` |
| **`FR-GW-06`** | `[PROPOSED]` | **Result Sanitization & Size Bounding**: The Gateway shall normalize harness responses, strip leaked secret patterns or oversized payloads, and tag external untrusted content (e.g., email bodies, web DOM text, screen OCR) with provenance labels before returning observations to the agent. | `AP-01`, `AP-05` | `MOD-08` |
| **`FR-GW-07`** | `[CONFIRMED]` | **Immutable Action-Bound Approval Integrity**: Every approval grant shall bind cryptographically to the exact `(user_id, task_id, tool_id, tool_version, canonical_arguments_hash, target_resource, policy_version, expires_at)` tuple (`ADR-004`). Any alteration to tool arguments, target resource, or policy version, or expiry of the TTL, invalidates the approval and requires a fresh human approval. | `AP-06`, `AP-07` | `MOD-03`, `MOD-08`, `MOD-09` |

### 1.5 State, Memory & Skill Lifecycle (`FR-MS`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-MS-01`** | `[CONFIRMED]` | **Tri-Partite Data Separation**: Application/Task State (PostgreSQL), Long-Term Knowledge Memory (Memory Manager over Cognee), and Reusable Skill Packages (Skill Lifecycle Manager) shall reside in logically distinct storage boundaries, schemas, and lifecycles. | `AP-04` | `MOD-03`, `MOD-10`, `MOD-11` |
| **`FR-MS-02`** | `[CONFIRMED]` *(Backends: `[EVALUATION REQUIRED]`)* | **Memory Manager Abstraction over Cognee**: `MOD-10` shall act as a Memory Manager facade encapsulating **Cognee** to ingest, index, retrieve, and curate user preferences, facts, and entity relationships, subject to OpenFGA ownership/household scopes. Minimal infrastructure is preferred, and actual Cognee storage backends shall be verified in `EVAL-03` before HLD finalization (`ADR-007`). | `AP-04`, `AP-02`, `AP-09` | `MOD-10` |
| **`FR-MS-03`** | `[PROPOSED]` | **Memory Provenance & Quarantine**: Facts extracted from untrusted external inputs (e.g., incoming emails, web pages, OCR captures) shall carry source provenance and trust tags, preventing unverified prompt-injected instructions from silently overwriting core user preferences or policies. | `AP-04`, `AP-07` | `MOD-10` |
| **`FR-MS-04`** | `[CONFIRMED]` | **Versioned Markdown Skill Packages (`SKILL.md`)**: Reusable skills shall be authored and managed as versioned Markdown-based skill packages (`SKILL.md` instructions plus optional examples, JSON schemas, test fixtures, and supporting scripts). Candidate skills must pass sandboxed evaluation (`EVALUATED`) before publication (`ADR-005`). | `AP-04`, `AP-08` | `MOD-11` |
| **`FR-MS-05`** | `[CONFIRMED]` | **Human-Gated Skill Publication**: Newly generated or modified skill packages remain in `DRAFT` / `EVALUATED` states until explicitly approved by an authorized human owner; unapproved skills cannot be promoted or invoked in production tasks. | `AP-06`, `AP-02` | `MOD-11`, `MOD-02` |
| **`FR-MS-06`** | `[CONFIRMED]` | **Skill Pinning & Rollback**: Tasks and agent profiles shall reference pinned immutable skill versions (`skill:<id>@<semver>`), enabling deterministic behavior and instant rollback without breaking active executions. | `AP-04`, `AP-08` | `MOD-11`, `MOD-07` |
| **`FR-MS-07`** | `[CONFIRMED]` | **Instruction vs. Script Separation & Script Sandboxing**: Skill Markdown instructions guide cognitive planning only and **never** grant permissions. Any supporting executable script bundled inside a skill package must run inside an isolated cloud sandbox (`MOD-11-SANDBOX`) with no ambient credentials, no direct network/host access, and must invoke external capabilities exclusively via `MOD-08` (`AP-01`). | `AP-01`, `AP-02`, `AP-04` | `MOD-11`, `MOD-08` |

### 1.6 Execution Harnesses (`FR-EX`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-EX-01`** | `[CONFIRMED]` | **External SaaS API Connectors**: The platform shall provide stateless, typed adapters inside `orbi-core` for external SaaS APIs—starting with services such as Gmail and Spotify—invoked exclusively through `MOD-08`. | `AP-01`, `AP-10` | `MOD-12` |
| **`FR-EX-02`** | `[CONFIRMED]` | **Isolated Cloud Browser Harness (Playwright)**: The platform shall operate a dedicated, container-isolated Playwright browser execution environment (`MOD-13`) on OCI for web workflows lacking APIs, enforcing domain allowlists/blocklists and Gateway mediation. | `AP-01`, `AP-05` | `MOD-13` |
| **`FR-EX-03`** | `[CONFIRMED]` *(UIA/Vision: `[EVALUATION REQUIRED]`)* | **Layered Windows Companion Execution Hierarchy**: Local desktop operations in `MOD-14` shall follow a strict 3-tier fallback order (`ADR-006`): **(1) Native OS/App APIs** first; **(2) UI Automation (UIA)** via Vercel Labs `agent-desktop` (`EVAL-02`) second; **(3) Controlled Vision Fallback** only when native APIs and UIA cannot resolve target elements. | `AP-05`, `AP-08` | `MOD-14` |
| **`FR-EX-04`** | `[CONFIRMED]` | **Local Desktop Guardrails, Safe Confidence Stops & Kill Switch**: `MOD-14` shall halt execution safely (`STOP_INSUFFICIENT_CONFIDENCE`) whenever UIA resolution or vision grounding confidence falls below threshold, enforce local app allow/deny lists and data redaction, expose a local pause/kill switch, and enforce OpenFGA + HITL approvals identically across Native API, UIA, and Vision modes (`ADR-006`). | `AP-05`, `AP-06` | `MOD-14`, `MOD-08`, `MOD-09` |
| **`FR-EX-05`** | `[CONFIRMED]` | **Future Home IoT Integration Boundary (Home Assistant / MQTT)**: `MOD-07` Tool Registry schemas, `MOD-08` harness dispatch interfaces, and `MOD-09` OpenFGA resource types shall preserve a clean integration boundary for future **Home Assistant / MQTT** integrations without committing prematurely to a single protocol or implementing IoT now. | `AP-10` | `MOD-07`, `MOD-08`, `MOD-09`, `MOD-16-EXT` |

### 1.7 Audit & Observability (`FR-AU`)

| ID | Status | Requirement | Principle(s) | Target Module(s) |
| :--- | :--- | :--- | :--- | :--- |
| **`FR-AU-01`** | `[CONFIRMED]` | **Immutable Correlation Ledger**: Every task transition, discovery filter result, Decision Service evaluation (provider used, fallback triggered, confidence), OpenFGA check, action-bound approval event (including payload digest), tool execution, memory mutation, and skill publication shall be recorded with shared `correlation_id` and `task_id`. | `AP-07` | `MOD-15` |
| **`FR-AU-02`** | `[PROPOSED]` | **Secret-Safe Redacted Auditing**: Audit logs shall persist payload digests and sanitized metadata while redacting OAuth tokens, passwords, and sensitive personal/screen payloads before storage. | `AP-07`, `AP-05` | `MOD-15`, `MOD-08` |
| **`FR-AU-03`** | `[PROPOSED]` | **Append-Only Audit Integrity**: Audit records shall be append-only at the application tier (no `UPDATE`/`DELETE` endpoints exposed to agents or operational workflows). | `AP-07` | `MOD-15` |

---

## 2. Non-Functional Requirements (`NFR`)

| Category | ID | Status | Requirement |
| :--- | :--- | :--- | :--- |
| **Security** | **`NFR-SEC-01`** | `[CONFIRMED]` | **Zero-Trust Agent & Skill Runtime**: Prompt injection within retrieved emails, web pages, Cognee memories, skill Markdown files, or desktop screenshots/UIA trees shall not invent tools, elevate permissions, or bypass OpenFGA + action-bound HITL approvals. |
| **Security** | **`NFR-SEC-02`** | `[PROPOSED]` | **Encrypted Cloud-Local Transport**: Traffic between OCI (`MOD-08`) and the Windows Companion (`MOD-14`) shall use mutually authenticated TLS (mTLS) over an outbound-initiated persistent connection from Windows (`ASM-04`, `ASM-05`). |
| **Security** | **`NFR-SEC-03`** | `[PROPOSED]` | **Least-Privilege Secrets Management**: External API credentials and Telegram secrets shall reside exclusively in OCI vault/secrets storage accessible only to `MOD-08`/`MOD-12`, never inside agent prompts, skill sandboxes, or Cognee memory. |
| **Reliability** | **`NFR-REL-01`** | `[PROPOSED]` | **Crash Recovery & Durable Checkpoints**: Restarting any OCI container shall not lose active task records, pending action-bound approvals, or audit history; Redis queues reconcile cleanly against PostgreSQL authoritative task state. |
| **Reliability** | **`NFR-REL-02`** | `[CONFIRMED]` | **Fail-Closed Governance**: If OpenFGA is unreachable, Tool Registry schema is missing, or approval payload verification fails (digest mismatch, expired, changed `policy_version`), `MOD-08` shall deny execution. |
| **Reliability** | **`NFR-REL-03`** | `[CONFIRMED]` | **Provider-Independent Decision & Model Resilience**: If optional Jev (`MOD-05`) times out, errors, or returns low confidence, `MOD-05` falls back transparently to deterministic rules or LLM-based classification. Likewise, LLM model adapters support configurable fallback providers (`ADR-003`). |
| **Isolation** | **`NFR-MOD-03`** | `[CONFIRMED]` | **Three-Zone Resource Isolation**: Clear boundaries shall separate (1) `orbi-core` in-process control/cognitive/API modules, (2) isolated cloud execution containers (`MOD-13` Playwright, `MOD-11-SANDBOX` skill script sandbox, `MOD-09` OpenFGA, DBs), and (3) local off-cloud `MOD-14` Windows Companion (`ADR-001`). |
| **Testability** | **`NFR-MOD-01`** | `[CONFIRMED]` | **Independent Module Testability (`AP-08`)**: All modules (`MOD-01`–`MOD-15`) interact via typed Python ports, OpenFGA contracts, and queue schemas enabling unit and integration testing with mocked Decision/LLM/Harness providers. |
| **Infrastructure** | **`NFR-INF-01`** | `[CONFIRMED]` *(Sizing: `[EVALUATION REQUIRED]`)* | **Modular Single-VM OCI Docker Compose Deployment**: Initial cloud deployment uses a modular Docker Compose topology on a single OCI VM (`AP-09`). Resource adequacy (specifically OCI Always Free vs. paid compute/RAM tiers) must **not** be assumed and requires benchmarking (`EVAL-05`). |

---

## 3. Explicit Assumptions (`ASM-01` – `ASM-08`)

| ID | Status | Statement & Current Disposition |
| :--- | :--- | :--- |
| **`ASM-01`** | `[CONFIRMED -> ADR-002]` | **Household-Extensible Single-Owner Baseline**: Orbi starts with one primary owner, while identity, resource ownership, and OpenFGA schemas cleanly model future household members without structural rework. |
| **`ASM-02`** | `[CONFIRMED -> ADR-001]` | **Core Runtime Stack**: `orbi-core` is standardized on **Python 3.12+, FastAPI, and LangGraph**. |
| **`ASM-03`** | `[CONFIRMED -> EVAL-05]` | **Docker Compose Single-VM Baseline**: Initial OCI deployment uses modular Docker Compose on a single VM; actual VM shape/RAM tier is **unverified** pending `EVAL-05` benchmarking. |
| **`ASM-04`** | `[ASSUMPTION]` | **Windows NAT/Firewall Reality**: The user's personal Windows workstation sits behind residential NAT/dynamic IP and cannot expose inbound ports to the public internet. |
| **`ASM-05`** | `[ASSUMPTION]` | **Outbound mTLS Relay**: The Windows Companion (`MOD-14`) initiates an outbound persistent authenticated tunnel (WebSocket/gRPC over mTLS) to an OCI relay managed by `MOD-08`. |
| **`ASM-06`** | `[ASSUMPTION]` | **Consolidated PostgreSQL Cluster**: A single PostgreSQL server in Docker Compose hosts logically isolated databases/schemas (`orbi_app`, `openfga`, and `cognee` only if supported and validated by `EVAL-03`). |
| **`ASM-07`** | `[ASSUMPTION]` | **Telegram Identity Anchoring**: Telegram access maps verified numeric `user_id` + `chat_id` tuples to OpenFGA user principals (`user:<id>`), supporting owner and future household members (`ASM-01`). |
| **`ASM-08`** | `[CONFIRMED -> ADR-003]` | **Provider-Independent External/Local LLM Access**: LangGraph agents invoke LLMs through provider-independent adapters supporting configurable model selection and budget policies. |

---

## 4. Unresolved Questions Register (`OQ-01` – `OQ-12`)

| ID | Status | Question | Impacted Area & Target Phase |
| :--- | :--- | :--- | :--- |
| **`OQ-01`** | **OPEN (HLD)** | **Dashboard Frontend Stack**: Core backend is confirmed (`Python 3.12+ / FastAPI / LangGraph`). Which frontend technology is preferred for `MOD-02` Dashboard (e.g., Next.js/React vs. lightweight HTMX/FastAPI vs. Vite SPA)? | `MOD-02` / HLD |
| **`OQ-02`** | **RESOLVED** | *Tenancy Model* -> Single-owner initially, schema/OpenFGA ready for household members ([ADR-002](file:///f:/Projects/Orbi/docs/04-adr/ADR-002-household-openfga-and-two-stage-discovery.md)). | `MOD-09` |
| **`OQ-03`** | **RESOLVED** | *Jev Dependency & Fallback* -> Provider-independent Decision Service (`MOD-05`); Jev optional (`EVAL-01`); deterministic + LLM fallbacks required ([ADR-003](file:///f:/Projects/Orbi/docs/04-adr/ADR-003-provider-independent-decision-and-llm-adapters.md)). | `MOD-05` |
| **`OQ-04`** | **PARTIALLY RESOLVED** | *LLM Providers & Concrete Budgets*: Adapter pattern confirmed ([ADR-003](file:///f:/Projects/Orbi/docs/04-adr/ADR-003-provider-independent-decision-and-llm-adapters.md)). **Remaining open for HLD/Config**: Which initial LLM providers/models (e.g., Anthropic/OpenAI/Gemini/local) and default daily/per-task spend caps should ship as defaults? | `MOD-03`, `MOD-04` / HLD |
| **`OQ-05`** | **OPEN (`EVAL-05`)** | **OCI VM Sizing Benchmark**: Docker Compose on a single VM is confirmed. What actual RAM/CPU footprint is needed once OpenFGA, PostgreSQL, Redis, `orbi-core`, Playwright, and Cognee backends are benchmarked (`EVAL-05`)? *(Must not assume Always Free suffices.)* | Infrastructure / Spike & HLD |
| **`OQ-06`** | **OPEN (`EVAL-03`)** | **Cognee Verified Storage Backends**: What exact relational, vector, and graph storage backends does the target version of Cognee officially support and stabilize in Docker Compose (`EVAL-03`)? | `MOD-10` / Spike & HLD |
| **`OQ-07`** | **RESOLVED** | *Skill Format* -> Versioned Markdown-based skill packages (`SKILL.md` + optional examples, schemas, tests, sandboxed supporting scripts) ([ADR-005](file:///f:/Projects/Orbi/docs/04-adr/ADR-005-markdown-skill-packages-and-script-sandboxing.md)). | `MOD-11` |
| **`OQ-08`** | **OPEN (HLD)** | **Concrete Sensitivity Matrix & Approval TTL Defaults**: Action-bound approval semantics are confirmed ([ADR-004](file:///f:/Projects/Orbi/docs/04-adr/ADR-004-cryptographic-action-bound-approvals.md)). **Remaining open**: Exact default tier mapping (`LOW_RISK_WRITE` vs. `SENSITIVE_WRITE` vs. `IRREVERSIBLE`) for Gmail, Spotify, Browser, and Desktop actions, plus default expiration TTLs (e.g., 10 min vs. 24 h). | `MOD-03`, `MOD-07` / HLD |
| **`OQ-09`** | **RESOLVED** | *Windows Execution Hierarchy* -> Native APIs -> UIA (`agent-desktop`) -> Controlled Vision Fallback with safe confidence stops ([ADR-006](file:///f:/Projects/Orbi/docs/04-adr/ADR-006-layered-windows-companion-execution.md)). *(Specific blocked app list & confidence thresholds deferred to `EVAL-02` / HLD.)* | `MOD-14` |
| **`OQ-10`** | **RESOLVED** | *Future Home IoT Boundary* -> Preserve clean integration boundary for future Home Assistant / MQTT without committing to a specific protocol yet ([ADR-006](file:///f:/Projects/Orbi/docs/04-adr/ADR-006-layered-windows-companion-execution.md)). | `MOD-16-EXT` |
| **`OQ-11`** | **OPEN (`EVAL-06`)** | **Skill Script Sandbox Technology**: What isolation runtime will execute optional supporting scripts in Markdown skill packages (`MOD-11-SANDBOX` — e.g., ephemeral Docker container, Firecracker/gVisor, WASM/Pyodide, or restricted OS container with seccomp/no-net)? | `MOD-11`, `CON-08` / Spike & HLD |
| **`OQ-12`** | **OPEN (`EVAL-02` / HLD)** | **Vision Fallback Guardrails & Dynamic Approval Arguments**: For `MOD-14` vision fallback, which vision grounding mechanism is permitted, what minimum confidence threshold triggers safe stop, and how does `ADR-004` canonical argument hashing handle volatile UI coordinates/timestamps (`CON-09`, `CON-10`)? | `MOD-08`, `MOD-14` / Spike & HLD |
