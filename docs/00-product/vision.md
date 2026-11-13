# Orbi — Product Vision & Architectural Charter

- **Document ID**: `DOC-PRD-001`
- **Revision**: `1.1` *(Updated with GLD Review Decisions & Clarifications)*
- **Phase**: `00-Product`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-14`

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

`[CONFIRMED]` **Orbi** is a personal AI agent platform designed to connect reasoning agents, provider-independent structured decision capabilities, deterministic task orchestration, long-term knowledge memory, cloud browsers, external SaaS applications, local desktop environments, and—in future phases—home IoT devices under a single, governed operational spine.

`[CONFIRMED]` The platform adopts a **Governed Modular Monolith with Isolated Execution Harnesses** built on **Python 3.12+, FastAPI, and LangGraph** (`orbi-core`), deployed initially via **Docker Compose on a single Oracle Cloud Infrastructure (OCI) VM** (with resource sizing subject to benchmarking, `[EVALUATION REQUIRED]`).

`[PROPOSED]` **Core Product Thesis**: Most modern AI agent stacks fail in personal automation because they grant probabilistic LLMs direct, unmediated access to credentials, shells, browsers, and external APIs, while conflating chat history with durable workflow state and long-term knowledge. Orbi separates **cognitive proposal** from **deterministic governance**:

1. **Intelligence proposes; deterministic systems dispose.** Reasoning agents (LangGraph via provider-independent LLM adapters) and a provider-independent Decision Service (with deterministic rules, LLM fallbacks, and optional Jev by TypeSafe `[EVALUATION REQUIRED]`) analyze intent, retrieve knowledge, and formulate tool-call intents from permission-filtered catalogs.
2. **Governance enforces outside the model.** A deterministic Task Manager, an externalized Authorization Engine (OpenFGA), and a single Tool Execution Gateway enforce identity, two-stage permission checks, cryptographically action-bound human approval gates, secret injection, and audit logging entirely outside LLM prompt context.
3. **State domains and execution environments are strictly partitioned.** Transactional task/application state (PostgreSQL), Memory Manager knowledge (Cognee, storage backends `[EVALUATION REQUIRED]`), and versioned Markdown-based skill packages (`SKILL.md` + sandboxed supporting scripts) are isolated into distinct lifecycles, while cloud execution and local Windows execution maintain strict operational boundaries.

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
| **`AP-04`** | **Memory, Skills & App State Separation** | Long-term knowledge memory (Memory Manager over Cognee), reusable capabilities (versioned Markdown Skill Packages in the Skill Lifecycle Manager), and transactional application/task state (PostgreSQL) must remain distinct concepts, schemas, and lifecycles. |
| **`AP-05`** | **Clear Cloud vs. Local Boundary** | Local desktop execution (Windows Companion) and cloud execution (`orbi-core` + isolated cloud sandboxes) must maintain strict network, identity, data-redaction, resource-isolation, and kill-switch boundaries. |
| **`AP-06`** | **Explicit Approval for Sensitive/Irreversible Actions** | Sensitive writes and irreversible operations must pause task execution deterministically and require explicit human approval bound immutably to exact user, task, tool, arguments, target resource, policy version, and expiration (`ADR-004`). |
| **`AP-07`** | **End-to-End Auditability** | Every important operation—task creation, state transitions, decision-service calls, discovery filtering, OpenFGA checks, approval decisions, tool calls, memory commits, and skill publications—must emit immutable, correlated audit records. |
| **`AP-08`** | **Modularity & Independent Testability** | Every logical module must interact through explicit typed contracts and provider-independent ports so components can be unit- and contract-tested independently. |
| **`AP-09`** | **Pragmatic Simplicity** | Do not introduce unnecessary frameworks, message brokers, service meshes, or microservices. Run `orbi-core` as a modular monolith in Python/FastAPI/LangGraph, isolating separate containers/sandboxes only where execution isolation or OS locality requires it. |
| **`AP-10`** | **Extensibility Without Premature Implementation** | Design clean registry, harness, channel, household identity, and policy abstractions now so future capabilities (e.g., Home Assistant/MQTT IoT, household members, additional SaaS connectors) plug in cleanly without building them in Phase 1. |

---

## 5. High-Level Capability Pillars

### Pillar 1: Deterministic Task Spine & Cryptographic Approval Governance (`[CONFIRMED]`)
A durable, PostgreSQL-backed Task Manager coordinated via Redis queues ensures every user request becomes a trackable task with explicit states, configurable budgets, cancellation support, and deterministic pause/resume semantics bound to immutable action digests (`ADR-004`).

### Pillar 2: Provider-Independent Cognitive & Decision Architecture (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)
- **System-2 Reasoning (`[CONFIRMED]`)**: **LangGraph** orchestrates a **Manager/Planner Agent** and domain-specialized sub-agents using **provider-independent LLM model adapters** with configurable provider routing and budget controls (`ADR-003`).
- **Provider-Independent Decision Service (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)**: A unified Decision Service handles classification, routing, and triage (`noul`, `choice`, `score` semantics) backed by deterministic rules and LLM-based fallbacks. **Jev by TypeSafe** is an **optional provider** subject to evaluation (`EVAL-01`); core operation never depends exclusively on Jev (`ADR-003`).

### Pillar 3: Permission-Filtered Discovery & Zero-Trust Execution (`[CONFIRMED]`)
Central **Agent** and **Tool Registries** catalog every identity and capability. Before reasoning starts, tool discovery filters the agent's catalog by agent, user, and instance OpenFGA permissions so agents only see authorized tools (`ADR-002`). All invocations route through the **Tool Execution Gateway**, which re-verifies **OpenFGA**, checks action-bound approvals, and injects secrets.

### Pillar 4: Tri-Partite Knowledge & Markdown Skill Packages (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)
- **Application State**: Relational operational truth in PostgreSQL.
- **Memory Manager (`[CONFIRMED]` / `[EVALUATION REQUIRED]`)**: Storage-abstracted facade over **Cognee** (`EVAL-03` to verify actual supported backends), inspectable and editable in the Dashboard (`ADR-007`).
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
