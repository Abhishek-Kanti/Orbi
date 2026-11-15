# Orbi — Overall System High-Level Design (HLD)

- **Document ID**: `DOC-HLD-002`
- **Revision**: `1.0`
- **Phase**: `02-HLD`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-16`
- **Authoritative Sources**:
  - Product Baseline: [vision.md](file:///f:/Projects/Orbi/docs/00-product/vision.md) (`DOC-PRD-001`), [requirements.md](file:///f:/Projects/Orbi/docs/00-product/requirements.md) (`DOC-PRD-002`), [scope-and-roadmap.md](file:///f:/Projects/Orbi/docs/00-product/scope-and-roadmap.md) (`DOC-PRD-003`)
  - General Level Design: [system-overview.md](file:///f:/Projects/Orbi/docs/01-gld/system-overview.md) (`DOC-GLD-001`), [module-catalog.md](file:///f:/Projects/Orbi/docs/01-gld/module-catalog.md) (`DOC-GLD-002`), [functional-responsibilities.md](file:///f:/Projects/Orbi/docs/01-gld/functional-responsibilities.md) (`DOC-GLD-003`)
  - Architecture Decision Records: [ADR-001](file:///f:/Projects/Orbi/docs/04-adr/ADR-001-modular-monolith-and-resource-isolation.md) through [ADR-008](file:///f:/Projects/Orbi/docs/04-adr/ADR-008-hybrid-memory-retrieval-architecture.md)
  - Memory Subsystem Authority: [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md) (`DOC-HLD-001`)

> **Epistemic Discipline**: Every confirmed decision (`[CONFIRMED]`), proposed HLD design (`[PROPOSED]`), evaluation-gated capability (`[EVALUATION REQUIRED]` `EVAL-01`–`07`), and unresolved open question (`[OPEN QUESTION]` `OQ-01`–`14`) preserves strict alignment with the Product/GLD Rev 1.2 baseline and ADRs. Untested proposals (e.g., Jev provider efficacy, Cognee backend selection, BM25/RRF/reranking parameters, OCI Always-Free VM adequacy, Dashboard UI framework, Skill script sandbox runtime) are never represented as finalized facts.

---

## A. Overall Architecture

### A.1 System Context & External Actors

`[CONFIRMED]` Orbi is a personal AI agent platform connecting conversational channels, an operator dashboard, deterministic task governance, hybrid knowledge memory, external SaaS APIs, cloud browser automation, and local Windows desktop interaction—while preserving clean extension boundaries for future household members (`ADR-002`), Home Assistant/MQTT IoT (`ADR-006`), and a Developer Agent (`AP-10`).

#### Diagram 1: Overall System Context

```mermaid
flowchart LR
    OWNER["Human Owner / Future Household Member"]
    TG["Telegram Bot API"]
    WEB["Admin Web Browser"]
    WIN_PC["Personal Windows Workstation"]

    ORBI["Orbi Platform - OCI Cloud Core & Harnesses"]

    LLMS["External LLM Providers - Configurable Adapters"]
    JEV["Optional Jev Decision API - EVAL-01"]
    SAAS["External SaaS APIs - Gmail, Outlook, Spotify"]
    INTERNET["Target Websites - Playwright Automation"]
    IOT["Future Phase 5: Home Assistant / MQTT / ESP32"]

    OWNER <-->|"Chat Commands & Approvals"| TG
    OWNER <-->|"Admin, Audit, Memory & Takeover"| WEB
    OWNER <-->|"Local Desktop & Kill-Switch"| WIN_PC

    TG <-->|"HTTPS Webhook / Polling"| ORBI
    WEB <-->|"HTTPS + WSS / SSE"| ORBI
    WIN_PC <-->|"Outbound mTLS Tunnel"| ORBI

    ORBI <-->|"HTTPS ModelAdapter"| LLMS
    ORBI -. "Optional HTTPS + Fallback" .-> JEV
    ORBI <-->|"OAuth2 / REST via Gateway"| SAAS
    ORBI <-->|"Isolated Egress - Allowlisted"| INTERNET
    ORBI -. "Future Phase 5 Boundary" .-> IOT
```

### A.2 Four-Plane Logical Architecture (`CON-21` Reconciliation)

To satisfy `AP-08` (modularity) and `AP-09` (no unnecessary microservices), Orbi maps its 15 core modules (`MOD-01`–`MOD-15`, plus `MOD-16-EXT`) across **four logical planes** aligned 1-to-1 with the **three physical isolation zones** defined in [ADR-001](file:///f:/Projects/Orbi/docs/04-adr/ADR-001-modular-monolith-and-resource-isolation.md):

| Logical HLD Plane | Mapped GLD Modules | Physical Execution Zone (`ADR-001`) | Architectural Responsibility |
| :--- | :--- | :--- | :--- |
| **1. Control Plane** | `MOD-01`, `MOD-02` (BFF + UI), `MOD-03`, `MOD-04`, `MOD-05`, `MOD-08`, `MOD-11` (Manager) | Zone 1 (`orbi-core` API/Worker) + Zone 2b (`orbi-dashboard`) | User ingress, deterministic task FSM, bounded cognitive turns, Decision Service, Gateway enforcement, skill lifecycle control. |
| **2. Trusted Data & Policy Plane** | `MOD-06`, `MOD-07`, `MOD-09`, `MOD-10` (Orchestrator + Catalog), `MOD-15`, Postgres, Redis, Blob Store | Zone 1 (`MOD-06`/`07`/`10`/`15` facades) + Zone 2b (`orbi-openfga`, `orbi-postgres`, `orbi-redis`) | Authoritative task state, agent/tool/skill registries, OpenFGA ReBAC policies, canonical memory catalog & audit ledger, secret vault. |
| **3. Isolated Execution Plane** | `MOD-12` (Stateless SaaS), `MOD-13` (Playwright), `MOD-11-SANDBOX` (Future Scripts), `Cognee` Stores | Zone 1 Gateway-mediated (`MOD-12`) + Zone 2a (`orbi-browser`, `MOD-11-SANDBOX`) + Zone 2b (`Cognee` backends) | Executes side-effecting APIs, untrusted DOM rendering, future skill scripts, and Cognee vector/graph processing outside `MOD-04` trust. |
| **4. Local Execution Plane** | `MOD-14` (Windows Companion), Future `MOD-16-EXT` (IoT Bridge) | Zone 3 (Off-Cloud Local Workstation / Home LAN) | Outbound mTLS client executing Native API -> UIA (`agent-desktop`) -> Controlled Vision Fallback under local kill-switch & app policies. |

#### Diagram 2: Logical Architecture Across the Four Planes

```mermaid
flowchart TB
    subgraph CP["1. Control Plane - Ingress, Orchestration & Enforcement"]
        MOD01["MOD-01: Channel Gateway - Telegram"]
        MOD02["MOD-02: Dashboard UI & FastAPI BFF"]
        MOD03["MOD-03: Deterministic Task Manager"]
        MOD04["MOD-04: LangGraph Runtime & Model Adapters"]
        MOD05["MOD-05: Decision Service - Rules/LLM/Optional Jev"]
        MOD08["MOD-08: Tool Execution Gateway"]
        MOD11["MOD-11: Skill Lifecycle Manager"]
    end

    subgraph TDPP["2. Trusted Data & Policy Plane - State, Registries & Authz"]
        MOD06["MOD-06: Central Agent Registry"]
        MOD07["MOD-07: Central Tool Registry"]
        MOD09["MOD-09: OpenFGA Policy Engine"]
        MOD10["MOD-10: Memory Manager & Catalog - HLD-001"]
        MOD15["MOD-15: Append-Only Audit Ledger"]
        PG["PostgreSQL - orbi_app / orbi_memory / openfga / lg_ckpt"]
        REDIS["Redis - Queues, Leases & PubSub"]
        VAULT["Encrypted Secret Store & Blob Volume"]
    end

    subgraph IEP["3. Isolated Execution Plane - Untrusted Workloads & SaaS"]
        MOD12["MOD-12: SaaS Adapters - Gmail, Spotify, MCP"]
        MOD13["MOD-13: Playwright Cloud Browser Container"]
        MOD11_SB["MOD-11-SANDBOX: Future Skill Script Sandbox"]
        COGNEE["Cognee Vector & Graph Backends - EVAL-03"]
    end

    subgraph LEP["4. Local Execution Plane - Off-Cloud User Hardware"]
        MOD14["MOD-14: Windows Companion - Native / UIA / Vision"]
        MOD16["MOD-16-EXT: Future Home Assistant / MQTT / ESP32"]
    end

    MOD01 --> MOD03
    MOD02 <--> MOD03
    MOD02 <--> MOD09
    MOD02 <--> MOD10
    MOD02 <--> MOD11
    MOD02 --> MOD15

    MOD03 <--> REDIS
    MOD03 <--> PG
    MOD03 <--> MOD04
    MOD03 --> MOD05
    MOD03 --> MOD15

    MOD04 --> MOD05
    MOD04 --> MOD06
    MOD04 --> MOD07
    MOD04 --> MOD09
    MOD04 <--> MOD10
    MOD04 <--> MOD11
    MOD04 -- "ToolCallIntent ONLY" --> MOD08

    MOD08 --> MOD07
    MOD08 --> MOD09
    MOD08 <--> MOD03
    MOD08 --> VAULT
    MOD08 --> MOD15

    MOD08 --> MOD12
    MOD08 --> MOD13
    MOD08 -. "Phase 4+ Future Scripts" .-> MOD11_SB
    MOD14 -- "Outbound mTLS Tunnel" --> MOD08
    MOD16 -. "Phase 5 Future IoT" .-> MOD08

    MOD09 <--> PG
    MOD10 <--> PG
    MOD10 <--> MOD09
    MOD10 <--> COGNEE
    MOD15 --> PG
    MOD11 --> PG
    MOD11 --> MOD07
```

### A.3 Deployment Topology & Trust Boundaries

`[CONFIRMED]` Cloud services deploy via **Docker Compose on a single Oracle Cloud Infrastructure (OCI) VM** (`ADR-001`, VM hardware sizing gated by `EVAL-05` rather than assuming Always-Free adequacy).

#### Diagram 3: Deployment Topology & Trust Boundaries

```mermaid
flowchart TB
    subgraph TB1["TB-01: Untrusted External Internet & Channels"]
        EXT_TG["Telegram Servers"]
        EXT_SAAS["Gmail / Spotify / External MCP"]
        EXT_WEB["Public Web Pages"]
    end

    subgraph OCI_VM["OCI Single-VM Docker Compose Host - Sizing Pending EVAL-05"]
        subgraph TB2["TB-02: TLS Reverse Proxy / DMZ Network"]
            PROXY["Caddy / Nginx Reverse Proxy - HTTPS & mTLS Termination"]
            DASH["orbi-dashboard Container - MOD-02 UI - Tech OQ-01"]
        end

        subgraph TB3["TB-03: Control Plane Internal Network - orbi-core"]
            API["orbi-core-api - FastAPI: Webhooks, BFF, Gateway mTLS Relay"]
            WORKER["orbi-core-worker - Task Consumers, LangGraph, Gateway, Memory Mgr"]
        end

        subgraph TB4["TB-04: Trusted Data & Policy Internal Network"]
            FGA["orbi-openfga Container - MOD-09"]
            POSTGRES["orbi-postgres Container - Schemas & PG Search"]
            REDIS_C["orbi-redis Container - AOF Persistent"]
            BLOBS["Local Volume / Blob Store - Traces & Skill Artifacts"]
        end

        subgraph TB5["TB-05: Isolated Execution Network - No Secret/DB Access"]
            PW["orbi-browser Container - MOD-13 Playwright + noVNC Takeover"]
            SB["Future MOD-11-SANDBOX Container - No Net / Ephemeral"]
            COG["Cognee Storage Backends - If Separate per EVAL-03"]
        end
    end

    subgraph TB6["TB-06: Local Windows Workstation - Off-Cloud Zone 3"]
        COMP["MOD-14 Windows Companion Service + agent-desktop + Kill-Switch"]
    end

    EXT_TG -->|"HTTPS"| PROXY
    WEB_USER["Owner Browser"] -->|"HTTPS + Auth"| PROXY
    COMP -->|"Outbound mTLS - Client Cert Pinned"| PROXY

    PROXY --> DASH
    PROXY --> API
    DASH <-->|"Authenticated BFF REST/SSE"| API
    API <--> REDIS_C
    API <--> POSTGRES
    WORKER <--> REDIS_C
    WORKER <--> POSTGRES
    WORKER <--> FGA
    API <--> FGA
    WORKER <--> BLOBS
    WORKER -->|"Strict Typed RPC"| PW
    WORKER -. "Phase 4+ Script RPC" .-> SB
    WORKER <--> COG
    WORKER -->|"Vault-Injected HTTPS"| EXT_SAAS
    PW -->|"Egress-Filtered HTTPS - No RFC1918"| EXT_WEB
```

### A.4 Module Communication Patterns & Trust Boundary Rules

| Boundary Crossing | Transport Mechanism | Authentication / Enforcement |
| :--- | :--- | :--- |
| **`TB-01` -> `TB-02` (Telegram)** | HTTPS Webhook (or outbound polling fallback) | Telegram secret token header + `MOD-01` sender allowlist mapped to OpenFGA `user:<id>` (`ASM-07`). |
| **`TB-02` -> `TB-03` (Dashboard)** | HTTPS REST + WebSocket / SSE via FastAPI BFF | Authenticated session cookie / WebAuthn + CSRF check + OpenFGA owner/member verification. |
| **`TB-03` Internal (`orbi-core`)** | Typed Python `Protocol` calls (in-process) & Redis streams (`API <-> Worker`) | Static/runtime architectural boundary tests (`AP-08`); `MOD-04` has zero import access to `MOD-12` secrets or DB write handles for `tasks`. |
| **`TB-03` -> `TB-04` (OpenFGA & Data)** | Internal Docker bridge network (unexposed to host public IP) | Dedicated DB credentials per schema (`orbi_app`, `orbi_memory`, `openfga`); fail-closed OpenFGA gRPC/HTTP client. |
| **`TB-03` -> `TB-05` (Playwright / Sandbox)** | Internal isolated Docker network (`--internal` for `MOD-11-SANDBOX`) | `MOD-13` container has zero DB, Redis, or secret-vault network routes and blocks RFC1918/link-local SSRF targets. |
| **`TB-06` -> `TB-02/03` (Windows Companion)** | Outbound-initiated persistent WebSocket/gRPC stream over mTLS (`ASM-05`) | Client X.509 device certificate pinned in `MOD-09` + per-command signed envelope + local Windows kill-switch. |

---

## B. Agent Architecture (`MOD-04`, `MOD-05`, `MOD-06`)

`[CONFIRMED]` Orbi separates deterministic routing/orchestration (`MOD-03`, `MOD-05`) from cognitive reasoning (`MOD-04` LangGraph), ensuring agents are declarative identities with strictly bounded capabilities.

### B.1 Central Agent Registry (`MOD-06`) & Agent Profiles

Every agent in Orbi must exist as an active record in `orbi_app.agent_definitions`:
- **Core Fields**: `agent_id` (e.g., `agent:manager`, `agent:email`, `agent:media`, `agent:browser`, `agent:desktop`, `agent:skill_author`), `version`, `status` (`ACTIVE | DEPRECATED | DISABLED`), `role_summary`, `system_prompt_ref`, `model_profile_id`, `max_turns_per_step`, `configured_tool_ids[]`, `configured_skill_ids[]`, and `openfga_principal`.
- **Future Extensible Personas (`AP-10`)**: `agent:iot` (Phase 5 Home Assistant/MQTT) and `agent:developer` (Phase 5 isolated coding workspace).

### B.2 Agent Router, Decision Service (`MOD-05`) & Manager/Planner Agent

1. **Request Triage (`MOD-03` + `MOD-05`)**: When a task starts, `MOD-03` optionally invokes `MOD-05` (**Provider-Independent Decision Service**, [ADR-003](file:///f:/Projects/Orbi/docs/04-adr/ADR-003-provider-independent-decision-and-llm-adapters.md)) to classify complexity (`direct_qa`, `single_agent_tool`, `multi_step_plan`) and urgency (`noul`).
   - `MOD-05` tries deterministic heuristics first, optionally calls **Jev by TypeSafe** (`EVAL-01`) under a strict timeout (`timeout_ms`), and falls back transparently to `LLMDecisionProvider` if Jev is disabled, slow, or low-confidence (`CON-12`).
2. **Simple Requests vs. Complex Multi-Agent Tasks**:
   - **Simple Single-Domain Request**: If triage confidently routes to one specialized agent (e.g., `agent:media` for Spotify playback), `MOD-03` dispatches a bounded turn directly to that agent, avoiding unnecessary Manager planner overhead.
   - **Complex / Cross-Domain Task**: `MOD-03` dispatches to **`ManagerAgent`**, which decomposes the user goal into an explicit `SubGoalPlan`, delegates individual sub-tasks to specialized agents (`EmailAgent`, `BrowserAgent`, `DesktopAgent`), synthesizes intermediate observations, and checks completion criteria.

### B.3 LangGraph Runtime & Provider-Independent Model Adapters

- `MOD-04` executes each bounded turn inside a LangGraph `StateGraph` (`Reason -> SelectToolIntent -> ObserveToolResult -> ConcludeOrYield`).
- **Model Adapter Port (`ADR-003`)**: Graph nodes never call OpenAI/Anthropic/Gemini SDKs directly. They invoke `ModelAdapterPort.generate(messages, authorized_tools, budget_remaining)` which tracks token usage per step and feeds `MOD-03` budget accounting (`OQ-04`).
- **Turn Yield Rules (`AP-03`)**: LangGraph exits a turn and returns control to `MOD-03` whenever: (a) a final answer is ready, (b) `MOD-08` returns `REQUIRES_APPROVAL` or `HUMAN_TAKEOVER_REQUIRED`, (c) clarification from the user is required, or (d) `max_turns_per_step` is reached.

### B.4 Mandatory Initialization & Authorized Tool Discovery (`ADR-002`)

Every time `MOD-04` initializes an agent—whether top-level or delegated by `ManagerAgent`—it enforces the exact 7-step discovery pipeline:
1. Manager (or Router) selects `agent_id` from `MOD-06`.
2. `MOD-04` initializes the agent runtime with `ModelAdapter` and task budget.
3. `MOD-04` fetches `configured_tool_ids` metadata and JSON schemas from `MOD-07`.
4. `MOD-04` invokes `MOD-09` (OpenFGA) to filter candidate tools against `(user_id, household_id, agent_id, instance_id)` permissions (`Stage-1 Filtering`).
5. `MOD-04` binds **only** the authorized tool definitions and schemas into LangGraph's tool-calling schema list.
6. The LLM selects from the authorized list (`ToolCallIntent`).
7. Every invocation passes through `MOD-08` (**Tool Execution Gateway**).

### B.5 Agent-to-Agent Delegation & Context Construction

- **Delegation via Bounded Sub-Invocations**: When `ManagerAgent` delegates a sub-goal to `SpecializedAgent`, the runtime creates a child cognitive turn scoped strictly to the delegated sub-goal. `SpecializedAgent` performs its **own independent 7-step tool discovery** (`MOD-06` -> `MOD-07` -> `MOD-09`); child agents never inherit `ManagerAgent` prompts or unconstrained tool handles.
- **Context Construction & Injection Fencing**:
  - `MOD-04` constructs prompts in separated structural sections: `[1. System Role]`, `[2. Authorized Skill Instructions (SKILL.md)]`, `[3. Provenance-Tagged Memory Context from MOD-10]`, `[4. Authenticated User Request]`, and `[5. Fenced Untrusted Observations]`.
  - Any content tagged `untrusted_external` (emails, web DOMs, UI OCR) is wrapped in explicit data delimiters and flagged as untrusted data, never elevated into system/developer instruction blocks.

#### Diagram 4: Agent Request Lifecycle

```mermaid
flowchart TB
    START["MOD-03 Dispatches Task Turn"] --> TRIAGE["MOD-05 Decision Service - Rules / Optional Jev / LLM Fallback"]
    TRIAGE -->|"Simple Single-Domain"| PICK_SPEC["Select Specialized Agent via MOD-06"]
    TRIAGE -->|"Complex Cross-Domain"| PICK_MGR["Select ManagerAgent via MOD-06"]

    PICK_MGR --> DECOMP["Decompose Goal & Delegate Sub-Goal"]
    DECOMP --> INIT["MOD-04 Initialize Selected Agent"]
    PICK_SPEC --> INIT

    INIT --> MEM["MOD-10 Hybrid Memory Query - Scoped & Authz-Filtered"]
    INIT --> DISC["MOD-07 + MOD-09 Stage-1 OpenFGA Tool & Skill Filtering"]

    MEM --> CTX["Construct Context with Provenance & Injection Fencing"]
    DISC --> CTX

    CTX --> LG["LangGraph Step Execution via ModelAdapter"]
    LG -->|"ToolCallIntent"| GW["MOD-08 Tool Execution Gateway"]
    GW -->|"Sanitized Observation"| LG
    GW -->|"Approval / Takeover Needed"| YIELD_WAIT["Yield to MOD-03 -> Pause Task"]
    LG -->|"Sub-Goal Done -> Manager"| DECOMP
    LG -->|"Final Response / Budget Hit"| DONE["Return TurnOutcome to MOD-03"]
```

---

## C. Tool Platform (`MOD-07`, `MOD-08`, `MOD-09`, `MOD-12`, `MOD-15`)

`[CONFIRMED]` No agent, LangGraph node, or skill script can perform external I/O directly. All capabilities are governed through `MOD-07` and `MOD-08`.

### C.1 Central Tool Registry (`MOD-07`), Schemas & Versioning

Each tool definition in `orbi_app.tool_definitions` stores:
- **Identity & Pinning**: `tool_id` (e.g., `gmail.send_email`), immutable `tool_version` (`1.2.0`), `adapter_type` (`NATIVE_PYTHON | API_SDK | PLAYWRIGHT_RPC | WINDOWS_COMPANION | MCP_CLIENT | SKILL_SCRIPT`), `isolation_zone`.
- **Contracts**: JSON Schema for `input_schema` and `output_schema`.
- **Semantic vs. Volatile Argument Split (`CON-10`)**: Declares which input fields are *semantic action fields* hashed into approval digests (`to`, `subject`, `body`, `target_resource`) versus ephemeral execution metadata (`snapshot_epoch`, `pagination_cursor`).
- **Governance Metadata**: `sensitivity_tier` (`READ_ONLY`, `LOW_RISK_WRITE`, `SENSITIVE_WRITE`, `IRREVERSIBLE`), `idempotent` (`bool`), `timeout_ms`, `rate_limit_bucket`.

### C.2 Tool Execution Gateway (`MOD-08`) & Adapter Architecture (`CON-19`)

`MOD-08` exposes a single `execute_tool_intent()` pipeline enforcing seven deterministic gates in sequence:
1. **Existence & Schema Gate**: Reject unknown `tool_id` (preventing invented tool IDs) and validate arguments strictly against `MOD-07` JSON Schema.
2. **Idempotency Gate**: Check `(task_id, step_id, idempotency_key)` in Redis/PostgreSQL to prevent duplicate side effects on worker retry.
3. **Stage-2 OpenFGA Gate (`MOD-09`)**: Query `Check(user_id, household_id, agent_id, execute, tool_id, target_resource)` and retrieve current `policy_version`. Fail closed if OpenFGA is unreachable (`NFR-REL-02`).
4. **Sensitivity & Approval Gate (`MOD-03` / `ADR-004`)**: If `sensitivity_tier` is `SENSITIVE_WRITE` or `IRREVERSIBLE` (or OpenFGA policy / `MOD-05` risk signal escalates the call), check for a valid consumed-pending `ApprovalRequest` matching the exact canonical payload digest. If absent, trigger `MOD-03` pause (`AWAITING_APPROVAL`).
5. **Credential Injection**: Fetch encrypted OAuth2 tokens or API keys from the OCI Secret Store inside `MOD-08` / `MOD-12`—never visible to `MOD-04`.
6. **Typed Adapter Dispatch (`CON-19`)**: Route to the registered adapter implementation:
   - **Native Python Adapter**: Deterministic, pure in-process utilities (date math, safe formatting, local registry queries).
   - **API / SDK Adapter (`MOD-12`)**: Typed HTTP/SDK wrappers for Gmail, Outlook, Spotify, etc., with token refresh and rate limiting.
   - **MCP Client Adapter (`[PROPOSED]`)**: Connects strictly from behind `MOD-08` to external/local MCP servers. Discovered MCP tools must be explicitly imported, schema-pinned, and assigned a `sensitivity_tier` in `MOD-07` before any agent can see or invoke them—agents **never** hold raw MCP client sessions.
   - **Isolated Harness Dispatch**: RPC to `MOD-13` (Playwright container), `MOD-11-SANDBOX` (future script sandbox), or mTLS command to `MOD-14` (Windows Companion).
7. **Sanitization, Provenance Tagging & Audit (`MOD-15`)**: Strip secret regex patterns, enforce payload size caps, attach `untrusted_external` provenance tags to external responses, and write an immutable audit record (`FR-AU-01`).

#### Diagram 5: Tool Discovery & Execution Pipeline

```mermaid
flowchart LR
    subgraph ST1["Stage 1: Discovery - MOD-04 / MOD-07 / MOD-09"]
        A1["Agent Configured Tools - MOD-07"] --> A2["OpenFGA Filter by Agent, User & Instance - MOD-09"]
        A2 --> A3["Authorized Tool Schemas Bound to LangGraph"]
    end

    subgraph ST2["Stage 2: Execution Chokepoint - MOD-08 Gateway"]
        G1["1. Validate Tool ID & JSON Schema"] --> G2["2. Check Idempotency Key"]
        G2 --> G3["3. Stage-2 OpenFGA Check on Target Resource"]
        G3 -->|"Denied / Error"| FAIL["Fail-Closed & Audit MOD-15"]
        G3 -->|"Allowed"| G4{"4. Approval Required?"}
        G4 -->|"Yes & Missing/Changed"| PAUSE["Suspend via MOD-03 AWAITING_APPROVAL"]
        G4 -->|"No or Revalidated Digest Match"| G5["5. Inject Secrets & Dispatch Adapter"]
        G5 --> ADAPTERS["Native Python / MOD-12 API / MCP Client / MOD-13 / MOD-14"]
        ADAPTERS --> G6["6. Sanitize, Tag Provenance & Audit MOD-15"]
    end

    A3 -->|"LLM ToolCallIntent"| G1
```

---

## D. Task Orchestration (`MOD-03`, `MOD-04`, PostgreSQL & Redis)

`[CONFIRMED]` `MOD-03` (**Deterministic Task Manager**) is the authoritative owner of macro-task state (`AP-03`).

### D.1 Task Lifecycle & Authoritative State Store

All durable task data lives in PostgreSQL (`orbi_app.tasks`, `task_steps`, `task_budgets`, `approval_requests`):
- **States**: `CREATED` -> `QUEUED` -> `RUNNING` -> `{AWAITING_APPROVAL | AWAITING_HUMAN_TAKEOVER}` -> `RUNNING` -> `{COMPLETED | FAILED | CANCELLED | EXPIRED}`.
- **Budget Enforcement (`FR-TM-04`)**: Every task carries hard caps (`max_steps`, `max_llm_tokens`, `max_cost_usd`, `task_ttl_seconds`). `MOD-03` increments counters deterministically after every `MOD-04` turn and transitions to `FAILED` (`BUDGET_EXCEEDED`) or `EXPIRED` on breach.

#### Diagram 7: Task Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> CREATED : User Command Verified - MOD-01/02
    CREATED --> QUEUED : Persist Task & Enqueue in Redis
    QUEUED --> RUNNING : Worker Acquires Lease
    RUNNING --> RUNNING : Bounded Turn Complete - Next Step
    RUNNING --> AWAITING_APPROVAL : MOD-08 Action-Bound Approval Needed
    RUNNING --> AWAITING_HUMAN_TAKEOVER : MOD-13 OTP / CAPTCHA Detected
    AWAITING_APPROVAL --> QUEUED : Valid Approval Granted - Re-enqueue
    AWAITING_HUMAN_TAKEOVER --> QUEUED : User Completes Challenge
    AWAITING_APPROVAL --> CANCELLED : User Rejects or Cancels
    AWAITING_APPROVAL --> EXPIRED : Approval TTL Elapsed
    AWAITING_HUMAN_TAKEOVER --> EXPIRED : Takeover TTL Elapsed
    RUNNING --> COMPLETED : Final Response Produced
    RUNNING --> FAILED : Unrecoverable Error / Budget Exceeded
    QUEUED --> CANCELLED : User Cancels
    RUNNING --> CANCELLED : Cancellation Signal Acknowledged
    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
    EXPIRED --> [*]
```

### D.2 PostgreSQL Authoritative State vs. Scoped LangGraph Checkpoints (`CON-18`, `EVAL-04`)

- **`orbi_app.tasks` (Source of Truth)**: Authorizes whether a task is runnable, tracks step budgets, owns `AWAITING_APPROVAL` and `AWAITING_HUMAN_TAKEOVER` suspensions, and records curated step summaries.
- **`langgraph_checkpoints` (Subordinate Intra-Turn State)**: Stores LangGraph graph-node scratchpad state **only** for the duration of an active turn or structured pause resumption (`EVAL-04`). LangGraph checkpoints **never** hold approval authority, task status, or retry budgets, and cannot resume execution unless `MOD-03` explicitly transitions the authoritative task to `RUNNING` and dispatches the turn.

### D.3 Redis Responsibilities, Retries, Cancellation & Recovery

- **Redis Roles**:
  1. *Durable Dispatch Queues*: Priority Redis lists/streams (`queue:tasks:interactive`, `queue:tasks:background`, `queue:memory:consolidation`).
  2. *Worker Leases & Heartbeats*: Short-TTL keys (`lease:task:<id>`) preventing split execution across workers.
  3. *Cancellation & Wakeup Signals*: Pub/Sub channel (`chan:task:cancel:<id>`) checked between LangGraph steps and before `MOD-08` dispatches.
  4. *Companion Presence*: Live heartbeat metadata (`presence:companion:<device_id>`) for `MOD-14`.
- **Retry & Recovery Rules**:
  - *Transient I/O*: `MOD-08` retries `READ_ONLY` and declared `idempotent=true` calls with jittered exponential backoff; non-idempotent failed writes halt cleanly and notify the user rather than blindly re-executing.
  - *Worker Crash Recovery*: On container restart or lease expiration, a background reconciler scans PostgreSQL `orbi_app.tasks WHERE status IN ('QUEUED', 'RUNNING')` with stale heartbeats and safely re-queues or transitions them—guaranteeing zero lost tasks (`NFR-REL-01`).

---

## E. Identity, Authorization & Approvals (`MOD-01`, `MOD-02`, `MOD-03`, `MOD-08`, `MOD-09`)

### E.1 User & Household Identity Model (`ADR-002`)

`[CONFIRMED]` Orbi runs single-owner initially while establishing normalized identity tables and OpenFGA tuples designed for seamless future household expansion:
- **`user:<id>`**: Authenticated human identity (`orbi_app.users`).
- **`household:<id>`**: Shared group entity (`orbi_app.households`) linking owner and future family members (`member`, `admin` relations).
- **`agent:<id>`** & **`instance:<id>`**: Delegated cognitive principal and runtime deployment/companion scope.

### E.2 Channel Identity Mapping & Dashboard Authentication

1. **Telegram Identity Mapping (`MOD-01`, `ASM-07`)**:
   - Telegram updates carry numeric `from.id` and `chat.id`. `MOD-01` resolves `(telegram_user_id, chat_id)` against `orbi_app.channel_bindings` (`status = ACTIVE`). Unbound or suspended Telegram IDs are rejected before creating tasks and logged to `MOD-15`.
   - Group chats (if enabled later) require explicit `household:<id>` chat registration and per-sender `user:<id>` verification so untrusted third parties in a chat cannot hijack owner permissions.
2. **Dashboard Authentication (`MOD-02`, `[PROPOSED]`)**:
   - Browser authentication uses secure operator credentials (Passkey/WebAuthn or password + TOTP MFA) backed by `HttpOnly`, `Secure`, `SameSite=Lax` session cookies, strict CSRF tokens, and short idle timeouts.
   - Sensitive governance actions (creating OpenFGA tuples, approving `IRREVERSIBLE` tasks, publishing skills, launching browser Human Takeover) require recent MFA/re-auth verification.

### E.3 OpenFGA Relationship Model (`MOD-09`)

`[CONFIRMED]` / `[PROPOSED]` `orbi-openfga` stores the authoritative authorization model (`policy_version` tracked monotonically on schema/policy updates):
- **`type household`**: `relations: admin, member`
- **`type user`**: Identity principal
- **`type instance`**: `relations: household, operator` (identifies OCI core or a specific companion machine)
- **`type agent`**: `relations: owner, allowed_user, configured_tool`
- **`type tool`**: `relations: owner, allowed_agent, allowed_user`
- **`type skill`**: `relations: owner, published_reader, invoker` (note: skills never grant tool permissions; tools remain independently checked)
- **`type resource`**: `relations: owner, household, reader, writer, approver` (covers email accounts, Spotify sessions, browser profiles, Windows Companion devices, future IoT devices)
- **`type memory_unit`**: `relations: owner, household, reader` (private `owner` vs. `member from household` per [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md))
- **`type approval_request`**: `relations: owner, approver` (`approver` restricted strictly to resource/task owner or delegated household admin)

### E.4 Cryptographically Protected, Action-Bound Approvals (`ADR-004`)

`[CONFIRMED]` Human approvals protect against TOCTOU bait-and-switch attacks (`CON-10`):
1. **Canonical Digest Construction**: When `MOD-08` blocks a `SENSITIVE_WRITE` or `IRREVERSIBLE` call (or policy-escalated action), it constructs a canonical JSON structure containing:
   - `user_id` (authorized approver)
   - `task_id`
   - `tool_id` and `tool_version`
   - `canonical_arguments_hash` (`SHA-256` of sorted, normalized semantic fields per `MOD-07`, excluding volatile epoch tokens per `CON-10`)
   - `target_resource`
   - `policy_version` (current OpenFGA model/policy version)
   - `expires_at` (UTC timestamp)
2. **Integrity Signature (`[PROPOSED]`)**: `MOD-03` computes `binding_digest = SHA-256(canonical_payload)` and an internal `HMAC-SHA256(approval_hmac_key, approval_id || binding_digest)` stored in `orbi_app.approval_requests`.
3. **One-Time Consumption & Replay Protection**:
   - Approval buttons in Telegram (`MOD-01`) or Dashboard (`MOD-02`) submit `(approval_id, actor_user_id, expected_digest)`.
   - `MOD-03` executes an atomic PostgreSQL compare-and-swap (`UPDATE ... SET status = 'APPROVED', decided_at = now() WHERE approval_id = $1 AND status = 'PENDING' AND expires_at > now()`), preventing duplicate clicks across Telegram and Dashboard (`CON-06`).
4. **Immediate Pre-Execution Revalidation (`MOD-08`)**:
   - When `MOD-03` resumes the task, `MOD-08` **immediately revalidates** prior to adapter dispatch:
     1. Recompute `binding_digest` from the live `ToolCallIntent` about to execute and verify equality + HMAC;
     2. Verify `now() < expires_at`;
     3. Query `MOD-09` for live `policy_version` and verify OpenFGA still returns `ALLOW`;
     4. Atomically transition `status = 'APPROVED'` -> `'CONSUMED'` (`single-use token`).
   - **Any mismatch, expiration, or policy-version bump** rejects execution (`APPROVAL_INVALIDATED`), logs to `MOD-15`, and initiates a fresh approval request.

#### Diagram 6: Approval Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING : MOD-08 Triggers Pause - Compute Digest & HMAC
    PENDING --> APPROVED : Authorized Human Grants via Telegram or Dashboard - CAS Lock
    PENDING --> REJECTED : Human Denies Action
    PENDING --> EXPIRED : Approval TTL Elapsed
    PENDING --> INVALIDATED : OpenFGA Policy Version Changed or Task Cancelled
    APPROVED --> CONSUMED : MOD-08 Pre-Execution Revalidation Passes - Single Use
    APPROVED --> INVALIDATED : Digest Mismatch / Policy Drift / Expired Before Exec
    CONSUMED --> [*]
    REJECTED --> [*]
    EXPIRED --> [*]
    INVALIDATED --> [*]
```

---

## F. Memory Architecture Integration (`MOD-10` — Referencing `DOC-HLD-001`)

`[CONFIRMED]` Detailed memory internals, retrieval formulas, data contracts, and evaluation plans reside in [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md) (`DOC-HLD-001`). At the overall system level, Orbi integrates with the Memory Manager as follows:

1. **System Integration Points**:
   - **`MOD-04` (Read Path)**: During context assembly (`Section B.5`), agents call `MOD-10.retrieve_hybrid_memory(query, user_id, household_id, budget)`. `MOD-10` runs its 5-stage pipeline—(1) Query Understanding without mandatory LLMs, (2) parallel Vector / BM25 `[PROPOSED]` / Graph / selective Temporal branches, (3) **mandatory per-branch OpenFGA resource checks**, (4) deduplicating RRF `[PROPOSED]` fusion, and (5) configurable replaceable reranking `[PROPOSED]`—returning provenance-tagged items.
   - **`MOD-03` + `MOD-10` (Write & Consolidation Path)**: Post-turn or via Redis `queue:memory:consolidation` workers, candidate facts are written first to PostgreSQL `orbi_memory` (authoritative `MemoryUnit` catalog) with `source_provenance`, `trust_tier` (`user_explicit`, `system_verified`, or `untrusted_external`), and bi-temporal markers (`event_time`, `valid_from`, `valid_to`, `superseded_by`). `MOD-10` then asynchronously triggers `CogneeAdapter` ECL pipelines (`CON-14`).
   - **`MOD-02` (Dashboard Curation)**: Authorized users search, inspect provenance/graph links, approve/promote quarantined `untrusted_external` memories, or delete memories (`MOD-10` cascades deletions across `orbi_memory`, Cognee stores, and OpenFGA tuples).
2. **Cognee Boundary & Minimal Infrastructure (`ADR-007`, `ADR-008`)**:
   - Cognee sits strictly behind `CogneeAdapter` inside `MOD-10`. Capabilities not verified or missing in Cognee during `EVAL-03` (such as BM25 search evaluated first via PostgreSQL FTS/BM25 in `EVAL-07`, and bi-temporal validity) are owned directly by `MOD-10` over `orbi_memory`.
3. **Security & Provenance Invariants**:
   - OpenFGA (`MOD-09`) constrains **every retrieval branch** (`private_user`, `shared_household`, custom scopes) prior to Candidate Fusion or Reranking.
   - `untrusted_external` facts cannot automatically supersede `user_explicit` facts (`CON-16`).

#### Diagram 8: Memory Retrieval & Consolidation Integration

```mermaid
flowchart LR
    M04["MOD-04 Agent Runtime"] -->|"Scoped Query"| M10["MOD-10 Memory Manager"]
    M03["MOD-03 Background Worker"] -->|"Post-Turn Fact Extraction"| WRITE["Write Path: orbi_memory + Quarantine Check"]

    subgraph HLD1["Memory Manager Internal Pipeline - See DOC-HLD-001"]
        QU["Query Understanding"] --> BR["Parallel Branches: Vector / BM25* / Graph / Temporal"]
        BR --> FGA["Per-Branch OpenFGA Pre-Fusion Filter - MOD-09"]
        FGA --> FUS["Candidate Fusion RRF* + Reranker*"]
    end

    M10 --> QU
    BR <--> PG_MEM["PostgreSQL orbi_memory - Catalog, Temporal, PG Search*"]
    BR <--> COG["CogneeAdapter -> Vector & Graph Backends - EVAL-03"]
    WRITE --> PG_MEM
    WRITE -->|"Trusted / Curated Sync"| COG
    FUS -->|"Provenance-Tagged Context"| M04
    M02["MOD-02 Dashboard"] <-->|"Inspect, Promote Quarantine, Delete"| M10
```
*(\* Proposed / Evaluation Required under `EVAL-03`, `EVAL-07`)*

---

## G. Skill Lifecycle Manager (`MOD-11`, `ADR-005`, `CON-17`)

`[CONFIRMED]` Reusable procedural skills are strictly separated from factual knowledge memory (`AP-04`) and are **never** allowed to grant permissions or bypass `MOD-08` (`AP-01`, `AP-02`).

### G.1 Skill Registry & Package Structure (`SKILL.md`)

`orbi_app.skill_packages` and `skill_versions` store immutable, semantically versioned packages centered around a **`SKILL.md`** file:
- **YAML Frontmatter**: `skill_id`, `version` (`semver`), `title`, `author_actor`, `required_tools[]` (must match `MOD-07` entries), `approval_policy_notes`, `package_mode` (`MARKDOWN_ONLY` initially vs. `WITH_SANDBOXED_SCRIPTS` future).
- **Markdown Body**: Structured workflow instructions, edge-case handling, and composition guidance over existing Gateway tools.
- **Optional Package Companion Artifacts**: `examples/`, JSON `schemas/`, and deterministic `tests/` (golden input/expected-intent fixtures).

### G.2 Skill Discovery, Loading & Creation/Evaluation Flow

1. **Discovery & Loading**: During agent initialization (`Section B.4`), `MOD-04` queries `MOD-11` for `PUBLISHED` skills allowed by OpenFGA (`MOD-09`) and whose `required_tools` are a **subset** of the agent's Stage-1 authorized tools. `SKILL.md` instructions load strictly as procedural guidance; loading a skill adds **zero** new tool authorizations.
2. **Creation & Automated Evaluation**:
   - Either the human owner (in `MOD-02`) or `SkillAuthorAgent` drafts a `DRAFT` skill package.
   - `MOD-11` executes the package's `tests/` fixtures in dry-run mode against mocked `MOD-08` Gateway tools (`EVALUATED`), verifying schema validity, zero unauthorized tool references, and deterministic output assertions.
3. **Human Approval & Versioning**:
   - Passing skills enter `AWAITING_PUBLISH_APPROVAL` and appear in `MOD-02` Dashboard with full Markdown diff, fixture pass report, and required tool list.
   - Explicit owner approval promotes the immutable version (`PUBLISHED`, e.g., `skill:weekly_expense_digest@1.0.0`), registers any declarative trigger entry in `MOD-07`, and records the event in `MOD-15`.

### G.3 Initial Markdown-Only Scope vs. Future Sandboxed Scripts (`CON-17`)

- **Initial Implementation (Phase 4 Baseline)**: Skills operate strictly in **`MARKDOWN_ONLY` mode**—`SKILL.md` procedural instructions + JSON schemas + fixture tests orchestrating existing `MOD-07`/`MOD-08` tools. Any package attempting to declare executable script artifacts is rejected by schema policy until sandbox validation completes.
- **Future Capability (Post-`EVAL-06`)**: Optional supporting executable scripts inside a skill package will run strictly inside **`MOD-11-SANDBOX`** (Zone 2a isolated container/microVM, `OQ-11`), with zero ambient credentials, no host filesystem access, and Gateway-only callbacks to `MOD-08`.

#### Diagram 11: Skill Lifecycle

```mermaid
stateDiagram-v2
    [*] --> DRAFT : Human or SkillAuthorAgent Authors SKILL.md Package
    DRAFT --> EVALUATED : Run Golden Fixture & Schema Eval in MOD-11
    DRAFT --> FAILED_EVAL : Fixture / Policy Check Fails
    FAILED_EVAL --> DRAFT : Revise Package
    EVALUATED --> AWAITING_PUBLISH_APPROVAL : Submit for Owner Review in MOD-02
    AWAITING_PUBLISH_APPROVAL --> PUBLISHED : Owner Approves - Pin Semver & Audit MOD-15
    AWAITING_PUBLISH_APPROVAL --> REJECTED : Owner Denies Publication
    PUBLISHED --> DEPRECATED : Superseded by Newer Pinned Version or Retired
    REJECTED --> [*]
    DEPRECATED --> [*]
```

---

## H. Cloud Browser Execution (`MOD-13`)

`[CONFIRMED]` For web workflows lacking SaaS APIs, `MOD-08` dispatches structured browser intents to `orbi-browser` (`MOD-13`), a dedicated container running **Playwright + Chromium** inside Isolated Execution Zone 2a (`TB-05`).

### H.1 Isolation, Session Lifecycle & Cloud vs. Local Boundary

1. **Container & Network Isolation**:
   - `orbi-browser` runs non-root with strict CPU/RAM cgroup caps (`EVAL-05`), zero DB/Redis/Vault credentials, and egress filtering blocking private/link-local/metadata IP ranges (SSRF guard against `169.254.169.254` and Docker internal networks).
2. **Browser Session Lifecycle**:
   - **`EPHEMERAL_CLEAN`** *(Default)*: Fresh incognito `BrowserContext` created per task and destroyed on completion.
   - **`PERSISTENT_ENCRYPTED_PROFILE`** *(Opt-in per authorized site)*: Encrypted storage-state blob saved via `BlobStoragePort` and bound to `(user_id, site_domain)` in OpenFGA (`MOD-09`), decrypted transiently into `orbi-browser` only during an authorized session.
3. **Cloud Browser vs. Local Browser (`MOD-14`)**:
   - *Use Cloud Browser (`MOD-13`)* for public web research, headless form automation, and isolated web accounts without touching the user's workstation.
   - *Use Local Windows Browser (`MOD-14`)* only when the site explicitly requires local intranet access, hardware security keys attached to the PC, or pre-authenticated local browser profiles—always governed by `MOD-14` local policies and `MOD-09`.

### H.2 Human Takeover, OTP/CAPTCHA Handling & Sensitive Approvals (`CON-20`)

- **Strict Anti-Bypass Policy**: Orbi **never** solves, computes, or automatically bypasses CAPTCHAs, Cloudflare Turnstile, or anti-bot gates.
- **OTP & CAPTCHA Human Takeover Flow**:
  1. When `MOD-13` encounters a login challenge, 2FA/OTP prompt, or CAPTCHA, it returns `HUMAN_TAKEOVER_REQUIRED(reason, screenshot_redacted, session_id)` to `MOD-08`/`MOD-03`.
  2. `MOD-03` transitions the task to `AWAITING_HUMAN_TAKEOVER` and alerts the owner via Telegram (`MOD-01`) and Dashboard (`MOD-02`).
  3. **Takeover Options**:
     - For numeric OTPs: owner enters the one-time code via secure Dashboard prompt (never logged in plain audit).
     - For interactive CAPTCHAs/logins: owner opens the authenticated **Browser Sessions** tab in `MOD-02`, which proxies a time-limited, OpenFGA-authorized **noVNC / WebRTC interactive view** directly to the paused `orbi-browser` context (`CON-20`). Once complete, the owner clicks *Return Control to Orbi*, and `MOD-03` re-queues the task.
- **Approval & DOM Verification for Sensitive Web Actions**: Browser tools (`browser.click_submit`, `browser.purchase`, `browser.delete_account`) carry `SENSITIVE_WRITE` / `IRREVERSIBLE` tiers. Before clicking, `MOD-08` captures the target element label, target URL domain, and action summary into an action-bound approval (`ADR-004`) and verifies the page domain/element has not drifted upon resume.
- **Failure Recovery**: On page crash or timeout, `MOD-13` captures DOM/screenshot error artifacts to `BlobStoragePort`, tears down the hung context, and returns a clean structured error to `MOD-08`.

#### Diagram 9: Cloud Browser Execution & Human Takeover Lifecycle

```mermaid
flowchart TB
    GW["MOD-08 Gateway"] -->|"Authorized Browser Intent"| PW["MOD-13 Playwright Container - Zone 2a"]
    PW -->|"Public / Authenticated Flow"| DOM["Execute Action & Extract Sanitized DOM"]
    DOM -->|"Untrusted Content Tagged"| GW

    PW -->|"Detects OTP or CAPTCHA Gate"| PAUSE["Return HUMAN_TAKEOVER_REQUIRED -> MOD-03"]
    PAUSE --> WAIT["Task State: AWAITING_HUMAN_TAKEOVER"]
    WAIT -->|"Notify Telegram + MOD-02"| USER["Owner Opens Dashboard Browser Session - Authenticated noVNC / OTP"]
    USER -->|"Completes Challenge Manually"| RESUME["Owner Hands Control Back -> MOD-03 Re-queues"]
    RESUME --> PW

    DOM -->|"Next Step is Submit / Checkout / Delete"| HITL["MOD-08 Triggers Action-Bound Approval - ADR-004"]
    HITL -->|"Approved & DOM Verified"| PW
```

---

## I. Windows Local Companion (`MOD-14`, `ADR-006`)

`[CONFIRMED]` `MOD-14` extends Orbi onto the user's personal Windows 10/11 PC while enforcing strict cloud-to-local sovereignty (`AP-05`).

### I.1 Companion Architecture, Outbound Connection & Heartbeat

1. **Daemon Architecture**: Lightweight local background service + minimal system-tray status/kill-switch applet running under the active Windows user desktop session (required for UI Automation access).
2. **Outbound Authenticated mTLS Connection (`ASM-04`, `ASM-05`)**:
   - The Windows Companion **never** opens inbound internet ports. At startup, it initiates an outbound persistent **WebSocket/gRPC over mTLS** connection to the OCI Gateway Relay (`orbi-core-api`).
   - Authentication uses a per-device X.509 client certificate registered in `orbi_app.companion_devices` and bound to `instance:<device_id>` in OpenFGA (`MOD-09`).
3. **Heartbeat & Reconnect**:
   - `MOD-14` sends signed `HEARTBEAT` frames every 15 seconds; `MOD-08`/`orbi-core-api` updates Redis (`presence:companion:<device_id>`, TTL 45s) with active capabilities (`native_apis`, `agent_desktop_uia`, `vision_fallback`) and kill-switch status.
   - On network drop or laptop sleep/wake, `MOD-14` reconnects using jittered exponential backoff and invalidates any pre-sleep `snapshot_id` epochs (`CON-05`).

### I.2 Command Dispatch, Acknowledgement & Layered Execution (`Decision 9`)

1. **Dispatch & Ack Protocol**:
   - `MOD-08` sends a signed `CompanionCommand(command_id, task_id, idempotency_key, action, args, snapshot_epoch, approval_token_id)` over the mTLS stream.
   - `MOD-14` immediately returns `CMD_ACK` (received), executes locally under timeout, and streams `CMD_RESULT` (`status`, `tier_used`, `confidence`, redacted observation).
2. **Strict 3-Tier Execution Precedence (`ADR-006`)**:
   - **Tier 1 — Native OS / App APIs**: Attempted first via explicitly allowlisted local adapters (e.g., approved file read/move inside permitted workspace paths, Windows toast/clipboard API, COM/app CLI).
   - **Tier 2 — UI Automation (`vercel-labs/agent-desktop`, `EVAL-02`)**: When no Tier 1 adapter exists, invoke `agent-desktop` to inspect the Windows UIA tree as structured JSON and perform `ref`-based clicks/inputs bound to a short-lived `snapshot_id` epoch (`CON-05`).
   - **Tier 3 — Controlled Vision Fallback**: Permitted **only** when Tier 1 and Tier 2 return empty/unresolvable UIA nodes (e.g., custom canvas/Electron views). Captures **only** the allowlisted target window, applies local sensitive-region masking before any cloud vision grounding call, and binds actions to `(window_identity, target_label, perceptual_hash)` (`CON-09`).
3. **Safe Confidence Stop & Uniform Policy**:
   - If UIA matching or Vision grounding confidence falls below configured thresholds (`EVAL-02`, `OQ-12`), `MOD-14` aborts cleanly (`STOP_INSUFFICIENT_CONFIDENCE`) with zero clicks/keystrokes.
   - Sensitive operations require Stage-2 OpenFGA authorization + action-bound approval (`ADR-004`) **regardless** of whether Tier 1, Tier 2, or Tier 3 executes them.

### I.3 Local Filesystem, Process Restrictions & Offline Behavior

- **Local Hard Guardrails (`MOD-14` Local Config — Unoverridable by Cloud)**:
  - *Process & Window Denylist*: Password managers (e.g., 1Password, Bitwarden, KeePass), Windows UAC/Credential prompts, `regedit`, unrestricted `cmd.exe`/`powershell.exe`, banking portals, and `.ssh`/browser-vault directories are blocked locally.
  - *Filesystem Allowlist*: Tier 1 file operations are restricted strictly to configured user workspace directories.
  - *Instant Kill-Switch*: System-tray click or global hotkey immediately halts active execution, rejects incoming cloud commands (`LOCAL_KILL_SWITCH_ACTIVE`), and notifies `MOD-08`.
- **Laptop Unavailable / Offline Behavior**:
  - `MOD-08` checks Redis `presence:companion:<device_id>` before dispatching.
  - For interactive/real-time desktop commands, `MOD-08` fails fast with `COMPANION_OFFLINE` so `MOD-03` notifies the user immediately without blocking workers.
  - Optionally (for deferred tasks explicitly flagged `allow_queue_until_online=True`), `MOD-03` may hold the task with a bounded TTL and re-dispatch upon `HEARTBEAT` reconnection—mandatory **pre-execution approval & snapshot revalidation** (`ADR-004`) always runs before execution.

#### Diagram 10: Windows Companion Architecture & Execution Flow

```mermaid
flowchart TB
    GW["MOD-08 Tool Execution Gateway - OCI"] <-->|"Outbound mTLS Tunnel + 15s Heartbeat"| RELAY["MOD-14 Companion Relay & Policy Gate"]
    RELAY -->|"Kill-Switch Active or Blocked App/Path"| DENY["Reject: LOCAL_POLICY_BLOCK / KILL_SWITCH"]
    RELAY -->|"Allowed Target"| T1["Tier 1: Native OS / Allowlisted App API"]
    T1 -->|"Success"| RES["Redact & Return Result to MOD-08"]
    T1 -->|"Not Supported"| T2["Tier 2: UIA via agent-desktop - EVAL-02"]
    T2 -->|"High Confidence & Valid Epoch"| RES
    T2 -->|"Empty UIA / Canvas"| T3["Tier 3: Controlled Vision Fallback - Cropped & Masked"]
    T3 -->|"Confidence >= Threshold & State Verified"| RES
    T3 -->|"Confidence < Threshold or Drift"| STOP["Abort Safely: STOP_INSUFFICIENT_CONFIDENCE"]
```

---

## J. External Integrations (`MOD-01`, `MOD-12`, `MOD-16-EXT`)

`[CONFIRMED]` Orbi strictly distinguishes **initial implementation scope** (Phases 1–4) from **future architectural extension boundaries** (Phase 5+, `AP-10`):

| Integration | Scope | Adapter / Module | Boundary & Governance Pattern |
| :--- | :--- | :--- | :--- |
| **Telegram** | **Initial (Phase 1)** | `MOD-01` Channel Gateway | Webhook/polling ingress mapped to verified `user:<id>`/`household:<id>` (`ASM-07`); delivers summaries and interactive action-bound approval prompts. |
| **Gmail / Outlook** | **Initial (Phase 2)** | `MOD-12` SaaS Connectors | OAuth2 refresh tokens isolated in OCI Secret Vault, injected solely in `MOD-08`; read operations `READ_ONLY`, send/delete/forward `SENSITIVE_WRITE`/`IRREVERSIBLE` (`OQ-08`); email bodies tagged `untrusted_external`. |
| **Spotify** | **Initial (Phase 2)** | `MOD-12` SaaS Connectors | Scoped OAuth2 connector behind `MOD-08`; playback/search `READ_ONLY`/`LOW_RISK_WRITE`, account/playlist deletion higher tier. |
| **Other SaaS Connectors & MCP Tools** | **Extensible (Phase 2–4)** | `MOD-12` / `MOD-08` MCP Adapter | Plugged behind typed `SaaSConnectorPort` or `MOD-08` MCP Client Adapter (`CON-19`), cataloged and risk-tiered in `MOD-07`. |
| **Home Assistant / MQTT & ESP32 (`MOD-16-EXT`)** | **Future (Phase 5 — Boundary Only)** | `MOD-16-EXT` IoT Bridge | Interface slots preserved in `MOD-07` (`FUTURE_IOT`), `MOD-08`, and `MOD-09` (`iot_device`, `household` ownership). Two patterns preserved without premature lock-in: (a) local Home Assistant/MQTT bridge over outbound mTLS tunnel (reusing `ASM-05`), or (b) authenticated Home Assistant REST/WebSocket endpoint. High-impact physical actions (door locks, alarms, heating overrides, ESP32 relays) map to `SENSITIVE_WRITE`/`IRREVERSIBLE`. |
| **Future Developer Agent** | **Future (Phase 5 — Boundary Only)** | `MOD-06` (`agent:developer`) + Isolated Workspace Sandbox | Planned specialized agent operating strictly inside an isolated cloud container workspace (separate git worktree/container, zero host credentials) behind `MOD-08`, with `git push`, PR merge, and production deploys gated by `ADR-004` approvals. |

---

## K. Dashboard (`MOD-02`, `OQ-01`)

### K.1 Frontend / Backend Interaction

- **Backend-for-Frontend (BFF)**: FastAPI endpoints inside `orbi-core-api` expose authenticated REST APIs (`/api/v1/...`) and real-time Server-Sent Events (SSE) / WebSocket streams (`/api/v1/stream`) backed directly by `MOD-03`, `MOD-06`, `MOD-07`, `MOD-09`, `MOD-10`, `MOD-11`, `MOD-13`, and `MOD-15`.
- **Frontend Stack (`OQ-01` Open for HLD Finalization)**: `orbi-dashboard` runs as an isolated container (`Zone 2b`) communicating solely via HTTPS/WSS to `orbi-core-api` BFF—never touching PostgreSQL, Redis, or OpenFGA directly. *(Framework alternatives and recommendation documented in Section O.)*

### K.2 Dashboard Functional Workspaces

| Workspace | Backing Modules | Core Capabilities |
| :--- | :--- | :--- |
| **1. Members & Access** | `MOD-01`, `MOD-09` | Manage primary owner and future household members, Telegram/email channel bindings (`ASM-07`), paired Windows Companion certificates, and inspect/edit OpenFGA relationship tuples (`policy_version` audited). |
| **2. Agents & Tools** | `MOD-06`, `MOD-07`, `MOD-09` | View Central Agent Registry definitions, model/budget bindings, Tool Registry JSON schemas, sensitivity tiers (`READ_ONLY`–`IRREVERSIBLE`), and test effective Stage-1 OpenFGA permissions per `(user, agent)`. |
| **3. Memory Management** | `MOD-10` | Hybrid search debugger (`QueryPlan`, active branches, RRF/rerank scores), knowledge graph inspector, provenance/trust filter, quarantine review queue (promote/reject `untrusted_external` facts), bi-temporal history, and manual edit/purge (`DOC-HLD-001`). |
| **4. Tasks & Monitoring** | `MOD-03`, `MOD-04`, `MOD-05` | Live task list (`RUNNING`, `AWAITING_APPROVAL`, `AWAITING_HUMAN_TAKEOVER`, `FAILED`), LangGraph step traces, Decision Service provider/fallback logs, token/cost budget meters, and one-click task cancellation. |
| **5. Approvals & Audit** | `MOD-03`, `MOD-08`, `MOD-15` | Full-context queue of pending action-bound approvals displaying canonical JSON arguments, target resource, SHA-256 `binding_digest`, `policy_version`, and expiration countdown; searchable append-only `MOD-15` audit explorer. |
| **6. Browser Sessions** | `MOD-13`, `MOD-03` | Monitor active `orbi-browser` Playwright sessions, domain allowlist status, error trace screenshots, and launch time-bounded authenticated **Human Takeover** (noVNC/WebRTC or OTP entry) for paused tasks (`Section H.2`). |
| **7. System Settings** | `MOD-04`, `MOD-05`, `MOD-12`, `MOD-14` | Configure provider-independent `ModelAdapter` profiles, per-agent/daily cost caps, `MOD-05` Decision Service fallback thresholds (and optional Jev toggle), SaaS OAuth connection status, and Windows Companion live heartbeat / remote disconnect. |
| **8. Developer Workspace** | `MOD-11`, `MOD-10`, Future `DevAgent` | Author/diff `SKILL.md` packages, run golden fixture evaluations (`EVALUATED`), approve skill publication (`PUBLISHED`), execute the `EVAL-07` hybrid retrieval benchmark suite, and host the future Developer Agent console (`Phase 5`). |

---

## L. Data Architecture

### L.1 Storage Responsibilities & Logical Isolation (`AP-04`, `AP-09`, `ASM-06`)

To uphold `AP-04` (separation of operational state, knowledge memory, and skills) without multi-cluster sprawl (`AP-09`), a single `orbi-postgres` container hosts **four strictly isolated PostgreSQL schemas** with distinct database roles:

| Store / Schema | Authoritative Owner | Contents | Ownership & Isolation Rules |
| :--- | :--- | :--- | :--- |
| **`PostgreSQL: orbi_app`** | `MOD-01`, `MOD-03`, `MOD-06`, `MOD-07`, `MOD-08`, `MOD-11`, `MOD-15` | `users`, `households`, `channel_bindings`, `companion_devices`, `tasks`, `task_steps`, `task_budgets`, `approval_requests`, `agent_definitions`, `tool_definitions`, `skill_packages`, `skill_versions`, `skill_eval_runs`, `secret_envelopes`, `audit_events`. | Authoritative system of record for tasks, approvals, registries, skills, and immutable audit trails. `audit_events` role has `INSERT`/`SELECT` only (no `UPDATE`/`DELETE`). |
| **`PostgreSQL: orbi_memory`** | `MOD-10` Memory Manager | `memory_units` (canonical ID, `owner_user_id`, `household_id`, `visibility_scope`, `trust_tier`, `source_provenance`, `event_time`, `valid_from`, `valid_to`, `superseded_by`), PostgreSQL FTS/BM25 index (`EVAL-07`), `cognee_sync_outbox`. | Separate schema & DB role (`CON-14`). Never shares foreign keys with `orbi_app.tasks` or `orbi_app.approval_requests`. |
| **`PostgreSQL: openfga`** | `MOD-09` OpenFGA Container | OpenFGA internal tuple store, authorization model versions (`policy_version`). | Accessible **only** by `orbi-openfga` container; `orbi-core` interacts strictly via OpenFGA APIs. |
| **`PostgreSQL: langgraph_checkpoints`** | `MOD-04` Agent Runtime | Ephemeral intra-turn LangGraph graph state (`CON-18`, `EVAL-04`). | Subordinate to `orbi_app.tasks`; purged on task completion/expiry/cancellation; never consulted as lifecycle truth (`AP-03`). |
| **`Redis (orbi-redis)`** | `MOD-03`, `MOD-08`, `MOD-14` | Task queues (`queue:tasks:*`), worker heartbeat leases, cancellation pub/sub, idempotency locks, Windows Companion `presence:companion:*` keys. | Ephemeral coordination & durable AOF queue transport only; PostgreSQL remains authoritative on restart recovery (`NFR-REL-01`). |
| **`Cognee Backends` (`EVAL-03`)** | `MOD-10` (`CogneeAdapter`) | Vector embeddings and Knowledge Graph entities/edges keyed by `memory_unit_id`. | Backends verified in `EVAL-03`; every retrieved node/edge/vector requires `orbi_memory` + OpenFGA verification before exit (`DOC-HLD-001`). |
| **`Object / Blob Storage` (`CON-22`)** | `MOD-10`, `MOD-11`, `MOD-13`, `MOD-15` | Raw uploaded documents, Playwright error screenshots/HAR traces, persistent encrypted browser state blobs, archived skill tarballs. | Abstracted behind `BlobStoragePort`: initially local OCI encrypted Docker volume (zero extra infra per `AP-09`), extensible to OCI Object Storage buckets. |

### L.2 Core Entity Relationships & Key Identifiers

```mermaid
erDiagram
    HOUSEHOLD ||--o{ USER : includes
    USER ||--o{ CHANNEL_BINDING : authenticates_via
    USER ||--o{ TASK : initiates
    HOUSEHOLD ||--o{ COMPANION_DEVICE : owns
    TASK ||--o{ TASK_STEP : tracks
    TASK ||--o{ APPROVAL_REQUEST : pauses_for
    AGENT_DEFINITION ||--o{ TASK_STEP : executes
    TOOL_DEFINITION ||--o{ APPROVAL_REQUEST : binds
    USER ||--o{ MEMORY_UNIT : owns
    HOUSEHOLD ||--o{ MEMORY_UNIT : scopes
    MEMORY_UNIT ||--o| MEMORY_UNIT : superseded_by
    SKILL_PACKAGE ||--o{ SKILL_VERSION : versions
    TASK ||--o{ AUDIT_EVENT : correlates
```

- **Canonical Key Taxonomy**: Every cross-module flow propagates `correlation_id` (root request trace UUID), `task_id` (UUID), `user_id` (`user:<uuid>`), `household_id` (`household:<uuid>`), `agent_id`, `tool_id@version`, `approval_id`, `memory_unit_id`, `skill_id@semver`, and `audit_event_id`.

### L.3 Retention, Deletion & Right-to-Forget (`[PROPOSED]`)

- **Memories**: User deletion in `MOD-02` marks `memory_units` deleted in `orbi_memory`, enqueues cascading removal in `CogneeAdapter` (vectors + orphaned graph entities/edges) and `BlobStoragePort`, removes OpenFGA `memory_unit` tuples, and appends a redacted `MEMORY_PURGED` event in `MOD-15`.
- **Ephemeral Browser/Checkpoint Artifacts**: `langgraph_checkpoints` and ephemeral Playwright screenshots auto-expire via configurable retention TTLs (e.g., 7–30 days), while `audit_events` retain redacted digests longer per audit policy.

---

## M. Security & Reliability Architecture

| Concern | Threat / Risk | Architectural Control & Enforcing Module(s) |
| :--- | :--- | :--- |
| **Threat Boundaries (`TB-01`–`TB-06`)** | Compromise of external channels, public web DOMs, or local PC pivoting into OCI core or secret vaults. | Six explicit trust boundaries (`Diagram 3`). `orbi-browser` (`MOD-13`) and future `MOD-11-SANDBOX` sit in isolated Docker networks with zero DB/Redis/Vault routes; `MOD-14` connects outbound via device-pinned mTLS. |
| **Untrusted Content & Indirect Prompt Injection** | Malicious instructions embedded inside emails, web DOMs, or desktop screen text hijacking agent control flow (`CON-07`). | `MOD-08` tags all external inputs `untrusted_external`; `MOD-04` wraps them in fenced data delimiters; Stage-1 Discovery + Stage-2 OpenFGA + Action-Bound Approvals operate entirely deterministically outside LLM reasoning (`AP-02`). |
| **Memory Poisoning** | External prompt injections persisting fake user preferences or rules into Cognee (`CON-16`). | `MOD-10` quarantines `untrusted_external` extractions; untrusted records cannot supersede (`superseded_by`) `user_explicit` facts (`DOC-HLD-001`). |
| **Tool Abuse & Hallucination** | Agent inventing tool IDs, elevating permissions, or running direct network/OS calls (`AP-01`). | 7-step Stage-1 OpenFGA discovery strips unauthorized schemas; `MOD-08` validates registered `tool_id` + JSON Schema, re-checks OpenFGA on exact target resource, and blocks any unregistered or bypassed invocation. |
| **Credential Isolation** | LLM prompting, logs, skill scripts, or `MOD-13` leaking OAuth tokens/API keys. | Secrets encrypted at rest in `orbi_app.secret_envelopes` (envelope encryption with OCI KMS or master KEK), decrypted and injected solely inside `MOD-08`/`MOD-12` at call time, and scrubbed from outputs/audits (`FR-AU-02`). |
| **Execution Sandboxing** | Browser exploits, SSRF against OCI metadata, or malicious skill scripts. | `MOD-13` runs non-root with resource limits and SSRF IP blocking; initial Phase 4 skills are `MARKDOWN_ONLY`, with future supporting scripts confined to `MOD-11-SANDBOX` (`EVAL-06`, zero ambient network/secrets). |
| **Sensitive Data & Screen Privacy** | Desktop screenshots or audit logs exposing passwords, banking screens, or PII (`CON-09`). | `MOD-14` hard-blocks password managers/UAC/banking apps locally, crops Tier 3 vision captures strictly to allowlisted windows with local masking, and `MOD-15` stores digests/redacted payloads only. |
| **Failure Isolation & Graceful Degradation** | Jev outage, OpenFGA outage, single memory branch timeout, or laptop offline stalling entire platform. | Bulkhead timeouts per adapter/branch; fail-closed OpenFGA (`NFR-REL-02`); `MOD-05` fallback to rules/LLM (`ADR-003`); `MOD-10` multi-branch partial degradation (`DOC-HLD-001`); fast `COMPANION_OFFLINE` detection via Redis presence. |
| **Observability & Auditability (`AP-07`)** | Inability to trace why an agent acted or verify compliance. | Every stage emits `correlation_id`/`task_id`-linked events to append-only `MOD-15` ledger plus OpenTelemetry structured metrics/traces exposed in `MOD-02` Dashboard. |

---

## N. Required End-to-End Sequence Diagrams

### Sequence 1: Simple Request Invoking One Tool *(e.g., "Play my Focus playlist on Spotify")*

```mermaid
sequenceDiagram
    autonumber
    participant U as User - Telegram
    participant M01 as MOD-01 Channel Gateway
    participant M03 as MOD-03 Task Manager
    participant M05 as MOD-05 Decision Service
    participant M04 as MOD-04 Runtime / MOD-06 / MOD-07
    participant M09 as MOD-09 OpenFGA
    participant M08 as MOD-08 Tool Gateway
    participant M12 as MOD-12 Spotify Adapter
    participant M15 as MOD-15 Audit Ledger

    U->>M01: "Play my Focus playlist on Spotify"
    M01->>M09: Verify Telegram binding -> user:owner
    M01->>M03: CreateTask(user:owner, intent_text)
    M03->>M03: Persist Task(CREATED -> QUEUED -> RUNNING)
    M03->>M05: Triage complexity (timeout_ms)
    M05-->>M03: single_agent_tool -> agent:media
    M03->>M04: Dispatch Bounded Turn(agent:media)
    M04->>M09: Stage-1 OpenFGA filter on MOD-07 catalog
    M09-->>M04: Authorized tools [spotify.search, spotify.play]
    M04->>M04: ModelAdapter selects spotify.play(playlist="Focus")
    M04->>M08: ToolCallIntent(spotify.play)
    M08->>M09: Stage-2 Check(user:owner, agent:media, execute, spotify.play, res:spotify_primary)
    M09-->>M08: ALLOW (LOW_RISK_WRITE -> no HITL needed)
    M08->>M12: Inject Spotify OAuth & Invoke play
    M12-->>M08: Playback Started
    M08->>M15: Record TOOL_EXECUTED
    M08-->>M04: Sanitized Observation
    M04-->>M03: TurnOutcome(COMPLETED, "Playing Focus playlist")
    M03->>M15: Record TASK_COMPLETED
    M03->>M01: Send Reply to Telegram
```

### Sequence 2: Complex Multi-Agent & Multi-Tool Task *(e.g., "Summarize unread project emails from Priya and log action items to Notion via Browser")*

```mermaid
sequenceDiagram
    autonumber
    participant U as User - Telegram
    participant M03 as MOD-03 Task Manager
    participant M05 as MOD-05 Decision Service
    participant MGR as MOD-04 ManagerAgent
    participant SPEC1 as MOD-04 EmailAgent
    participant SPEC2 as MOD-04 BrowserAgent
    participant M09 as MOD-09 OpenFGA
    participant M10 as MOD-10 Memory Manager
    participant M08 as MOD-08 Tool Gateway
    participant HAR as MOD-12 Gmail / MOD-13 Playwright

    U->>M03: Submit Complex Cross-Domain Request
    M03->>M05: Triage -> multi_step_plan
    M03->>MGR: Dispatch Bounded Turn(agent:manager)
    MGR->>M10: Retrieve scoped context ("Priya project")
    M10-->>MGR: Authorized provenance-tagged facts
    MGR->>SPEC1: Delegate Sub-Goal 1: Fetch emails from Priya
    SPEC1->>M09: Independent Stage-1 Tool Filter -> [gmail.search, gmail.read]
    SPEC1->>M08: ToolCallIntent(gmail.search)
    M08->>HAR: MOD-12 Gmail Search (Stage-2 OpenFGA ALLOW)
    HAR-->>M08: Email threads (tagged untrusted_external)
    M08-->>SPEC1: Fenced Observations
    SPEC1-->>MGR: Sub-Goal 1 Summary (Action Items)
    MGR->>SPEC2: Delegate Sub-Goal 2: Open Notion & draft page
    SPEC2->>M09: Independent Stage-1 Tool Filter -> [browser.navigate, browser.fill]
    SPEC2->>M08: ToolCallIntent(browser.navigate / browser.fill)
    M08->>HAR: MOD-13 Playwright Container (Stage-2 OpenFGA ALLOW)
    HAR-->>M08: Draft Status
    M08-->>SPEC2: Sanitized Observation
    SPEC2-->>MGR: Sub-Goal 2 Done
    MGR-->>M03: Final Synthesized Outcome -> Reply to User
```

### Sequence 3: Sensitive Action Requiring Cryptographic Approval & Pre-Execution Revalidation *(e.g., "Send reply email to client")*

```mermaid
sequenceDiagram
    autonumber
    participant M04 as MOD-04 EmailAgent
    participant M08 as MOD-08 Tool Gateway
    participant M07 as MOD-07 Tool Registry
    participant M09 as MOD-09 OpenFGA
    participant M03 as MOD-03 Task Manager
    participant UI as MOD-01 Telegram & MOD-02 Dash
    participant M12 as MOD-12 Gmail Adapter
    participant M15 as MOD-15 Audit Ledger

    M04->>M08: ToolCallIntent(gmail.send_email, args={to, subject, body})
    M08->>M07: Lookup RiskTier -> SENSITIVE_WRITE & Semantic Fields
    M08->>M09: Stage-2 Check -> ALLOW + policy_version=v12
    M08->>M08: Compute SHA-256 binding_digest(user, task, tool, canonical_args, target, v12, expires_at)
    M08->>M03: RequestApproval(binding_digest, summary)
    M03->>M03: Persist ApprovalRequest(PENDING, HMAC) & Task -> AWAITING_APPROVAL
    M03->>M15: Record APPROVAL_REQUESTED(binding_digest)
    M03->>UI: Push Action-Bound Approval Prompt
    UI-->>M03: Human Grants Approval(approval_id, actor_user_id)
    M03->>M09: Verify actor_user_id authorized approver
    M03->>M03: Atomic CAS PENDING -> APPROVED & Re-enqueue Task -> QUEUED
    M03->>M08: Resume Approved ToolCallIntent
    Note over M08: Immediate Pre-Execution Revalidation (ADR-004)
    M08->>M09: Re-check live OpenFGA ALLOW & current policy_version
    M08->>M08: Recompute digest, verify HMAC, verify now < expires_at, CAS APPROVED -> CONSUMED
    alt Any Mutation, Policy Bump, or Expiry
        M08->>M15: Record APPROVAL_INVALIDATED
        M08->>M03: Reject & Trigger Fresh Approval
    else Valid Single-Use Consumption
        M08->>M12: Inject Credentials & Send Email
        M12-->>M08: Message Sent ID
        M08->>M15: Record TOOL_EXECUTED(CONSUMED)
        M08-->>M04: Return Confirmation
    end
```

### Sequence 4: Hybrid Memory Retrieval Request *(See [memory-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/memory-architecture.md) for full internal contracts)*

```mermaid
sequenceDiagram
    autonumber
    participant M04 as MOD-04 Agent Runtime
    participant M10 as MOD-10 Memory Manager
    participant QU as QueryUnderstanding
    participant VEC as VectorBranch - CogneeAdapter
    participant BM25 as BM25Branch - PG Search*
    participant GRP as GraphBranch - CogneeAdapter
    participant TMP as TemporalBranch - orbi_memory
    participant M09 as MOD-09 OpenFGA
    participant FUS as Fusion RRF* & Reranker*
    participant M15 as MOD-15 Audit Ledger

    M04->>M10: retrieve_hybrid_memory(query, user_id, household_id, limits)
    M10->>QU: Analyze intent, entities, keywords, temporal & graph need (no mandatory LLM)
    QU-->>M10: QueryPlan(activated_branches)
    par Active Branches Only
        M10->>VEC: Vector semantic search
        VEC->>M09: Per-item OpenFGA check (before branch exit)
        M09-->>VEC: Permitted items only
    and
        M10->>BM25: Keyword/BM25 search*
        BM25->>M09: Per-item OpenFGA check (before branch exit)
        M09-->>BM25: Permitted items only
    and
        M10->>GRP: Entity/relationship traversal
        GRP->>M09: Per-node & per-edge OpenFGA check (before branch exit)
        M09-->>GRP: Permitted subgraph only
    and If Temporal Constraint Active
        M10->>TMP: Time/validity SQL query
        TMP->>M09: Per-item OpenFGA check (before branch exit)
        M09-->>TMP: Permitted items only
    end
    M10->>FUS: Fuse authorized candidates (RRF*), deduplicate & optionally rerank*
    FUS-->>M10: Final Top-K Provenance-Tagged Context
    M10->>M15: Record HYBRID_RETRIEVAL(branches, dropped_authz, latency)
    M10-->>M04: Authorized Memory Context
```
*(\* Proposed / Evaluation Required under `EVAL-03`, `EVAL-07`)*

### Sequence 5: Task Involving the Windows Local Companion

```mermaid
sequenceDiagram
    autonumber
    participant M04 as MOD-04 DesktopAgent
    participant M08 as MOD-08 Tool Gateway Relay
    participant REDIS as Redis Presence
    participant M09 as MOD-09 OpenFGA
    participant M03 as MOD-03 Task Manager
    participant M14 as MOD-14 Windows Companion
    participant WIN as Windows Local OS - Native / agent-desktop / Vision
    participant M15 as MOD-15 Audit Ledger

    M14->>M08: Persistent Outbound mTLS Heartbeat (15s) -> Update Redis presence:device_1
    M04->>M08: ToolCallIntent(desktop.export_report, target="device_1")
    M08->>REDIS: Check Companion Online Presence
    alt Companion Offline
        REDIS-->>M08: Missing / Stale Heartbeat
        M08->>M15: Record COMPANION_OFFLINE
        M08-->>M04: Return COMPANION_OFFLINE (Fail Fast or Bounded Queue per Task)
    else Companion Online
        M08->>M09: Stage-2 OpenFGA Check(user:owner, agent:desktop, resource:device_1)
        M09-->>M08: ALLOW
        Note over M08,M03: Sensitive actions trigger ADR-004 Approval regardless of Tier 1/2/3
        M08->>M14: Dispatch Signed CompanionCommand over mTLS
        M14-->>M08: CMD_ACK
        M14->>M14: Enforce Kill-Switch & App/Path Allowlist
        M14->>WIN: Execute Tier 1 Native API -> Tier 2 UIA agent-desktop -> Tier 3 Vision
        alt Confidence Below Threshold or Stale Snapshot
            WIN-->>M14: Confidence Insufficient / Drift
            M14-->>M08: STOP_INSUFFICIENT_CONFIDENCE
        else Validated Execution
            WIN-->>M14: Redacted UI / File Result
            M14-->>M08: CMD_RESULT
        end
        M08->>M15: Record WINDOWS_EXECUTION(tier_used, confidence, status)
        M08-->>M04: Return Sanitized Observation
    end
```

---

## O. Unresolved Architectural Choices — Alternatives, Trade-Offs & Recommendations

To avoid locking untested assumptions prior to spike validation (`EVAL-01`–`EVAL-07`) and HLD sign-off, all open questions (`OQ-01`–`OQ-14`) carry structured evaluations below:

| Open Question / Spike | Candidate Alternatives | Trade-Offs | Architect Recommendation | Evaluation / Exit Criteria |
| :--- | :--- | :--- | :--- | :--- |
| **`OQ-01` Dashboard Frontend Stack** | (A) **Next.js / React SPA** (`orbi-dashboard` container)<br>(B) **HTMX + Jinja2/Tailwind** served by FastAPI<br>(C) **Vite + React/TS** static SPA | (A)/(C) deliver richer graph visualization (`MOD-10` memory graph) and WebRTC/noVNC takeover UI (`MOD-13`), at the cost of Node build tooling; (B) minimizes containers (`AP-09`) but limits interactive graph/canvas UX. | **(C) Vite + React + TypeScript** static build served behind reverse proxy calling FastAPI BFF. Keeps SSR complexity zero while supporting rich graph/takeover/diff UIs. | Owner confirmation on frontend stack prior to LLD (`Phase 2`). |
| **`OQ-04` Initial LLM Providers & Budgets** | (A) Frontier Cloud APIs (Anthropic/OpenAI/Gemini) via LiteLLM/custom `ModelAdapter`<br>(B) Local Ollama + Cloud hybrid | Cloud APIs maximize reasoning reliability for LangGraph tool selection without GPU RAM overhead on OCI; local models reduce marginal cost but cannot fit cleanly on CPU-only OCI VMs. | **Cloud Frontier APIs behind `ModelAdapter`** with small fast tier for simple tasks and configurable daily/per-task USD & token caps in `MOD-03`. | Owner sets starter provider keys & daily/per-task spend thresholds. |
| **`OQ-05` / `EVAL-05` OCI VM Sizing** | (A) OCI Always-Free ARM (`VM.Standard.A1.Flex` up to 4 OCPU / 24 GB RAM if capacity available)<br>(B) Paid OCI Flex VM (`16–32 GB RAM`) | Always Free ARM saves cost (`\$0`) but suffers regional capacity scarcity and ARM64 container compatibility checks (`agent-desktop`/Playwright/ONNX); paid Flex ensures availability & predictable IOPS. | **Never assume Always Free suffices.** Run Docker Compose memory/CPU load benchmark (`EVAL-05`) with Postgres, Redis, OpenFGA, `orbi-core`, Playwright, and Cognee backends before choosing shape. | Measured idle & peak RAM/CPU under concurrent browser + memory workloads. |
| **`OQ-06` / `EVAL-03` / `EVAL-07` Cognee Backends & Search** | (A) Cognee on `orbi-postgres` (`pgvector`) + embedded graph (Kuzu) + PG FTS/BM25 (`pg_search`)<br>(B) Dedicated Neo4j/Qdrant/Meilisearch containers | (A) honors `AP-09` minimal containers on 1 VM; (B) adds 3+ stateful JVM/Rust containers blowing single-VM RAM, though potentially richer native graph/BM25 features. | **Test (A) first** in `EVAL-03` & `EVAL-07`; introduce extra graph/search containers **only** if Cognee lacks support or PG search fails `EVAL-07` quality/latency targets. | Pass `EVAL-03` 6-dimension audit + `EVAL-07` `Recall@10 >= 0.85`, `MRR >= 0.80`, $0\%$ leakage. |
| **`OQ-08` Sensitivity Matrix & Approval TTLs** | (A) Strict conservative defaults (all external writes/sends `SENSITIVE_WRITE`, deletes/payments `IRREVERSIBLE`, 15m interactive TTL)<br>(B) Permissive auto-drafts | Conservative defaults prevent accidental external messages/purchases (`AP-06`) at slight friction; permissive risks silent unwanted side effects. | **Conservative defaults** (`READ_ONLY` auto; internal drafts `LOW_RISK_WRITE`; sending emails, public posts, desktop submissions `SENSITIVE_WRITE` w/ 15m–1h TTL; deletes/purchases `IRREVERSIBLE` w/ 15m TTL). | Owner sign-off on per-tool tier table before Phase 1 LLD. |
| **`OQ-11` / `EVAL-06` Skill Script Sandbox** | (A) Ephemeral unprivileged Docker container (`--network=none`, `--read-only`)<br>(B) gVisor / Firecracker microVM<br>(C) Restricted Pyodide / WASM | Docker `--network=none` is straightforward on OCI Compose; gVisor adds stronger kernel isolation if nested cleanly on OCI VM; WASM avoids container cold start but limits Python C-extensions. | **Launch Phase 4 with `MARKDOWN_ONLY` skills (`CON-17`)**, then evaluate **(A)/(B)** vs. **(C)** in `EVAL-06` before enabling supporting scripts. | Security escape test pass + invocation latency $< 500\text{ ms}$. |
| **`OQ-12` / `EVAL-02` Windows UIA & Vision Thresholds** | (A) Native -> `agent-desktop` UIA -> Multimodal Vision API fallback<br>(B) Local lightweight grounding model | Cloud multimodal grounding on cropped/redacted windows avoids shipping large vision models on user's laptop while preserving `STOP_INSUFFICIENT_CONFIDENCE`; local grounding keeps pixels offline at higher laptop GPU cost. | **Native API -> `agent-desktop` UIA -> Window-Cropped/Redacted Cloud Vision** with strict confidence floor (`>= 0.85` proposed), perceptual hash check, and zero silent retries. | Pass `EVAL-02` Windows app matrix + PII redaction verification. |
| **`OQ-13` / `EVAL-07` Memory Reranker Mode** | (A) `NoOp` RRF only<br>(B) Local CPU ONNX cross-encoder<br>(C) External Rerank API | `NoOp` costs `0 ms` & `0 MB RAM`; local cross-encoder adds ~`1–2 GB RAM` + CPU latency on OCI VM; API reranker keeps VM lean at small per-call cost. | **Benchmark (A) vs. (B) vs. (C)** in `EVAL-07`; ship `NoOp` or API reranker in Phase 2 unless local ONNX fits comfortably inside `EVAL-05` RAM budget. | $\Delta \text{NDCG@5}$ lift vs. latency & RAM overhead in `EVAL-07`. |
| **`OQ-14` Bi-Temporal Supersession Policy** | (A) Auto-close `valid_to` on `user_explicit` facts<br>(B) Queue all contradictions for Dashboard review | Auto-closing keeps current preference queries accurate immediately while preserving history; review queue guarantees zero silent errors on ambiguous edits. | **Hybrid**: Auto-close `valid_to` (`superseded_by`) for explicit user corrections; hold `untrusted_external` contradictions in quarantine for Dashboard review (`CON-16`). | Validated in `MEM-EVAL-04` and `MEM-EVAL-10`. |
| **`EVAL-01` Optional Jev Provider** | (A) Enable `JevDecisionProvider` + fallback<br>(B) `DeterministicRule` + fast `LLMDecisionProvider` only | Jev promises fast calibrated `noul`/`choice`/`score`, but adds an external vendor dependency requiring benchmark validation from OCI. | Keep Jev strictly behind `DecisionService` (`MOD-05`) and deploy Phase 1 with rules + LLM fallback until `EVAL-01` proves positive ROI. | Latency, accuracy & calibration benchmark across 30+ triage/risk prompts. |
| **`EVAL-04` LangGraph Turn Checkpointing** | (A) Stateless per-turn invocation + `MOD-03` summary state<br>(B) Scoped `langgraph_checkpoints` cleaned on completion | Scoped checkpoints allow clean intra-turn recovery while keeping `MOD-03` authoritative over `AWAITING_APPROVAL` (`CON-18`). | **Scoped `langgraph_checkpoints`** subordinated strictly to `orbi_app.tasks` (`AP-03`). | Verified pause/restart/approval-resume test with zero split-brain state. |
