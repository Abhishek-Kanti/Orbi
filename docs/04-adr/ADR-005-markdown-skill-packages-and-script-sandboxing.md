# ADR-005: Versioned Markdown Skill Packages & Supporting Script Sandboxing

- **Status**: Accepted *(Sandbox Runtime Pending `EVAL-06` / `OQ-11`)*
- **Date**: `2026-11-14`
- **Drivers**: Decision 7, Clarification D, Principles `AP-01`, `AP-02`, `AP-04`, `AP-06`

---

## Context

Orbi requires a Skill Lifecycle Manager (`MOD-11`) capable of authoring, evaluating, versioning, and publishing reusable agent skills while keeping skills separate from Cognee knowledge memory (`AP-04`) and preventing unreviewed dynamic code from executing inside `orbi-core` or escalating agent permissions (`CON-04`, `CON-08`).

---

## Decision

1. **Versioned Markdown Skill Package Format**:
   - Each skill is stored and versioned (`semver`, immutable once published) as a structured package centered around a **`SKILL.md`** specification file, optionally accompanied by:
     - Structured examples,
     - Input/Output JSON schemas,
     - Deterministic test/evaluation fixtures, and
     - Supporting executable scripts (e.g., data transformation or multi-step helper scripts).
2. **Strict Separation of Instructions vs. Supporting Scripts (`Clarification D`)**:
   - **`SKILL.md` Instructions**: Narrative procedural guidance loaded into `MOD-04` agent prompts **only** when authorized; instructions **never** grant tool permissions, mutate OpenFGA tuples, or bypass `MOD-08`.
   - **Supporting Executable Scripts**: Executed **exclusively** inside an isolated cloud execution sandbox (**`MOD-11-SANDBOX`**, Zone 2a), never in-process inside `orbi-core` (`ADR-001`, `CON-08`).
   - Sandbox restrictions: zero ambient OAuth credentials, zero direct host filesystem access, zero unrestricted outbound network access, and any external side effect requested by a script must route through `MOD-08` Gateway subject to OpenFGA + action-bound approvals (`AP-01`, `AP-02`).
3. **Evaluation & Human Publication Gate**:
   - Lifecycle states: `DRAFT` -> `EVALUATED` (must pass automated fixture tests inside `MOD-11-SANDBOX`) -> `AWAITING_PUBLISH_APPROVAL` -> `PUBLISHED` (human owner review & approval in `MOD-02`) -> `DEPRECATED`.
   - Unapproved candidate skills cannot be invoked in production tasks.

---

## Consequences

### Positive
- Human-readable, diffable, Git/review-friendly skill format (`SKILL.md`) ideal for Dashboard inspection (`MOD-02`) and version pinning (`skill:<id>@<semver>`).
- Neutralizes remote code execution and ambient credential exfiltration risks from agent-generated supporting scripts.

### Negative / Risks
- Requires selecting and hardening `MOD-11-SANDBOX` (`EVAL-06` / `OQ-11`), adding invocation overhead when skills bundle supporting scripts vs. pure Markdown instructions.
- Pure declarative tool-composition patterns should be preferred over supporting scripts whenever possible (`CON-08`).
