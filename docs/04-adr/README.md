# Orbi — Architecture Decision Records (ADRs)

- **Phase**: `04-ADR`
- **Last Updated**: `2026-11-14`

---

## Index of Architectural Decisions

| ADR ID | Title | Status | Evaluation Gate(s) | Related Decisions / Principles |
| :--- | :--- | :--- | :--- | :--- |
| **[ADR-001](file:///f:/Projects/Orbi/docs/04-adr/ADR-001-modular-monolith-and-resource-isolation.md)** | Governed Modular Monolith Runtime & Three-Zone Resource Isolation | **Accepted** *(VM Sizing Pending Eval)* | `EVAL-05` *(OCI VM Benchmark)*, `OQ-01` *(Dashboard UI)* | Decisions 1, 5; Clarification E; `AP-05`, `AP-08`, `AP-09` |
| **[ADR-002](file:///f:/Projects/Orbi/docs/04-adr/ADR-002-household-openfga-and-two-stage-discovery.md)** | Household-Extensible OpenFGA Model & Two-Stage Tool Discovery | **Accepted** | `—` | Decision 2; Clarification A; `AP-01`, `AP-02`, `AP-10` |
| **[ADR-003](file:///f:/Projects/Orbi/docs/04-adr/ADR-003-provider-independent-decision-and-llm-adapters.md)** | Provider-Independent Decision Service & LLM Model Adapters | **Accepted** *(Jev Optional Pending Eval)* | `EVAL-01` *(Jev Benchmark)*, `OQ-04` *(Initial LLM/Budgets)* | Decisions 3, 4; Clarification B; `AP-08`, `AP-09` |
| **[ADR-004](file:///f:/Projects/Orbi/docs/04-adr/ADR-004-cryptographic-action-bound-approvals.md)** | Cryptographic Action-Bound Approval Integrity | **Accepted** | `EVAL-04` *(Task/LangGraph Handoff)*, `OQ-08` *(Matrix/TTLs)* | Decision 8; Clarification C; `AP-03`, `AP-06`, `AP-07` |
| **[ADR-005](file:///f:/Projects/Orbi/docs/04-adr/ADR-005-markdown-skill-packages-and-script-sandboxing.md)** | Versioned Markdown Skill Packages & Supporting Script Sandboxing | **Accepted** *(Sandbox Runtime Pending Eval)* | `EVAL-06` / `OQ-11` *(Sandbox Tech)* | Decision 7; Clarification D; `AP-01`, `AP-04`, `AP-06` |
| **[ADR-006](file:///f:/Projects/Orbi/docs/04-adr/ADR-006-layered-windows-companion-execution.md)** | Layered Windows Companion Execution & Home IoT Boundary | **Accepted** *(UIA/Vision Thresholds Pending Eval)* | `EVAL-02` *(UIA/Vision Spike)*, `OQ-12` | Decisions 9, 10; `AP-05`, `AP-06`, `AP-10` |
| **[ADR-007](file:///f:/Projects/Orbi/docs/04-adr/ADR-007-memory-manager-abstraction-over-cognee.md)** | Memory Manager Facade Encapsulating Cognee | **Accepted** *(Storage Backends Pending Eval)* | `EVAL-03` / `OQ-06` *(Backend Verification)* | Decision 6; `AP-04`, `AP-09` |

---

## Status Definitions

- **Accepted**: Architectural decision confirmed by the project owner and locked for HLD/LLD.
- **Accepted *(Pending Eval)***: Architectural pattern is confirmed, while specific vendor inclusion or sizing parameters remain explicitly gated on empirical spike verification (`EVAL-01`–`EVAL-06`) before finalizing HLD.
- **Superseded**: Replaced by a later ADR.
