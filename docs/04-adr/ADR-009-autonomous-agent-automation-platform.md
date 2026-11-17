# ADR-009: Autonomous Agent-Driven Automation Platform & Non-Inherited Delegation

- **Status**: Accepted *(Default Autonomy Quotas, Condition DSL & Phabric Packaging Pending `EVAL-08` / `OQ-15`–`OQ-17`)*
- **Date**: `2026-11-16`
- **Drivers**: Autonomous Agent-Driven Automation Clarification, `FR-AT-01`–`FR-AT-10`, `NFR-SEC-05`, `NFR-REL-04`, Principles `AP-01`–`AP-10`
- **Extends**: [ADR-001](file:///f:/Projects/Orbi/docs/04-adr/ADR-001-modular-monolith-and-resource-isolation.md), [ADR-002](file:///f:/Projects/Orbi/docs/04-adr/ADR-002-household-openfga-and-two-stage-discovery.md), [ADR-004](file:///f:/Projects/Orbi/docs/04-adr/ADR-004-cryptographic-action-bound-approvals.md)
- **Realized In**: [system-architecture.md](file:///f:/Projects/Orbi/docs/02-hld/system-architecture.md)

---

## Context

Treating automation merely as a user-configured cron scheduler—or attempting to keep live LLM sessions sleeping in memory while waiting for future times, events, or physical/system state changes—breaks core architectural requirements for an autonomous personal AI platform:
1. **Autonomous Objective Fulfillment**: Authorized agents must independently recognize when an ongoing responsibility requires future follow-ups, recurring evaluations, event subscriptions, or condition monitors and arrange or adapt schedules as circumstances evolve, without waiting for manual user re-prompting at every step.
2. **Cost & Reliability**: Keeping LangGraph/LLM sessions alive simply to poll time or monitor sensor/system states wastes tokens, exhausts RAM on single-VM OCI hosting (`AP-09`), and cannot survive process restarts.
3. **Authorization & Delegation Safety**: When one agent arranges future work for itself or another specialized agent (`ManagerAgent -> PlantCareAgent -> Notification/Watering`), implicit permission inheritance creates confused-deputy privilege escalation, infinite delegation loops, and automation explosions (`AP-02`, `AP-06`).
4. **Architectural Separation & Reusability**: Mixing trigger persistence, event/condition matching, task execution, and result notification inside one module violates `AP-03` and `AP-08`. Furthermore, the Automation Platform must be cleanly reusable across both Orbi and future **Phabric** products while respecting Orbi's Governed Modular Monolith (`AP-09`).

---

## Decision

1. **Introduce `MOD-17` (Autonomous Automation Platform) & `MOD-18` (Result & Delivery Manager) Inside the Governed Modular Monolith**:
   - Both `MOD-17` and `MOD-18` run as in-process Zone 1 modules within `orbi-core`, backed by existing `orbi-postgres` and `orbi-redis` containers (`AP-09` — zero new microservices or message brokers).
   - `MOD-17` is structured behind a framework-independent domain package boundary (`AutomationPlatformPort` / `phabric_automation` core contract), completely decoupled from LangGraph internals so it is directly reusable across Orbi and future **Phabric** products while Orbi's agents act as its first consumers.

2. **Strict Five-Way Separation of Responsibilities**:
   - **Agent Runtime (`MOD-04`)**: Domain reasoning, planning, deciding when future/recurring/event/condition execution or plan adaptation is needed, and emitting `ToolCallIntent` calls for `automation.*`. Never keeps LLM sessions alive waiting on timers/events.
   - **Autonomous Automation Platform (`MOD-17`)**: Owns persistent `AgentResponsibility` records, `AutomationDefinition` rules (with monotonic `generation` and `schedule_version`), immutable `AutomationScheduleVersion` history, complete **`CREATE`–`UPDATE`–`PAUSE`–`RESUME` (`misfire_policy`)–`CANCEL`–`DELETE`** lifecycle (with `DELETE` executing the mandatory 6-step authoritative PG `DELETED` + `generation += 1` + outbox transition *prior* to async schedule/payload cleanup), 6-type trigger registrations, event matching, LLM-free `ConditionEvaluator` execution, concurrency policies, and due dispatch into `MOD-03`.
   - **Task Manager (`MOD-03`)**: Exclusive owner of actual task execution lifecycle (`CREATED -> QUEUED -> RUNNING -> WAITING_FOR_USER -> ...`), centralized `HumanInteractionManager` suspension/resumption, budgets, retries, worker **PostgreSQL pre-dispatch generation/status revalidation** (blocking pre-queued Redis triggers invalidated by concurrent pause/cancel/update/delete as `SKIPPED_CANCELLED` / `SKIPPED_STALE_GENERATION`), cascade cancellation of pending `WAITING_FOR_USER` interactions on automation pause/cancel/update/delete, cooperative in-flight abort (`CANCELLED_AFTER_EXTERNAL_COMMIT`), and `ADR-004` approvals. Every triggered automation executes by creating a normal `MOD-03` task; `MOD-17` never runs a competing task engine.
   - **Tool Execution Gateway (`MOD-08`)**: Non-bypassable chokepoint (`AP-01`) governing both agent `automation.*` management calls and `MOD-17` `ConditionEvaluator` read-only state probes (`MOD-09` enforced on every call), and revalidating task cancellation, `task_version`, live `automation.generation`/`status`, live OpenFGA `policy_version`, and single-use approvals immediately before *every* external action dispatch (recording `CANCELLED_AFTER_EXTERNAL_COMMIT` honestly if a non-interruptible external side effect already transmitted during a cancellation race).
   - **Result & Delivery Manager (`MOD-18`)**: Handles structured result formatting, user notifications (`MOD-01` Telegram / `MOD-02` Dashboard), and post-task completion callbacks back to `MOD-17` (driving `schedule_followup` chains, responsibility summaries, and `task.completed` event emission).

3. **Standard Runtime Automation Capability (`automation.*` via `MOD-07` & `MOD-08`)**:
   - Expose typed automation tools through `MOD-07` / Stage-1 OpenFGA Discovery / `MOD-08` Gateway (`automation.create`, `automation.schedule`, `automation.subscribe_to_event`, `automation.create_condition_monitor`, `automation.schedule_followup`, `automation.update`, `automation.pause`, `automation.resume`, `automation.cancel`, `automation.delete`, `automation.get_execution_history`), distinguishing `cancel` (terminal `CANCELLED` state preserving definition + execution history) from `delete` (soft-tombstone/purge after active trigger cancellation and terminal execution completion while preserving redacted `MOD-15` audit trails). Contracts remain framework-independent and illustrative until LLD schema lock.

4. **Unified 6-Trigger Abstraction & Event vs. Condition Distinction**:
   - Support **`CRON`**, **`INTERVAL`**, **`ONE_TIME`**, **`EVENT`**, **`CONDITION`**, and **`MANUAL`** triggers under a common `TriggerSpec` abstraction.
   - Distinguish **Event Triggers** (discrete event messages on authorized event streams matched via topic + filter + debounce) from **Condition Triggers** (periodic evaluation by `MOD-17`'s `ConditionEvaluator` executing an authorized `READ_ONLY` probe via `MOD-08` and evaluating deterministic predicate/hysteresis rules **without invoking an LLM**). An LLM task is dispatched to `MOD-03` *only* when the condition evaluates `TRUE` and passes cooldown/concurrency checks.

5. **Ongoing Agent Responsibilities (`AgentResponsibility`)**:
   - Model ongoing multi-schedule work plans (e.g., long-lived care/monitoring objectives spanning sensor checks, periodic ML inference, condition monitors, action triggers, and schedule adaptation) as first-class `AgentResponsibility` aggregates linking component automations, shared budgets, versioned plans, and execution lineage.

6. **Generalized Ownership, Typed Targets, Permission Envelopes & Outbox Loop Safety**:
   - Every automation explicitly separates **`tenant_id`** (`PERSONAL | HOUSEHOLD | ORGANIZATION_FUTURE`, modeling household sharing uniformly while preserving room for future org tenants), **`owner_principal`**, **`creator_principal`**, typed **`execution_target`** (`AGENT` active; `WORKFLOW` only when deterministically defined; `SCRIPT` disabled until `EVAL-06` approves `MOD-11-SANDBOX`), **Trigger Definition**, **Task/Workflow Definition**, immutable **`PermissionEnvelope`**, **Execution Budgets**, and **Approval Policy**.
   - **Zero Inheritance & Immutable `PermissionEnvelope`**: Execution targets never inherit creator permissions and can never self-expand their envelope. In-envelope `update()` adjustments follow Point-2 policy, whereas expanding frequency, budget, resource scope, tools, concurrency, target, or approval requirements mandates renewed authorization/approval (`ADR-004`).
   - **3-Point Authorization (`MOD-09`)**: Verified deterministically at (1) **Creation**, (2) **Modification/Lifecycle Change**, and (3) **Every Execution Dispatch** (plus normal `MOD-04` Stage-1 discovery and `MOD-08` Stage-2 execution checks).
   - **Transactional Outbox (`automation_outbox`), Gateway Pre-Dispatch Cancellation & Cohesive 8-Control Loop Guard**: All state mutations + events commit atomically to PostgreSQL `orbi_automation.automation_outbox` with monotonic `generation` / `schedule_version` fences (Redis is transport only, with leased dispatchers, bounded retries, idempotent `event_id` consumers, `DEAD_LETTERED` handling, and stale-lease recovery). Both `MOD-03` workers *and* `MOD-08` Tool Execution Gateway immediately before dispatching any action revalidate authoritative PostgreSQL state (`tasks.status == 'RUNNING'`, `tasks.cancellation_requested == false`, matching `task_version`, and `automation.status == 'ACTIVE'` + matching `schedule_version` & `generation`) plus live `MOD-09` OpenFGA `policy_version`, dropping stale triggers (`SKIPPED_CANCELLED` / `SKIPPED_STALE_GENERATION`); running tasks support cooperative cancellation (`CANCELLED_AFTER_EXTERNAL_COMMIT` logged honestly without claiming rollback when non-interruptible external Gateway actions already committed). An integrated 8-control pipeline—propagated `correlation_id`/`trigger_chain_ids[]`/`trigger_depth`, `event_id` dedup, cycle & depth rejection, `min_interval_seconds` & cooldowns, envelope quotas, bounded non-cascading retries, sliding-window indirect-loop circuit breaker, and deterministic `PAUSED_SAFETY_TRIP` abort + `MOD-18` alert—prevents automation storms and `ADR-004` protects sensitive/physical actions (`EVAL-08`, `AUTO-EVAL-01`–`12`, `DOC-HLD-002` §C.2 & §D.10–§D.11).

---

## Consequences

### Positive
- Enables genuinely proactive, self-adapting agent responsibilities (such as continuous environment/care monitoring) without idle LLM token burn or volatile in-memory timers.
- Eliminates confused-deputy privilege escalation and feedback loops in multi-agent autonomous scheduling.
- Delivers clean domain boundaries (`phabric_automation` / `AutomationPlatformPort`) reusable across future Phabric products while preserving Orbi's single-VM modular monolith today.

### Negative / Risks
- **Trigger/Event Loop & Storm Risk (`CON-23`–`CON-26`)**: Condition monitors polling too aggressively or cross-agent event subscriptions triggering recursively can overwhelm `orbi-core` workers or LLM budgets without strict `min_interval_seconds`, cycle rejection, and aggregate rate limits (`OQ-15`, `EVAL-08`).
- **Two-Layer Persistence Reconciliation**: Coordinating `orbi_automation` schedule leases with `orbi_app.tasks` idempotency keys adds schema and crash-recovery discipline verified in `EVAL-08`.
- **Policy Complexity**: Supporting 3-point OpenFGA checks across owners, creators, target agents, responsibilities, and resources increases OpenFGA model size and requires verification under `EVAL-05` and `EVAL-08`.
