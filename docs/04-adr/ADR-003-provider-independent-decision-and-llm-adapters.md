# ADR-003: Provider-Independent Decision Service & LLM Model Adapters

- **Status**: Accepted *(Optional Jev Provider Pending `EVAL-01`; Initial LLM/Budgets Open under `OQ-04`)*
- **Date**: `2026-11-14`
- **Drivers**: Decisions 3 & 4, Clarification B, Principles `AP-08`, `AP-09`

---

## Context

Orbi leverages System-2 generative reasoning (LangGraph) alongside fast structured System-1 decisions (`noul`, `choice`, `score` for triage, routing, and risk pre-screening). Relying exclusively on a single LLM vendor or an external early-stage specialized service like **Jev by TypeSafe** introduces availability, cost, latency, and lock-in risk.

---

## Decision

1. **Provider-Independent Decision Service (`MOD-05`)**:
   - Introduce an abstract `DecisionService` port supporting typed `noul`, `choice`, and `score` operations with explicit per-call `timeout_ms` budgets.
   - **Jev by TypeSafe is strictly optional** and gated behind **`EVAL-01`**. Core platform functionality must operate identically with Jev disabled or unavailable.
   - Implement mandatory fallback chains:
     - `DeterministicRuleProvider` (fast heuristics, exact schema/keyword rules, mandatory risk floors).
     - `LLMDecisionProvider` (structured-output classification executed via `MOD-04` Model Adapters).
   - On timeout, error, circuit-breaker trip, or low confidence, `MOD-05` transitions smoothly to the configured fallback and logs `provider_used` + `fallback_triggered` to `MOD-15`.
   - **Calibration Guard (`CON-12`)**: Apply provider-specific confidence thresholds; probabilistic decision outputs can *escalate* sensitivity/human approval upward, never downgrade hard OpenFGA or Registry security policies (`CON-02`).
2. **Provider-Independent LLM Model Adapters (`MOD-04`)**:
   - All LangGraph agents consume LLMs via an abstract `ModelAdapter` interface rather than vendor-specific SDK calls embedded in graph nodes.
   - Provider selection, model tier routing (per agent in `MOD-06`), fallback providers, and per-task/daily token & cost budgets (`MOD-03`) are declarative and externally configurable (`OQ-04`).

---

## Consequences

### Positive
- Eliminates single-vendor lock-in and external service fragility for Jev and frontier LLM APIs.
- Enables deterministic unit testing (`AP-08`) and controlled A/B benchmarking during `EVAL-01`.

### Negative / Risks
- Maintaining clean parity across heterogeneous providers (Jev RLCD probabilities vs. LLM JSON mode vs. deterministic rules) requires careful threshold tuning (`CON-12`).
- Fallback from Jev to an LLM increases tail latency during Jev outages; mitigated via strict `timeout_ms` and circuit breakers.
