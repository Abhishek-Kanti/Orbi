# ADR-001: Governed Modular Monolith Runtime & Three-Zone Resource Isolation

- **Status**: Accepted *(OCI VM Sizing Pending `EVAL-05`; Dashboard Stack Open under `OQ-01`)*
- **Date**: `2026-11-14`
- **Drivers**: Decisions 1 & 5, Clarification E, Principles `AP-05`, `AP-08`, `AP-09`

---

## Context

Orbi combines deterministic control (Task Manager, OpenFGA, Gateway), Python-centric AI frameworks (LangGraph, Cognee), untrusted execution surfaces (Playwright browser automation, skill scripts), and local desktop automation (Windows Companion). We must define a runtime stack, deployment topology on Oracle Cloud Infrastructure (OCI), and execution isolation boundaries without falling into microservice sprawl (`AP-09`) or sacrificing security isolation (`AP-05`, `AP-08`).

---

## Decision

1. **Confirmed Core Runtime**: Standardize `orbi-core` on **Python 3.12+**, **FastAPI**, and **LangGraph**. Dashboard UI technology (`MOD-02`) remains open (`OQ-01`) for HLD selection.
2. **Deployment Topology**: Deploy initially via **modular Docker Compose on a single OCI VM**.
   - **Explicit Constraint**: OCI Always Free compute/RAM capacity is **not** assumed sufficient; final VM shape must be determined by **`EVAL-05`** benchmarking.
3. **Three-Zone Resource Isolation (`Clarification E`)**:
   - **Zone 1 (`orbi-core` In-Process Modules)**: `MOD-01`, `MOD-03`, `MOD-04`, `MOD-05`, `MOD-06`, `MOD-07`, `MOD-08`, `MOD-10` (facade), `MOD-11` (lifecycle manager), `MOD-12` (stateless SaaS connectors), and `MOD-15` (audit service) run as strictly bounded Python modules inside `orbi-core` API/worker containers.
   - **Zone 2 (Isolated Cloud Containers & Sandboxes)**: Dedicated Docker Compose services / ephemeral sandboxes isolate stateful engines (`orbi-openfga`, `orbi-postgres`, `orbi-redis`, verified `Cognee` storage backends) and untrusted cloud workloads (`MOD-13` Playwright Browser container, `MOD-11-SANDBOX` Skill Script Sandbox, `MOD-02` Dashboard UI).
   - **Zone 3 (Off-Cloud Local Execution)**: `MOD-14` Windows Companion executes solely on the user's Windows workstation over an outbound-initiated mTLS tunnel (`ASM-05`).

---

## Consequences

### Positive
- Eliminates premature distributed tracing, service-mesh overhead, and network serialization bugs between control/cognitive modules (`AP-09`).
- Preserves native ecosystem ergonomics for LangGraph and Cognee in Python 3.12+.
- Isolates memory-heavy or security-sensitive workloads (Chromium/Playwright, untrusted skill scripts, Windows UIA) outside `orbi-core`'s process space.

### Negative / Risks
- **Single-VM Blast Radius / RAM Contention**: Running PostgreSQL, Redis, OpenFGA, `orbi-core`, Playwright, and Cognee stores on one VM requires strict cgroup memory caps and benchmarking (`EVAL-05`).
- **Module Boundary Discipline**: In-process modules in Zone 1 rely on strict Python package import boundaries and architectural tests (`AP-08`) to avoid accidental bypass of `MOD-08`.
