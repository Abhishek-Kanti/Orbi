# ADR-006: Layered Windows Companion Execution & Home IoT Boundary

- **Status**: Accepted *(UIA/Vision Coverage & Thresholds Pending `EVAL-02` / `OQ-12`)*
- **Date**: `2026-11-14`
- **Drivers**: Decisions 9 & 10, Principles `AP-05`, `AP-06`, `AP-10`

---

## Context

Desktop applications on Windows exhibit vastly different automation surfaces: some expose clean native APIs/COM/CLI interfaces, many expose Windows UI Automation (UIA) trees readable by `vercel-labs/agent-desktop`, and legacy/custom-rendered/canvas apps expose incomplete or empty UIA trees. Relying exclusively on UIA breaks on custom apps, while defaulting to vision-based pixel clicking introduces fragility, latency, and privacy risks (`CON-09`). Separately, Phase 5 Home IoT support needs a clean boundary without premature implementation (`AP-10`).

---

## Decision

1. **Strict 3-Tier Windows Companion Execution Hierarchy (`MOD-14`, Zone 3)**:
   - **Tier 1 — Native OS / App APIs**: Always attempted first where an allowlisted native API/CLI adapter exists.
   - **Tier 2 — UI Automation (UIA) via `vercel-labs/agent-desktop` (`EVAL-02`)**: Used when native APIs are unavailable, operating on structured JSON UIA trees and `ref` handles.
   - **Tier 3 — Controlled Vision Fallback**: Permitted **only** when Tier 1 and Tier 2 cannot resolve target elements, restricted to allowlisted active windows with local sensitive-region cropping/redaction.
2. **Mandatory Safe Stops & Uniform Governance**:
   - **Confidence Floor**: When UIA element resolution or Vision grounding falls below configured confidence thresholds (`EVAL-02`, `OQ-12`), `MOD-14` MUST abort safely (`STOP_INSUFFICIENT_CONFIDENCE`) rather than guessing clicks/keystrokes.
   - **Governance Invariance**: OpenFGA authorization (`MOD-09`), local app allow/deny lists, local kill-switch, and action-bound human approvals (`ADR-004`) apply uniformly regardless of whether Tier 1, Tier 2, or Tier 3 executes the action.
3. **Future Home IoT Boundary (`MOD-16-EXT`, `Decision 10`)**:
   - Reserve `FUTURE_IOT` harness metadata in `MOD-07`, adapter dispatch hooks in `MOD-08`, and `iot_device` / `household` ownership tuples in `MOD-09` designed to accommodate **Home Assistant / MQTT** bridges in Phase 5 without hardcoding an IoT protocol or implementing IoT components prior to Phase 5 (`AP-10`).

---

## Consequences

### Positive
- Maximizes determinism and speed (Native API -> UIA) while gracefully covering non-accessible Windows UIs (Controlled Vision) without blind clicking.
- Protects local desktop privacy and prevents catastrophic misclicks via safe confidence halts and uniform HITL approvals.
- Keeps Home Assistant / MQTT integration modular and future-proof without Phase 1 scope creep.

### Negative / Risks
- Tier 3 Vision Fallback introduces tighter latency, visual drift risk during HITL pauses (`CON-09`), and OCR/screen-privacy engineering evaluated in `EVAL-02` and `OQ-12`.
