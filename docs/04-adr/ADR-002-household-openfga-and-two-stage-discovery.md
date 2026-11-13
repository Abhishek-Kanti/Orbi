# ADR-002: Household-Extensible OpenFGA Model & Two-Stage Tool Discovery

- **Status**: Accepted
- **Date**: `2026-11-14`
- **Drivers**: Decision 2, Clarification A, Principles `AP-01`, `AP-02`, `AP-10`

---

## Context

Orbi starts as a single-owner personal assistant (`ASM-01`), yet future household members (e.g., family members with separate Gmail/Spotify accounts or shared/restricted home IoT access) should be supportable without tearing down identity and authorization tables. Additionally, letting an LLM see every registered tool regardless of user/agent authorization invites hallucinated tool IDs, unauthorized intent generation, and prompt-driven privilege escalation.

---

## Decision

1. **Household-Ready Identity & Ownership Model**:
   - All identity, task, memory, device, and authorization models distinguish `user:<id>`, `household:<id>`, `owner` relation, `agent:<id>`, `instance:<id>`, and `resource:<id>` in PostgreSQL and OpenFGA (`MOD-09`).
   - Initial deployment creates one primary owner and default household/instance scope; future household members require only new user records and OpenFGA tuples—no structural redesign.
2. **Mandatory 7-Step Agent & Tool Discovery Sequence (`Clarification A`)**:
   - Step 1: Manager selects specialized agent via `MOD-06` (Central Agent Registry).
   - Step 2: `MOD-04` (Agent Runtime) initializes the selected agent.
   - Step 3: Runtime retrieves the agent's configured tool catalog from `MOD-07` (Central Tool Registry).
   - Step 4: Discovery filters tools via `MOD-09` (OpenFGA) using `(agent_id, user_id, household_id, instance_id)` permissions.
   - Step 5: Only authorized tool definitions and schemas are provided to LangGraph.
   - Step 6: LLM selects strictly from the authorized tool subset.
   - Step 7: Every tool invocation routes through `MOD-08` (Tool Execution Gateway), which performs Stage-2 OpenFGA validation against the concrete target resource and arguments (`CON-11`).
3. **Hard Prohibitions**: Agents must never invent tool identifiers, grant themselves access, or execute tools directly.

---

## Consequences

### Positive
- Defense in depth: unauthorized tools are invisible to the LLM at planning time (Stage 1) **and** rejected fail-closed at execution time (Stage 2).
- Seamless onboarding of household members and future Home IoT devices (`MOD-16-EXT`) via ReBAC relationship tuples.

### Negative / Risks
- **Two-Stage Granularity (`CON-11`)**: Stage-1 discovery cannot always know the exact runtime `target_resource` ID (e.g., a specific email thread), so Stage-1 filters at capability/user/instance level while Stage-2 Gateway enforces exact resource-level OpenFGA checks.
- **Latency Overhead**: Batch OpenFGA `ListObjects` / `Check` calls during discovery require efficient batching/caching bounded by `policy_version` in HLD.
