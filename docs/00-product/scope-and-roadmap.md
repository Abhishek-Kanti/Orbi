# Orbi — Scope, Evaluation Spikes & Delivery Roadmap

- **Document ID**: `DOC-PRD-003`
- **Revision**: `1.1` *(Updated with GLD Review Decisions & ADRs)*
- **Phase**: `00-Product`
- **Status**: `Draft - For Review`
- **Last Updated**: `2026-11-14`

---

## 1. Engineering Documentation Roadmap (Pre-Implementation Gate)

`[CONFIRMED]` No application code, dependency installation, or framework initialization will begin until the architecture blueprint is reviewed, reconciled, and approved across these documentation phases:

```
[Phase 0: Product & GLD + Initial ADRs]  ──▶  [Phase 1: Spike Evals]  ──▶  [Phase 2: HLD]  ──▶  [Phase 3: LLD]  ──▶  [Implementation]
        (Current Milestone)                      (EVAL-01..06)            (docs/02-hld/)       (docs/03-lld/)
```

| Folder | Target Artifacts | Gate Criterion |
| :--- | :--- | :--- |
| `docs/00-product/` | `vision.md`, `requirements.md`, `scope-and-roadmap.md` *(Rev 1.1)* | Owner alignment on Decisions 1–10, Clarifications A–E, and open HLD questions (`OQ-01`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-08`, `OQ-11`, `OQ-12`). |
| `docs/01-gld/` | `system-overview.md`, `module-catalog.md`, `functional-responsibilities.md` *(Rev 1.1)* | Sign-off on 3-zone resource isolation, 7-step discovery flow, action-bound approvals, and `CON-01`–`CON-12` resolutions. |
| `docs/04-adr/` | `README.md`, `ADR-001` through `ADR-007` *(Initial baseline drafted)* | Formal capture of confirmed architectural trade-offs and evaluation prerequisites. |
| `docs/02-hld/` | Component interaction specs, OpenFGA household/agent schema, Cloud-Local mTLS protocol, OCI Docker Compose layout, Security & Threat model | Verified end-to-end data flows, failure modes, Dashboard stack (`OQ-01`), sensitivity matrix (`OQ-08`), and spike outputs (`EVAL-01`–`06`). |
| `docs/03-lld/` | PostgreSQL DDL, Redis queue contracts, Tool/Agent Registry schemas, `SKILL.md` package spec, Task FSM, Gateway & Harness APIs | Implementation-ready specifications enabling modular, test-driven coding. |

---

## 2. Mandatory Technology Evaluation Spikes (`EVAL-01` – `EVAL-06`)

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

### `EVAL-03` — Cognee Supported Storage Backends & Footprint (`MOD-10`, `ADR-007`)
- **Role in Architecture**: Memory Manager (`MOD-10`) encapsulating Cognee for long-term knowledge storage and retrieval (`AP-04`).
- **Why Evaluation Is Required**: Minimal infrastructure is preferred (`AP-09`), **but we must verify actual officially supported relational/vector/graph storage backends** in the target Cognee release rather than assuming `pgvector` + embedded graph works seamlessly out-of-the-box (`OQ-06`).
- **Key Evaluation Questions**:
  1. Which vector and graph backends does Cognee natively support and maintain reliably in containerized Python 3.12+ environments?
  2. Can Cognee operate on isolated `orbi-postgres` (`pgvector`) + minimal/embedded graph storage, or does it require dedicated vector/graph containers?
  3. Does Cognee support clean deletion, provenance metadata filtering, and household/user scope isolation required by `MOD-02` and `MOD-09`?
- **Exit Criteria**: Verified backend compatibility matrix + resource footprint fed into `EVAL-05` and HLD.

### `EVAL-04` — LangGraph & Deterministic Task Manager Turn Handoff (`MOD-03`/`MOD-04`, `AP-03`)
- **Role in Architecture**: Ensuring `MOD-03` owns task lifecycle, budgets, and immutable `AWAITING_APPROVAL` states while LangGraph (`MOD-04`) handles bounded cognitive turns.
- **Why Evaluation Is Required**: Validates that pausing for action-bound approvals (`ADR-004`) and rehydrating after long pauses causes zero split-brain conflict with LangGraph's runtime semantics (`CON-01`).
- **Exit Criteria**: Prototype turn-state serialization and approval resume test specification.

### `EVAL-05` — OCI Single-VM Docker Compose Resource Benchmarking (`NFR-INF-01`, `ADR-001`)
- **Role in Architecture**: Validate CPU, RAM, disk IOPS, and swap behavior of the full modular Docker Compose stack on a single OCI VM.
- **Why Evaluation Is Required**: **OCI Always Free capacity is explicitly NOT assumed sufficient without benchmarking (`OQ-05`).** Running PostgreSQL, Redis, OpenFGA, `orbi-core`, Playwright Chromium (`MOD-13`), Dashboard (`MOD-02`), Skill Script Sandbox (`MOD-11-SANDBOX`), and Cognee's verified backends (`EVAL-03`) concurrently can easily exceed modest free-tier memory budgets.
- **Exit Criteria**: Measured idle and peak RAM/CPU budget per container + concrete OCI VM shape recommendation for HLD.

### `EVAL-06` — Sandboxed Skill Script Execution Mechanism (`MOD-11-SANDBOX`, `ADR-005`)
- **Role in Architecture**: Isolated execution environment for optional supporting scripts bundled inside Markdown skill packages (`SKILL.md`).
- **Why Evaluation Is Required**: Supporting scripts must execute without host filesystem access, ambient credentials, direct external network access, or Gateway bypass (`CON-08`, `OQ-11`).
- **Exit Criteria**: Selection and threat-model verification of sandbox isolation technology (e.g., ephemeral unprivileged container / gVisor / WASM runtime) prior to HLD.

---

## 3. Proposed Phased Delivery Roadmap (`[PROPOSED]`)

| Phase | Milestone Focus | Included Capabilities & Modules | Exit Gate |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Core Spine, Authz & Action-Bound Approvals** | OCI Docker Compose spine (`orbi-postgres`, `orbi-redis`, `orbi-openfga`), `orbi-core` (Python 3.12+/FastAPI/LangGraph), `MOD-01` Telegram Adapter, `MOD-03` Task Manager, `MOD-04` Provider-Independent LLM Adapters + 7-step Tool Discovery, `MOD-05` Decision Service (Deterministic + LLM fallback), `MOD-06`/`MOD-07` Registries, `MOD-08` Tool Execution Gateway with canonical action-bound approvals (`ADR-004`), `MOD-15` Audit Ledger. | End-to-end governed task lifecycle with verified OpenFGA household-ready filtering, immutable approval invalidation on argument change, and Telegram approval resume. |
| **Phase 2** | **Memory Manager, SaaS APIs, Dashboard & Optional Jev** | `MOD-02` Web Dashboard (stack from `OQ-01`), `MOD-10` Memory Manager wrapping Cognee (verified backends from `EVAL-03`), `MOD-12` SaaS API Connectors (Gmail, Spotify), optional Jev provider plugged into `MOD-05` (conditional on `EVAL-01` pass). | Gmail/Spotify tasks governed by action-bound approvals; Cognee memories inspectable/curated in Dashboard; `MOD-05` Jev fallback tested under simulated outage. |
| **Phase 3** | **Isolated Cloud Browser & Layered Windows Companion** | `MOD-13` Isolated Playwright Container, `MOD-14` Local Windows Companion implementing Native API -> UIA (`agent-desktop`, `EVAL-02`) -> Controlled Vision Fallback with confidence halts, mTLS outbound tunnel, local guardrails & kill-switch. | Verified browser automation and cross-boundary Windows automation with safe confidence stops and action-bound HITL approvals across all interaction layers. |
| **Phase 4** | **Markdown Skill Packages & Script Sandbox** | `MOD-11` Skill Lifecycle Manager (`SKILL.md` packages, schemas, examples, tests), `MOD-11-SANDBOX` (`EVAL-06`) isolated runner for supporting scripts, Dashboard skill review/approval UI, semantic version pinning & rollback. | Candidate Markdown skill packages evaluated in sandbox, explicitly approved by owner, published, and invoked via `MOD-08` without privilege escalation. |
| **Phase 5** | **Home IoT & Multi-Member Household Expansion** | `MOD-16-EXT` Home Assistant / MQTT Bridge behind `MOD-07`/`MOD-08`/`MOD-09`, OpenFGA household member tuples activated in `MOD-01`/`MOD-02`, optional secondary user channels (`AP-10`). | Multi-member OpenFGA isolation verified + IoT actions governed by sensitivity tiers and approvals. |

---

## 4. Scope Boundary Matrix (`[PROPOSED]`)

| Capability | Phase 1 (Core) | Phase 2 (Memory & SaaS) | Phase 3 (Harnesses) | Phase 4 (Skills) | Phase 5 (IoT & Household) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Python/FastAPI/LangGraph Core & Task Manager | **In Scope** | Enhanced | Enhanced | Enhanced | Enhanced |
| Household-Ready OpenFGA Schema & 7-Step Discovery | **In Scope** (Owner Active) | Expanded | Expanded | Skill Scopes | **Household Active** |
| Action-Bound Approval Digests & Invalidation | **In Scope** | Expanded | Cross-Boundary | Skill Approvals | IoT Approvals |
| Provider-Independent LLM & Decision Service (Rules/LLM) | **In Scope** | Optional Jev (`EVAL-01`) | Tuned | Tuned | Tuned |
| Web Dashboard (`MOD-02`, `OQ-01`) | — | **In Scope** | Trace/Sandbox Views | Skill Approval UI | Household/IoT UI |
| Memory Manager over Cognee (`EVAL-03`) | — | **In Scope** | Cross-Harness | Grounding | Context Awareness |
| External SaaS APIs (Gmail, Spotify) | — | **In Scope** | Expanded | Skill-Composed | — |
| Playwright Cloud Browser Container (`MOD-13`) | — | — | **In Scope** | Evaluated | — |
| Layered Windows Companion (`Native/UIA/Vision`, `EVAL-02`) | — | — | **In Scope** | Desktop Skills | — |
| Markdown Skill Packages & Script Sandbox (`EVAL-06`) | — | — | — | **In Scope** | IoT Skills |
| Home Assistant / MQTT Boundary (`MOD-16-EXT`) | Interface Only (`AP-10`) | Interface Only | Interface Only | Interface Only | **In Scope** |
| Commercial Multi-Tenant SaaS Isolation / Billing | Out of Scope | Out of Scope | Out of Scope | Out of Scope | Out of Scope |
