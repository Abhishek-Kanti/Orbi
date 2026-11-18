# Orbi — EVAL-08 Core Reliability Spike Report: Automation Idempotency, RFC 8785 JCS & Version Fencing

- **Selected Evaluation ID**: **`EVAL-08`** *(Core Reliability Sub-Spike: Automation Idempotency, RFC 8785 Canonical Serialization, `schedule_version`/`generation`/`task_version` Fencing & Atomic Claim/Task Creation; aligned with `AUTO-EVAL-01`–`05`, `07`–`09`, `11`, `12` in `DOC-HLD-002` §D.11 and `DOC-PRD-003` §2)*
- **Date**: `2026-11-18` *(Final evidence review & closure assessment `2026-11-19`)*
- **Decision Status**: **`CONDITIONAL PASS`** *(Core Reliability Sub-Spike validated; full `EVAL-08` OpenFGA / Redis / scheduler / circuit-breaker / `OQ-15`–`17` gates remain open before `MOD-17` LLD)*
- **Database**: Real PostgreSQL `16-alpine` (`orbi-eval04-postgres` on `127.0.0.1:54328`, isolated database `orbi_eval08`, schemas `orbi_automation` and `orbi_app`)
- **Runtime**: Python `3.12.8`, `psycopg==3.3.9`, `pytest==9.2.0`
- **Test Execution Outcome**: **`9 passed in 7.14s`** (`0` failed, `0` skipped)

---

## 1. Evaluation ID Selection & Rationale

After inspecting `docs/00-product/scope-and-roadmap.md` (§2 Evaluation Registry `EVAL-01`–`EVAL-08`), `docs/02-hld/system-architecture.md` (§C.2, §D.1–D.3, §D.8–D.11, §E.5.4, §E.5.8), `docs/04-adr/ADR-004`, `docs/04-adr/ADR-009`, and the completed `EVAL-04` artifact (`evals/eval-04-langgraph-handoff/EVAL-04-REPORT.md`):
- **`EVAL-04`** already validated `MOD-03`/`MOD-04`/`MOD-08` task/checkpoint handoff, `task_version`, `claim_epoch` lease recovery, and `step_execution_receipts`.
- **`EVAL-08`** is the designated evaluation in the registry for `MOD-17` (`orbi_automation`) execution deduplication, `schedule_version` and `generation` fencing, condition monitors, 6-step `DELETE` ordering, and `MOD-17 -> MOD-03` task dispatch handoff.
- Therefore, this spike extends **`EVAL-08`** in isolation under `evals/eval-08-automation-idempotency/` without inventing an uncataloged evaluation ID or modifying `evals/eval-04-langgraph-handoff/` or `docs/`.

---

## 2. Hypothesis & Experiment Plan

### Hypothesis
Deterministic RFC 8785 JCS idempotency keys combined with PostgreSQL row-level locking (`SELECT ... FOR UPDATE`), `UNIQUE` ledger constraints (`INSERT ... ON CONFLICT DO NOTHING`), strict orthogonal versioning (`schedule_version`, `generation`, `task_version`), and bounded-lease two-phase recovery prevent duplicate executions, stale lifecycle execution, and orphaned task claims across concurrent workers, out-of-order deliveries, and mid-operation crashes—while remaining strictly distinct from downstream `MOD-08` tool-step receipt idempotency (`step_execution_receipts`).

### Experiment Scope
1. Validate RFC 8785 (JCS) canonical JSON serialization, UTF-16-BE key ordering, strict integer vs. string/float typing, and per-trigger-type `trigger_occurrence_id` stability.
2. Empirically prove `SELECT`-then-`INSERT` race failure vs. atomic PostgreSQL `UNIQUE` claim enforcement under `threading.Barrier` concurrency.
3. Validate `schedule_version` vs. `generation` lifecycle transitions (`CREATE`, `UPDATE`, `PAUSE`, `RESUME`, `CANCEL`, 6-step `DELETE`) and concurrent update-vs-claim serializability.
4. Validate single-transaction atomic claim+task rollback on crash AND two-phase (`CLAIMED_PENDING_TASK` -> `tasks`) bounded-lease crash recovery + `claim_epoch` stale-worker fencing.
5. Validate task-level `task_version` optimistic CAS (`MOD-03`), rejecting stale approvals and stale cancellations.
6. Validate condition trigger rising-edge occurrence creation (`FALSE -> TRUE` vs. sustained `TRUE -> TRUE` vs. `TRUE -> FALSE -> TRUE`).
7. Validate two-layer separation between `MOD-17` occurrence deduplication and `MOD-08` tool-step receipts (`PROVIDER_IDEMPOTENCY_KEY` vs. `NON_IDEMPOTENT_NO_RECONCILE`).

---

## 3. Isolated Prototype & Automated Test Results

### Files Created (`evals/eval-08-automation-idempotency/`)
- [docker-compose.yml](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/docker-compose.yml)
- [pyproject.toml](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/pyproject.toml)
- [canonical.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/canonical.py): RFC 8785 JCS canonicalizer, 6-trigger `trigger_occurrence_id` builders, `MOD-17` & `MOD-08` SHA-256 key generators.
- [db.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/db.py): PostgreSQL DDL for `orbi_automation` & `orbi_app` on database `orbi_eval08`.
- [engine.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/engine.py): Lifecycle/fencing engine, `naive_select_then_insert_race`, single-tx & two-phase claim/recovery, condition edge monitor, `task_version` CAS, and `MOD-08` receipts.
- [test_eval08_automation_idempotency.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/tests/test_eval08_automation_idempotency.py): Automated test suite (`9` test functions).

### Pytest Results (`9 passed in 7.14s`)
```text
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_01_rfc8785_jcs_canonical_vectors_and_type_safety PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_02_naive_select_then_insert_fails_under_concurrency PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_03_duplicate_delivery_and_concurrent_workers_atomic_claim PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_04_schedule_version_and_generation_lifecycle_fencing PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_05_concurrent_schedule_update_and_event_claim_serializability PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_06_crash_between_claim_and_task_creation_atomic_and_two_phase_recovery PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_07_task_version_cas_concurrent_updates_and_stale_approval_cancel PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_08_condition_trigger_edge_transition_semantics PASSED
evals\eval-08-automation-idempotency\tests\test_eval08_automation_idempotency.py::test_09_two_layer_idempotency_mod17_vs_mod08_and_uncertain_outcome PASSED
```

*(See **Section 8** for the full scenario-by-scenario closure matrix covering `CONFIRMED`, `FAILED`, and `OPEN / NOT EVALUATED` statuses.)*

---

## 4. Concurrent Execution & Failure-Injection Evidence

1. **`SELECT`-then-`INSERT` Concurrency Failure (`test_02`)**:
   - Three threads opened separate PostgreSQL connections against `orbi_automation.naive_race_executions` (no `UNIQUE` constraint), synchronized at `select_barrier` to read `0` rows simultaneously, then synchronized at `insert_barrier` to insert simultaneously.
   - **Observed Output**: `inserted_rows == 3` — demonstrating deterministically that application-level read-then-write checks fail under concurrent workers.
2. **Atomic `ON CONFLICT (idempotency_key)` Claim (`test_03`)**:
   - Six threads synchronized behind `threading.Barrier(6)` executed `claim_and_dispatch_occurrence` concurrently.
   - **Observed Output**: `1` thread returned `CLAIMED_AND_DISPATCHED`, `5` returned `DUPLICATE_DEDUPLICATED` pointing to the winner's `(execution_id, task_id)`, and PostgreSQL contained `1` execution row and `1` task row. A late replay returned `DUPLICATE_DEDUPLICATED` with the same IDs (`1` task total).
3. **Concurrent Update vs. Claim Race (`test_05`)**:
   - Across `10` synchronized `threading.Barrier(2)` races between `update_automation(v1->v2, g1->g2)` and `claim_and_dispatch_occurrence(v1, g1)`: because both statements acquire `SELECT ... FOR UPDATE` on `automation_definitions`, PostgreSQL strictly serialized every race; zero `v1/g1` claims ever succeeded after `update_automation` committed `v2/g2`.
4. **Crash Injection, Lease Fencing & Uncertain-Outcome Reconciliation (`test_06` & `test_09`)**:
   - *Single-TX crash (`6a`)*: `SimulatedCrashError` raised after inserting `automation_executions` and before inserting `orbi_app.tasks` rolled back cleanly (`0` execution rows, `0` task rows). Retry committed `1` execution and `1` task.
   - *Two-phase crash & recovery (`6b`)*: Tx 1 committed `CLAIMED_PENDING_TASK` (`claim_epoch=1`, `lease_ttl_seconds=0.15`), then raised `SimulatedCrashError` before Tx 2. A concurrent redelivery returned `DUPLICATE_DEDUPLICATED` without creating a second claim. After lease expiry (`0.25s`), `recover_abandoned_executions` bumped `claim_epoch=2`, created the single task, and `stale_worker_finalize_claimed_execution(claim_epoch=1)` raised `StaleClaimEpochFencedError`.
   - *Two-phase superseded crash (`6c`)*: Tx 1 crashed leaving `CLAIMED_PENDING_TASK`, then `update_automation` bumped `v1->v2` / `g1->g2`. After lease expiry (`0.25s`), `recover_abandoned_executions` re-checked `automation_definitions FOR UPDATE`, transitioned the execution to `ABORTED_SUPERSEDED` (`REJECTED_FENCED` with reason `ABORTED_SUPERSEDED_DURING_RECOVERY`), and created `0` tasks.
   - *Tool step crashes (`7a`–`7c`)*: `SimulatedCrashError` right after provider execution left `step_execution_receipts.status = 'DISPATCHING'`; retry moved `DISPATCHING -> UNKNOWN_OUTCOME`, deduplicated `PROVIDER_IDEMPOTENCY_KEY` (`1` provider call), prohibited blind retry on `NON_IDEMPOTENT_NO_RECONCILE` (`UncertainOutcomeError`, `1` provider call), and reconciled `RECONCILABLE_READ_PROBE` via `probe_fn` (`reconciled=True`, `1` provider call, `1` probe call, subsequent replay `replayed=True`).

---

## 5. Canonical Serialization & Two-Stage Identity Normalization Contract

### 1. RFC 8785 (JCS) Compliance vs. Application Schema Policy

[canonical.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/canonical.py) implements a **strictly separated two-stage pipeline** so pure RFC 8785 (JSON Canonicalization Scheme) canonicalization is never conflated with application-level normalization or schema restrictions:

- **Stage 1 — Application-Level Identity Construction & Normalization** (`build_trigger_occurrence_id`, `build_execution_identity_payload`, `compute_tool_action_idempotency_key`):
  Validates strict integer types, normalizes UUIDs and timezone-aware timestamps into unambiguous canonical strings, and builds a pure JSON-serializable `dict`.
- **Stage 2 — Pure RFC 8785 (JCS) Canonical Serialization + UTF-8 + SHA-256** (`jcs_canonicalize` / `rfc8785_canonicalize`):
  Serializes standard JSON values (`None`, `bool`, `int`, `float`, `str`, `list`, `dict`) strictly per **RFC 8785** with zero string case-folding and zero implicit non-JSON type coercion (raw `UUID` objects raise `TypeError` if passed directly to `jcs_canonicalize`):
  - **RFC 8785 §3.2.2.2 (String Escaping)**: Escapes `\b`, `\t`, `\n`, `\f`, `\r`, `\"`, `\\`, and `\u00xx` only for control characters `U+0000`–`U+001F`; preserves raw Unicode (`café`, `東京`, `/`) without escaping `/`; rejects lone UTF-16 surrogates (`U+D800`–`U+DFFF`) with `ValueError`.
  - **RFC 8785 §3.2.2.3 (Number Formatting)**: `_format_es6_number` implements **ECMAScript 6 §7.1.12.1 (`NumberToString`)** over finite IEEE-754 double-precision floats (`NaN` / `±Infinity` raise `ValueError`; `-0.0 -> "0"`; fixed notation for $-5 \le n \le 21$; shortest round-trip decimal digits with no leading zeros in exponents when scientific notation applies). Verified in `test_01` via `rfc8785_canonicalize` / `jcs_canonicalize(..., allow_floats=True)` against RFC 8785 §3.2.2.3 / Appendix B vectors (`0.0 -> "0"`, `-0.0 -> "0"`, `1.0 -> "1"`, `4.50 -> "4.5"`, `-4.50 -> "-4.5"`, `2e-3 -> "0.002"`, `1e-6 -> "0.000001"`, `1e-7 -> "1e-7"`, `1e20 -> "100000000000000000000"`, `1e21 -> "1e+21"`, `1e30 -> "1e+30"`, `-1e21 -> "-1e+21"`, `333333333.33333329 -> "333333333.3333333"`).
  - **RFC 8785 §3.2.3 (Property Sorting & Whitespace)**: Recursively sorts object keys by UTF-16 code units (`k.encode("utf-16-be")`, verified by `"🚀"` `D8 3D DE 80` sorting before `"\uffff"` `FF FF`) and emits zero inter-token whitespace.

> [!NOTE]
> **Experimental Prototype Status & Production RFC 8785 Guidance**: Because this isolated evaluation spike keeps minimal dependencies ([pyproject.toml](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/pyproject.toml): `psycopg` and `pytest` only), the hand-written RFC 8785 serializer in [canonical.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/canonical.py) is **experimental prototype code**. Although tested here against §3.2.2.2, §3.2.2.3 / ES6 IEEE-754 Appendix B vectors, and §3.2.3 UTF-16-BE ordering rather than a restricted subset, Orbi should **evaluate an established, maintained RFC 8785-compatible library** (such as `rfc8785`) for production implementation rather than automatically carrying the custom prototype implementation forward.

#### Why `allow_floats=False` Is an Application Policy, Not an RFC 8785 Requirement

- **RFC 8785 natively supports IEEE-754 floats**: `rfc8785_canonicalize(value)` (`allow_floats=True`) conforms completely to RFC 8785 §3.2.2.3 and deterministically serializes finite floats such as `1.0 -> "1"`, `4.50 -> "4.5"`, and `1e21 -> "1e+21"`.
- **Orbi's application schema forbids floats in deterministic identity payloads**: Under RFC 8785, `1.0` and `1` both serialize to `"1"`, and arbitrary floating-point arithmetic can introduce representation jitter across callers. Every field in Orbi's `MOD-17` and `MOD-08` identity payloads is strictly an integer counter/version or canonical string. Enabling **`allow_floats=False`** inside `compute_automation_idempotency_key` and `compute_tool_action_idempotency_key` is therefore an **Orbi application-layer input validation policy** that fails closed (`TypeError`) on accidental `float` inputs (and `bool` passed as `int`) before RFC 8785 runs—never a limitation or modified subset of RFC 8785 itself.

### 2. Identity Normalization Boundary & Collision-Prevention Contract

Stage 1 normalizes **only** representation-equivalent identifiers whose underlying standard explicitly defines multiple encodings for the exact same logical identity, while preserving all caller/tenant/external identifiers verbatim so distinct logical identities can never be accidentally collapsed:

| Identity Field | Layer | Stage 1 Pre-JCS Normalization | Rationale & Collision-Prevention Guarantee |
| :--- | :---: | :--- | :--- |
| **`automation_id`** | `MOD-17` | Parsed via `UUID(str(val))`, formatted as lowercase 36-char hyphenated hex (`RFC 9562` §4) | Prevents uppercase vs. lowercase hex or `UUID` vs. `str` formatting of the same 128-bit UUID from creating duplicate executions (`"A1B2..."` and `"a1b2..."` refer to one UUID). Validated to fail on malformed UUIDs. Stage 2 JCS still preserves string case if called directly (`jcs_canonicalize({"id": "ABC"}) == '{"id":"ABC"}'`). |
| **`task_id`**, **`execution_id`**, **`approval_id`** *(nullable -> `null`)* | `MOD-08` | Parsed via `UUID(str(val))`, formatted as lowercase 36-char hyphenated hex | Same `RFC 9562` 128-bit UUID canonicalization before computing Gate 2 `action_idempotency_key`. Distinct UUIDs remain collision-free (`2^{-122}` entropy for UUIDv4). |
| **`trigger_type`** | `MOD-17` | Validated against `TriggerType` enum -> canonical uppercase string (`CRON`, `INTERVAL`, `ONE_TIME`, `EVENT`, `CONDITION`, `MANUAL`) | Rejects unknown/untyped strings; prevents enum object vs. string literal mismatch. |
| **`scheduled_at_utc`** *(`CRON` / `ONE_TIME`)* | `MOD-17` | Rejects naive `datetime` (`ValueError`); converts timezone-aware `datetime` via `.astimezone(timezone.utc).isoformat(timespec="auto")` -> prefixed `"cron:"` / `"onetime:"` | `DOC-HLD-002` §D.6 uses PostgreSQL `TIMESTAMPTZ` (`next_run_at`) and `run_at_utc` without guaranteeing whole-second-only precision for `CRON` or `ONE_TIME`. Using `timespec="auto"` (instead of `"seconds"`) preserves full sub-second (microsecond) precision whenever `microsecond != 0` so distinct fractional-second timestamps (e.g., `08:00:00+00:00` vs. `08:00:00.500000+00:00` vs. `08:00:00.500001+00:00`) are **never silently truncated into the same execution identity**, while normalizing equivalent timezone offsets and trailing-zero representations (e.g., `.5+00:00` vs. `.500000+05:30` -> `.500000+00:00`; `.000000+00:00` -> exact whole second `+00:00`). |
| **`schedule_version`**, **`generation`**, **`anchor_epoch_sec`**, **`interval_sec`**, **`tick_index`**, **`rising_edge_seq`** | `MOD-17` | No coercion: strict `type(x) is int and x >= 1` (or `int` for interval metadata) | Rejects `bool` (`True == 1`), `"1"`, and `1.0` via `TypeError`. Preserves integer JSON type (`1` vs `"1"`). |
| **`tenant_id`**, **`source_event_id`**, **`monitor_id`**, **`request_idempotency_id`** | `MOD-17` | **Preserved verbatim** (no case-folding, trimming, or Unicode NFKC rewriting; non-empty `str`) | Case-sensitive external/webhook IDs or tenant slugs (e.g., `"Webhook_AbC"` vs. `"webhook_abc"`, `"tenant:Personal"` vs. `"tenant:personal"`) are treated as distinct logical identities and produce distinct SHA-256 digests (`test_01`). Prefix separators (`cron:`, `interval:...:every:...:tick:`, `event:`, `condition:...:edge:`, `manual:`) prevent cross-trigger-type string aliasing. |
| **`logical_step_id`**, **`tool_id`**, **`tool_version`**, **`target_resource`**, **`arguments` (strings)** | `MOD-08` | **Preserved verbatim** (`arguments` additionally rejects floats under application policy before RFC 8785 hashing) | Preserves case-sensitive file paths, API parameters, tool versions, and targets verbatim while guaranteeing key-order invariance for nested `arguments` JSON objects. |

### 3. Tenant/Owner Scope Verification
Even though `automation_id` is generated as a UUIDv4 (globally unique across the cluster), **retaining `tenant_id` inside the canonical identity payload is necessary (`CONFIRMED`)** because:
1. It guarantees domain separation across tenant scopes (`PERSONAL | HOUSEHOLD | ORGANIZATION_FUTURE`) if deterministic or imported UUIDs are ever reused in test/migration fixtures (`test_01` proves `"tenant:personal"`, `"tenant:household_2"`, and `"tenant:Personal"` yield distinct SHA-256 digests for the exact same `automation_id` and `trigger_occurrence_id`).
2. `engine.py` also includes a tenant-scope mismatch check (`auto["tenant_id"] != tenant_id -> TENANT_SCOPE_MISMATCH`) inside `claim_and_dispatch_occurrence` *(note: live DB mismatch rejection at claim runtime was not exercised by an integration assertion in `test_01`–`test_09` and is flagged honestly in §8)*.

### 4. Observed Test Vectors

| Vector Name | Canonical RFC 8785 Output | SHA-256 Digest |
| :--- | :--- | :--- |
| **`cron_v1_g1_personal`** *(and scrambled key order / uppercase UUID / `UUID` obj / `+05:30` tz)* | `{"automation_id":"a1b2c3d4-e5f6-4789-abcd-0123456789ab","generation":1,"schedule_version":1,"schema":"orbi.automation.execution_idempotency.v1","tenant_id":"tenant:personal","trigger_occurrence_id":"cron:2026-11-18T08:00:00+00:00","trigger_type":"CRON"}` | `9dba104f92821c1eb1038a0e82960375f34b23b22a7e498a81847910a2b131ae` |
| **`cron_diff_occ`** *(`09:00` vs `08:00`)* | `{"automation_id":"a1b2c3d4-e5f6-4789-abcd-0123456789ab","generation":1,"schedule_version":1,"schema":"orbi.automation.execution_idempotency.v1","tenant_id":"tenant:personal","trigger_occurrence_id":"cron:2026-11-18T09:00:00+00:00","trigger_type":"CRON"}` | `b0a993aaa9649fcaa7ed02330b1b2bfcac59c07d37197c22c64b3d9fbddd02f7` |
| **`cron_diff_tenant`** *(`tenant:household_2`)* | `{"automation_id":"a1b2c3d4-e5f6-4789-abcd-0123456789ab","generation":1,"schedule_version":1,"schema":"orbi.automation.execution_idempotency.v1","tenant_id":"tenant:household_2","trigger_occurrence_id":"cron:2026-11-18T08:00:00+00:00","trigger_type":"CRON"}` | `1e67df783746e131610f3e84ecc9dafb57630d2a6e9db8b8c1293f640bb1003d` |
| **`cron_v2_g2`** *(`schedule_version=2, generation=2`)* | `{"automation_id":"a1b2c3d4-e5f6-4789-abcd-0123456789ab","generation":2,"schedule_version":2,"schema":"orbi.automation.execution_idempotency.v1","tenant_id":"tenant:personal","trigger_occurrence_id":"cron:2026-11-18T08:00:00+00:00","trigger_type":"CRON"}` | `302dcf1c09f02f5458fa324360ef7110eb49d965bbcbccad5ab71c0d5e315267` |
| **`utf16_sort`** *(`{"\uffff":1,"🚀":2}` UTF-16-BE key order)* | `{"🚀":2,"\uffff":1}` | `ff10931b20632c59631a766b324a74e8d3fbdc28b54fb083abb1496985b5627b` |
| **`int_vs_str` (integer)** | `{"v":1}` | `afbf9d0f3560b0fd7795e81c42a0a79ee6b6fc67e064f77826aee642cad28d91` |
| **`int_vs_str` (string)** | `{"v":"1"}` | `b8513f1a0c28d8dd9b3b175bee09eabca97c4819614ec9a2df7442a5b4eff8d7` |
| **`rfc8785_es6_floats`** *(`allow_floats=True`: `1e20`, `1e21`, `-0.0`, `1e-7`, `333333333.33333329`)* | `{"v":100000000000000000000}`, `{"v":1e+21}`, `{"v":0}`, `{"v":1e-7}`, `{"v":333333333.3333333}` | Verified exact ES6/RFC 8785 §3.2.2.3 string equivalence in `test_01` *(rejected under `allow_floats=False`)* |

---

## 6. Version Transition Contract & Three Versioning Concepts

### 1. Three Orthogonal Version Counters

| Field | Authoritative Owner | Scope | Purpose |
| :--- | :--- | :--- | :--- |
| **`schedule_version`** | `MOD-17` (`orbi_automation.automation_definitions`) | Definition / schedule spec revision | Prevents triggers generated under an old trigger spec (e.g., 8 AM cron) from executing after the schedule changed (e.g., 9 AM). Appends an immutable audit row to `automation_schedule_versions`. |
| **`generation`** | `MOD-17` (`orbi_automation.automation_definitions`) | Monotonic automation lifecycle epoch | Fences stale pre-pause, pre-cancel, pre-delete, pre-circuit-breaker, or superseded queued events **even when `schedule_version` stays identical** (e.g., across `PAUSE -> RESUME` or `CANCEL`). |
| **`task_version`** | `MOD-03` (`orbi_app.tasks`) | Individual task instance state revision | Optimistic CAS counter (`UPDATE ... WHERE task_id = :id AND task_version = :expected`) preventing lost updates across concurrent task workers, stale HITL approvals, and late cancellations (`EVAL-04` + `test_07`). |

They are strictly **orthogonal**: changing `generation` or `schedule_version` on `automation_definitions` fences new occurrence dispatch (and cascades cancellation to waiting tasks/interactions), whereas `task_version` governs state-machine concurrency within an already-materialized `orbi_app.tasks` row.

### 2. Exact Lifecycle Operation Transition Matrix

Per `DOC-HLD-002` §D.5 & §D.10 Diagram 13 and implemented in [engine.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/engine.py):

| Operation | `schedule_version` Change | `generation` Change | `status` Transition | Atomic Outbox / Ledger Side Effects *(Same PG Transaction)* | Verified in Spike? |
| :--- | :---: | :---: | :--- | :--- | :---: |
| **`CREATE`** | Set to `1` | Set to `1` | `[*] -> ACTIVE` | Insert `automation_schedule_versions(v=1, g=1)`, insert `automation_outbox('AUTOMATION_CREATED')`, write `MOD-15` audit event. | **Live** (`create_automation`) |
| **`UPDATE`** *(in-envelope spec/schedule edit)* | **`+1`** (`v -> v + 1`) | **`+1`** (`g -> g + 1`) | `ACTIVE / PAUSED -> unchanged` *(disallowed if `CANCELLED`/`DELETED`)* | Insert immutable `automation_schedule_versions(v+1, g+1)`, emit `AUTOMATION_UPDATED` outbox event, invoke waiting-task cascade helper. | **`CONFIRMED`** (`test_04`, `test_05`; cascade verified on `PAUSE`) |
| **`PAUSE`** | Unchanged (`v`) | **`+1`** (`g -> g + 1`) | `ACTIVE -> PAUSED` | Emit `AUTOMATION_PAUSED` outbox event, cascade `CANCELLED` (`task_version + 1`) to `QUEUED`/`WAITING_FOR_USER` tasks, `CANCELLED` (`interaction_version + 1`) to open `interaction_requests`, and `INVALIDATED` to pending/approved `approval_requests`. | **`CONFIRMED`** (`test_04` incl. HITL cascade) |
| **`PAUSED_SAFETY_TRIP`** *(loop/quota circuit breaker)* | Unchanged (`v`) | **`+1`** (`g -> g + 1`) | `ACTIVE -> PAUSED_SAFETY_TRIP` | HLD §D.8 / §D.10 Diagram 13: abort chain before task creation, record `MOD-15` audit event, alert `MOD-18`; requires owner reset + `generation + 1` to resume. | **`OPEN / NOT EVALUATED`** *(enum defined in `engine.py`; auto-trip deferred to `AUTO-EVAL-10`)* |
| **`RESUME`** | Unchanged (`v`) | **`+1`** (`g -> g + 1`) | `PAUSED` *(or `PAUSED_SAFETY_TRIP`)* `-> ACTIVE` | Emit `AUTOMATION_RESUMED` outbox event; older pre-pause `g` events fail revalidation (`generation` mismatch). Live OpenFGA Point-2 check and `misfire_policy` reconciliation are HLD requirements outside this PG spike. | **`CONFIRMED` *(generation bump)*** / **`OPEN`** *(OpenFGA + `misfire_policy`)* |
| **`CANCEL`** | Unchanged (`v`) | **`+1`** (`g -> g + 1`) | `ACTIVE / PAUSED -> CANCELLED` | Emit `AUTOMATION_CANCELLED` outbox event, invoke waiting-task cascade helper; retains definition & ledger history. | **`CONFIRMED`** (`test_04` fencing) |
| **`DELETE`** *(6-Step Authoritative Order)* | Unchanged (`v`) | **`+1`** (`g -> g + 1`) | `ACTIVE / PAUSED / CANCELLED -> DELETED` | In PG TX: set `status = 'DELETED'`, `next_run_at = NULL`, scrub `trigger_spec = '{}'` & `task_template = '{}'`, emit `AUTOMATION_DELETED_CLEANUP` outbox event, cascade waiting tasks/HITL, write `AUTOMATION_DELETED` audit row. *(Note: `test_04` asserts `status`, `generation`, `trigger_spec == {}`, and `SKIPPED_STATUS_DELETED`; async Redis/timer/payload purge is not tested.)* | **`CONFIRMED` *(PG TX fence)*** / **`OPEN`** *(async Redis cleanup)* |

---

## 7. Database Constraints & Atomic Version-Fenced Claim Execution

### 1. Authoritative Uniqueness Constraints
- `orbi_automation.automation_executions.idempotency_key TEXT NOT NULL UNIQUE` — enforces single-winner execution ledger claims via `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`.
- `orbi_app.tasks.execution_id UUID NOT NULL UNIQUE` — guarantees at most one `MOD-03` task row can ever be materialized per logical `execution_id`, even under crash recovery or two-phase retries.
- `orbi_automation.automation_schedule_versions (automation_id, schedule_version) UNIQUE` — preserves immutable schedule revision history.
- `orbi_app.step_execution_receipts.action_idempotency_key TEXT NOT NULL UNIQUE` & `UNIQUE (task_id, execution_id, logical_step_id)` — anchors `MOD-08` step receipts independently of `MOD-17` occurrence deduplication.

### 2. Why Lifecycle Changes and Execution Claims Are Mutually Atomic
Every lifecycle mutation (`update_automation`, `_transition_lifecycle` for `PAUSE`/`RESUME`/`CANCEL`, `delete_automation_6step`) **and** every execution validation/claim path (`claim_and_dispatch_occurrence`, `claim_occurrence_two_phase` Tx 1, `recover_abandoned_executions`, `evaluate_condition_poll`) executes the exact same PostgreSQL row-lock protocol inside a single ACID transaction:

1. **Exclusive Row Lock Acquisition**: The transaction first executes `SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE`.
2. **PostgreSQL Serialization Guarantee**: If a lifecycle transaction (`UPDATE`, `PAUSE`, `RESUME`, `CANCEL`, or `DELETE`) and a worker's `claim_and_dispatch_occurrence` execute concurrently (`test_05`), PostgreSQL blocks the second transaction on the `automation_definitions` row lock until the first commits (releasing the lock with its new `status`, `schedule_version`, and `generation`) or rolls back. Under PostgreSQL `READ COMMITTED`, once the waiter acquires the `FOR UPDATE` lock, PostgreSQL re-fetches the latest committed tuple—preventing torn reads between `status`, `schedule_version`, and `generation`.
3. **In-Lock Validation & Atomic Commit**:
   - Inside that same locked transaction, **Single-TX Atomic Dispatch** (`ADR-001` / `DOC-HLD-002` §L.1) verifies `(tenant_id == auto.tenant_id, status == 'ACTIVE', generation == event.generation, schedule_version == event.schedule_version)` -> inserts `automation_executions` (`ON CONFLICT (idempotency_key) DO NOTHING`) -> inserts `orbi_app.tasks` -> updates `automation_executions.task_id` -> writes `audit_events` -> commits atomically. Any stale generation/version/status is rejected before any `automation_executions` winner row or `tasks` row is created, and any crash mid-transaction rolls back both schemas cleanly (`test_06a`).
   - **Two-Phase Recoverable Dispatch** (if `orbi_automation` / Phabric `CON-26` and `orbi_app` decouple across pools/outbox): Tx 1 holds the `FOR UPDATE` lock on `automation_definitions` when committing `CLAIMED_PENDING_TASK` (`claim_epoch = 1`, bounded `lease_expires_at`); Tx 2 (`_finalize_claimed_execution_task`) or lease expiry recovery (`recover_abandoned_executions`) re-locks `automation_definitions` (`FOR UPDATE`), revalidates live `(status == 'ACTIVE', schedule_version, generation)` *(marking stale abandoned claims `ABORTED_SUPERSEDED` if an `UPDATE`/`PAUSE`/`CANCEL`/`DELETE` committed while the first worker was crashed)*, bumps `claim_epoch += 1` via CAS (fencing stale workers), and inserts `orbi_app.tasks` (`ON CONFLICT (execution_id) DO NOTHING`).
4. **Task CAS (`MOD-03`)**: `UPDATE orbi_app.tasks SET status = :new, task_version = task_version + 1 WHERE task_id = :id AND task_version = :expected AND status = :expected_status` guarantees linearizable single-winner state transitions on materialized tasks.

---

## 8. Final Evidence Review: Scenario-by-Scenario Closure Matrix & 9-Area Assessment

### 1. Scenario-by-Scenario Closure Matrix

Status definitions:
- **`CONFIRMED`**: Directly demonstrated by runnable prototype execution and assertions in [test_eval08_automation_idempotency.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/tests/test_eval08_automation_idempotency.py) against PostgreSQL `16-alpine` (`orbi_eval08`).
- **`FAILED`**: Empirically tested and shown to be unsafe or defective (either an expected anti-pattern failure or a prototype bug caught during review and remediated).
- **`OPEN / NOT EVALUATED`**: Requirement in `DOC-HLD-002` / `DOC-PRD-003` outside this PostgreSQL core reliability sub-spike or not directly verified by live integration tests.

| Scenario / Requirement | Closure Status | Direct Empirical Evidence or Specific Gap |
| :--- | :---: | :--- |
| Duplicate event delivered sequentially after claim | **`CONFIRMED`** | `test_03` (`3b`): replay returns `DUPLICATE_DEDUPLICATED` referencing winner `(execution_id, task_id)`; `count_tasks_for_automation == 1`. |
| Naive `SELECT`-then-`INSERT` claim without DB uniqueness | **`FAILED`** | `test_02`: `3` barrier-synchronized threads all passed `SELECT` and inserted `3` duplicate rows into `naive_race_executions` (proves anti-pattern unsafe). |
| Concurrent workers claim identical occurrence atomically | **`CONFIRMED`** | `test_03` (`3a`): `6` threads behind `threading.Barrier(6)` produced `1` `CLAIMED_AND_DISPATCHED` winner and `5` `DUPLICATE_DEDUPLICATED`; `1` execution and `1` task row committed. |
| Canonical key-order, whitespace & integer/string invariance (RFC 8785 JCS + Stage 1 normalization) | **`CONFIRMED`** | `test_01`: `d_order1` vs `d_order2`, UTF-16-BE key ordering (`"🚀"` before `"\uffff"`), Stage 1 vs Stage 2 separation (`UUID` rejected by JCS, string case preserved by JCS and lowercased in Stage 1), integer `1` vs `"1"`, float/`bool`/surrogate rejection, and ES6 Appendix B float vectors under `allow_floats=True`. |
| Initial prototype `isoformat(timespec="seconds")` truncation & `repr(float)` serializer | **`FAILED`** *(Caught & Fixed)* | Identified during verification review: `timespec="seconds"` silently truncated sub-second `TIMESTAMPTZ` values and `repr(float)` diverged from ES6 on `1e-7` / `1e16`–`1e20`; replaced with `timespec="auto"` + `_format_es6_number` and verified by `test_01` regression checks. |
| Trigger occurrence stability, sub-second timestamp precision, timezone normalization & naive rejection | **`CONFIRMED`** | `test_01`: distinct fractional seconds (`.0`, `.500000`, `.500001`) produce `3` distinct SHA-256 keys for both `CRON` and `ONE_TIME`; UTC/IST/EST & `.5`/`.500000` normalize identically; naive `datetime` raises `ValueError`; all 6 trigger builders + case-sensitive `EVENT`/`tenant_id` non-collision verified. |
| Different tenant scope hash isolation & runtime cross-tenant rejection | **`CONFIRMED` *(Hash)* / `OPEN` *(Runtime Test)*** | `test_01` proves distinct SHA-256 digests (`tenant:personal` vs `tenant:household_2` vs `tenant:Personal`). `engine.py` implements `auto["tenant_id"] != tenant_id -> TENANT_SCOPE_MISMATCH`, though runtime DB rejection was not invoked in `test_04`. |
| Stale `schedule_version` & `generation` fencing (`UPDATE`, `PAUSE`, `RESUME`, `CANCEL`, `DELETE`) | **`CONFIRMED`** | `test_04`: `v1/g1` rejected after `UPDATE` (`v2/g2`); `v2/g2` rejected after `PAUSE` (`SKIPPED_STATUS_PAUSED`) and after `RESUME` bumped `generation` to `4` while `schedule_version` stayed `2` (`SKIPPED_STALE_GENERATION`); `CANCEL` (`SKIPPED_STATUS_CANCELLED`) and PG TX `DELETE` (`SKIPPED_STATUS_DELETED`, `trigger_spec == {}`) verified; `PAUSE` waiting-task (`task_version + 1`, `CANCELLED`) & HITL (`interaction_requests = CANCELLED`, `approval_requests = INVALIDATED`) cascade verified. |
| Concurrent lifecycle `UPDATE` vs. event claim race | **`CONFIRMED`** | `test_05`: `10` synchronized `threading.Barrier(2)` races serialized cleanly via `SELECT ... FOR UPDATE` on `automation_definitions`; zero stale `v1/g1` claims succeeded post-update. |
| Crash between execution claim & task creation (1-TX and 2-phase bounded-lease recovery) | **`CONFIRMED`** | `test_06`: `6a` single-TX `SimulatedCrashError` rolled back `0` executions / `0` tasks; `6b` two-phase `CLAIMED_PENDING_TASK` crash + redelivery dedup + expired lease recovery (`claim_epoch=2`, `1` task) + stale-worker `StaleClaimEpochFencedError` + `6c` superseded recovery `ABORTED_SUPERSEDED` (`0` tasks). |
| Task-level `task_version` CAS, concurrent task claims, stale approval & stale cancellation | **`CONFIRMED`** | `test_07`: `threading.Barrier(2)` on `task_version=1` produced `1` winner (`v=2`) and `1` `StaleTaskVersionError`; stale approval (`v=3`) and stale cancellation (`v=2`) rejected after task moved to `CANCELLED (v=4)`. |
| Condition trigger rising-edge evaluation (`FALSE -> TRUE` vs sustained `TRUE`) | **`CONFIRMED`** | `test_08`: poll sequence `[False, True, True, True, False, True]` fired only twice (`edge:1` and `edge:2`), creating `2` executions and `2` tasks. |
| Two-layer separation (`MOD-17` vs `MOD-08` receipts) & external uncertain-outcome reconciliation | **`CONFIRMED`** | `test_09`: `PROVIDER_IDEMPOTENCY_KEY` crash/retry dedup (`1` provider call, replay `0` calls), `NON_IDEMPOTENT_NO_RECONCILE` blind retry blocked (`UncertainOutcomeError`, `1` call), `RECONCILABLE_READ_PROBE` reconciled via `probe_fn` (`reconciled=True`, `1` provider call, `1` probe call, replay `replayed=True`). |
| Redis Streams, `automation_outbox` leased dispatcher & timer wheel (`zset:auto:due_timers`) | **`OPEN / NOT EVALUATED`** | Prototype writes rows into `automation_outbox` inside PG TXs, but no Redis container, outbox publisher worker, consumer group redelivery, DLQ, or timer wheel ran (`AUTO-EVAL-08`, `AUTO-EVAL-09`). |
| Live `MOD-09` OpenFGA 3-point permission checks & `PermissionEnvelope` expansion rejection | **`OPEN / NOT EVALUATED`** | Prototype passes principal strings directly; no OpenFGA server, relation tuples, or envelope expansion checks were evaluated (`AUTO-EVAL-06`). |
| Production cron/DST calendar scheduler & `misfire_policy` (`SKIP` / `RUN_LATEST` / `CATCH_UP_BOUNDED`) | **`OPEN / NOT EVALUATED`** | Tests pass explicit `datetime` timestamps; no cron expression parser, DST fold/gap solver, or pause/outage misfire catch-up logic was tested. |
| Rate limits, quotas & 8-control indirect-loop circuit breaker (`PAUSED_SAFETY_TRIP`) | **`OPEN / NOT EVALUATED`** | `AutomationStatus.PAUSED_SAFETY_TRIP` enum exists in `engine.py`, but cycle/depth checks, `min_interval_seconds`, quotas (`OQ-15`), and sliding-window trip logic were not implemented or tested (`AUTO-EVAL-10`). |
| Declarative condition `predicate_dsl`, hysteresis/cooldown & live `MOD-08` read-only probe | **`OPEN / NOT EVALUATED`** | `test_08` passes pre-computed `condition_value: bool`; DSL parsing (`OQ-16`), sustained windows, cooldown timers, and `MOD-08.execute_readonly_probe()` were not evaluated. |
| Async 6-step `DELETE` purge, `task_template` redaction verification & `phabric_automation` packaging | **`OPEN / NOT EVALUATED`** | Only synchronous PG TX state/spec flip + dispatch block was verified in `test_04`; async timer purge, `MOD-15` redacted export, `OQ-17` import boundaries, and Phabric cross-DB deployment remain open. |

### 2. Explicit 9-Area Technical Assessment

1. **Canonical Serialization & Idempotency-Key Generation (`CONFIRMED` as Spike Design / Experimental Code)**:
   - Stage 1 builds an explicit typed payload (`schema`, `tenant_id`, normalized `automation_id`, `schedule_version`, `generation`, `trigger_type`, `trigger_occurrence_id`) while Stage 2 serializes canonically via RFC 8785 (UTF-16-BE key sorting, zero whitespace, RFC 8785 §3.2.2.2 string escaping, §3.2.2.3 ES6 float rules verified with `allow_floats=True`) before UTF-8 + SHA-256. Keeping `allow_floats=False` as an application policy rejects accidental floats without altering RFC 8785 semantics. Because [canonical.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/canonical.py)'s hand-written serializer is **experimental prototype code**, production must evaluate adopting a maintained RFC 8785 library rather than carrying the custom implementation forward automatically.
2. **Atomic Execution Claiming & Duplicate Prevention (`CONFIRMED`)**:
   - Empirically proved that application-level `SELECT`-then-`INSERT` fails under `3`-worker barrier concurrency (`test_02`: `3` rows), whereas `orbi_automation.automation_executions.idempotency_key UNIQUE` + `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` + `orbi_app.tasks.execution_id UNIQUE` (`test_03`) limits `6` concurrent barrier workers and sequential replays to `1` execution and `1` task.
3. **Concurrent Lifecycle Updates & Execution Claims (`CONFIRMED`)**:
   - Acquiring `SELECT * FROM orbi_automation.automation_definitions WHERE automation_id = %s FOR UPDATE` at the start of both lifecycle mutations and claim/recovery transactions forces PostgreSQL to serialize overlapping updates and claims (`test_05`: `10/10` barrier races clean; zero post-update stale `v1/g1` claims). Throughput under burst traffic assumes per-`automation_id` row-lock serialization is acceptable.
4. **`schedule_version`, `generation` & `task_version` Fencing (`CONFIRMED` on Tested Operations)**:
   - Proved orthogonality across `UPDATE` (`schedule_version + 1` and `generation + 1`), `PAUSE`/`RESUME`/`CANCEL`/`DELETE` (`generation + 1` only), `PAUSE` HITL cascade (`task_version + 1`), and per-task CAS updates (`test_04`, `test_07`). When `UPDATE` bumps both counters together, `claim_and_dispatch_occurrence` trips `generation != auto["generation"]` first (`SKIPPED_STALE_GENERATION`), leaving `SKIPPED_STALE_SCHEDULE_VERSION` as defense-in-depth.
5. **Crash Recovery, Leases & Stale-Worker Rejection (`CONFIRMED` Inside PG Two-Phase Protocol)**:
   - Proved single-TX rollback safety (`test_06a`: `0` orphans), two-phase `CLAIMED_PENDING_TASK` redelivery deduplication + expired-lease `claim_epoch` CAS recovery (`test_06b`), `StaleClaimEpochFencedError` fencing when an abandoned worker wakes with `claim_epoch=1`, and re-locked `ABORTED_SUPERSEDED` cleanup if an update occurred before lease expiry (`test_06c`). Background sweep scheduling and clock-skew tolerance in multi-worker clusters were not tested.
6. **Trigger Occurrence Identity, Timestamp Precision & Timezone Normalization (`CONFIRMED` in `canonical.py`)**:
   - Resolved silent fractional-second truncation by switching UTC normalization to `.astimezone(timezone.utc).isoformat(timespec="auto")` and rejecting naive `datetime` inputs (`ValueError`). Sub-second differences (`.0` vs `.500000` vs `.500001`), cross-timezone equivalents (`UTC`/`IST`/`EST`), trailing-zero equivalents, integer-typed `INTERVAL`/`CONDITION` fields, and verbatim case-sensitive `EVENT`/`MANUAL`/`tenant_id` boundaries all passed (`test_01`). Production cron/timer emitters must supply timezone-aware `datetime` objects.
7. **Condition-Trigger Rising-Edge Behavior (`CONFIRMED` for State Machine Only)**:
   - Row-locking `automation_condition_monitors FOR UPDATE` ensures transitions `FALSE -> TRUE` increment `rising_edge_seq` and emit one occurrence (`edge:1`, `edge:2`), while sustained `TRUE -> TRUE` emits zero (`test_08`). Actual predicate DSL evaluation (`OQ-16`), cooldown windows, sustained-duration checks (`for N checks`), and live `MOD-08` probes remain untested.
8. **Separation of `MOD-17` Execution Idempotency from `MOD-08` Tool-Action Idempotency (`CONFIRMED`)**:
   - `MOD-17` `automation_executions.idempotency_key` deduplicates occurrence dispatch into `MOD-03` (`tasks.execution_id UNIQUE`), whereas `MOD-08` `step_execution_receipts.action_idempotency_key` deduplicates individual external tool steps inside a dispatched task (`test_09`). Dispatched tasks still depend on per-step receipts to prevent duplicate side effects on retry.
9. **External Provider Idempotency & Uncertain-Outcome Reconciliation (`CONFIRMED` Against Deterministic Stubs)**:
   - Verified `DISPATCHING -> UNKNOWN_OUTCOME` write-ahead state transitions for all three `ExternalIdempotencyMode` variants against deterministic in-memory stubs (`test_09`): idempotent key reuse (`PROVIDER_IDEMPOTENCY_KEY`), fail-closed escalation (`NON_IDEMPOTENT_NO_RECONCILE` -> `UncertainOutcomeError`), and read-probe reconciliation (`RECONCILABLE_READ_PROBE` -> `CONFIRMED_SUCCESS` via `probe_fn`). Real external SaaS/API error semantics (`429`/`5xx`/network timeouts) were not tested.

---

## 9. Remaining Limitations, Untested Integrations, Assumptions & Risks

### Prototype Evidence vs. Production Recommendations
- **Experimental RFC 8785 JCS Code**: [canonical.py](file:///f:/Projects/Orbi/evals/eval-08-automation-idempotency/src/eval08/canonical.py) implements RFC 8785 directly to avoid extra evaluation dependencies. **Do not automatically carry this custom implementation into production**: evaluate adopting a maintained, audited RFC 8785 library (e.g., `rfc8785` on PyPI) verified against the complete official RFC 8785 Appendix B vector suite, while keeping Stage 1 application normalization (`RFC 9562` UUIDs, `timespec="auto"`, strict integer checks, `allow_floats=False` input policy, verbatim external IDs) explicitly separate.
- **In-Process Prototype Harness vs. Distributed Runtime**: All tests invoke Python engine methods in one process (using threads + `threading.Barrier`) rather than separate worker containers communicating via Redis and PostgreSQL connection pools (`psycopg_pool`).

### Untested Integrations & Risks (Do Not Treat PG Prototype As End-to-End Validation)
1. **No Redis Streams, Outbox Publisher, or Timer Wheel**: `automation_outbox` rows commit in PostgreSQL, but no Redis container, `XADD`/`XREADGROUP` redelivery, DLQ, or `zset:auto:due_timers` scheduler ran (`AUTO-EVAL-08`, `AUTO-EVAL-09`).
2. **No Live OpenFGA or `PermissionEnvelope` Enforcement**: Principal authorization and envelope-expansion rejection (`AUTO-EVAL-06`) remain unvalidated against `MOD-09`.
3. **No Production Cron/DST Solver or `misfire_policy`**: Tests supply explicit `datetime` values; DST ambiguity, cron tick generation, and `SKIP` / `RUN_LATEST` / `CATCH_UP_BOUNDED` misfire behavior were not evaluated.
4. **No Quotas, Rate Limits, or Indirect-Loop Circuit Breaker (`OQ-15` / `AUTO-EVAL-10`)**: `PAUSED_SAFETY_TRIP` automatic tripping, `min_interval_seconds`, `max_trigger_depth`, and recursive chain limits remain unvalidated.
5. **No Condition Predicate DSL (`OQ-16`) or Live Probe Integration**: `evaluate_condition_poll` accepts a pre-evaluated boolean rather than executing a declarative JSON predicate against `MOD-08` telemetry.
6. **Cross-Tenant Runtime Rejection Test & Phabric Cross-DB Split (`OQ-17` / `CON-26`)**: Cross-tenant `TENANT_SCOPE_MISMATCH` exists in `engine.py` but lacks a dedicated runtime assertion in pytest; full 6-step async `DELETE` cleanup/redacted `MOD-15` verification and cross-database Phabric separation (`1-TX` vs `2-phase`) remain open.

---

## 10. Final Closure Recommendation: **`CONDITIONAL PASS`**

- **Core Reliability Sub-Spike Verdict**: **`PASS`** — Every PostgreSQL concurrency, canonical key determinism (with sub-second timestamp preservation), orthogonal versioning (`schedule_version`, `generation`, `task_version`), two-phase `claim_epoch` lease recovery, rising-edge monotonicity, and two-layer `MOD-17`/`MOD-08` receipt separation mechanism held cleanly (`9 passed in 7.14s`).
- **Overall `EVAL-08` Verdict**: **`CONDITIONAL PASS`** — Core reliability is validated, **but `EVAL-08` cannot be closed as a full `PASS` and `MOD-17` LLD must not treat the full module design as locked until the remaining integration and design gates below are resolved**.

### Exact Conditions Remaining Before `MOD-17` LLD Can Begin
1. **Redis + Outbox Dispatch & Recovery (`AUTO-EVAL-08`, `AUTO-EVAL-09`)**: Run live `automation_outbox -> Redis Streams` dispatch, consumer crash redelivery, DLQ handling, and timer-wheel (`zset:auto:due_timers`) deduplication tests.
2. **Live OpenFGA 3-Point & `PermissionEnvelope` Validation (`AUTO-EVAL-06`)**: Test Point-1/2/3 OpenFGA checks and envelope-expansion rejection against a real OpenFGA instance.
3. **8-Control Loop Guard, `PAUSED_SAFETY_TRIP` & Quotas (`AUTO-EVAL-10`, `OQ-15`)**: Test cycle detection (`trigger_chain_ids[]`), `max_trigger_depth`, `min_interval_seconds`, and sliding-window `PAUSED_SAFETY_TRIP` halting.
4. **Open Spec Decisions & Residual Coverage (`OQ-16`, `OQ-17`, RFC 8785 Library, Cron/DST/Delete)**:
   - Evaluate and select a maintained RFC 8785 library (e.g., `rfc8785`) tested against the official Appendix B suite;
   - Finalize condition `predicate_dsl`, sustained window, and cooldown schemas (`OQ-16`);
   - Finalize DST-safe cron generation and `misfire_policy` semantics;
   - Lock `phabric_automation` packaging boundaries and deployment topology (`1-TX` vs `2-phase`, `OQ-17`), plus add explicit runtime test coverage for `TENANT_SCOPE_MISMATCH` and end-to-end 6-step `DELETE` redaction/cleanup.
