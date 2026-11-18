"""RFC 8785 (JCS) canonical JSON serialization and deterministic idempotency keys."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID


class TriggerType(StrEnum):
    """Unified 6-trigger taxonomy defined in DOC-HLD-002 Section D.6."""

    CRON = "CRON"
    INTERVAL = "INTERVAL"
    ONE_TIME = "ONE_TIME"
    EVENT = "EVENT"
    CONDITION = "CONDITION"
    MANUAL = "MANUAL"


def _escape_jcs_string(value: str) -> str:
    """Escape a JSON string strictly according to RFC 8785 Section 3.2.2.2."""
    out: list[str] = ['"']
    for ch in value:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError(f"Lone surrogate U+{code:04X} is invalid in RFC 8785 UTF-8")
        if code == 0x08:
            out.append("\\b")
        elif code == 0x09:
            out.append("\\t")
        elif code == 0x0A:
            out.append("\\n")
        elif code == 0x0C:
            out.append("\\f")
        elif code == 0x0D:
            out.append("\\r")
        elif code == 0x22:
            out.append('\\"')
        elif code == 0x5C:
            out.append("\\\\")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _format_es6_number(value: float) -> str:
    """Format a finite IEEE-754 float per RFC 8785 §3.2.2.3 (ECMAScript 6 NumberToString)."""
    if not math.isfinite(value):
        raise ValueError("Non-finite float (NaN/Infinity) is invalid in RFC 8785")
    if value == 0.0:
        return "0"
    if value < 0.0:
        return "-" + _format_es6_number(-value)

    # Python's repr(float) uses David Gay's shortest round-trip IEEE-754 decimal digits.
    raw = repr(value).lower()
    mantissa, _, exp_part = raw.partition("e")
    sci_exp = int(exp_part) if exp_part else 0
    int_part, _, frac_part = mantissa.partition(".")
    combined = int_part + frac_part
    stripped_leading = combined.lstrip("0")
    digits = stripped_leading.rstrip("0")
    k = len(digits)
    n = sci_exp - len(frac_part) + len(stripped_leading)

    if k <= n <= 21:
        return digits + ("0" * (n - k))
    if 0 < n < k:
        return digits[:n] + "." + digits[n:]
    if -5 <= n <= 0:
        return "0." + ("0" * (-n)) + digits

    significand = digits[0] if k == 1 else f"{digits[0]}.{digits[1:]}"
    exp = n - 1
    sign = "+" if exp > 0 else "-"
    return f"{significand}e{sign}{abs(exp)}"


def _format_jcs_number(value: int | float, *, allow_floats: bool) -> str:
    if isinstance(value, bool):
        raise TypeError("bool must be serialized before number check")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not allow_floats:
            raise TypeError(
                "Floating-point numbers are disallowed by Orbi application identity policy; use int or canonical string"
            )
        return _format_es6_number(value)
    raise TypeError(f"Unsupported numeric type: {type(value).__name__}")


def jcs_canonicalize(value: Any, *, allow_floats: bool = False) -> str:
    """Serialize JSON value canonically per RFC 8785 (UTF-16-BE key sort, zero whitespace).

    Stage 2 pure serializer: preserves string casing/contents verbatim and rejects non-JSON
    Python objects such as raw UUID instances (which must be normalized in Stage 1 first).
    By default, ``allow_floats=False`` enforces Orbi's application-layer identity schema
    policy; pass ``allow_floats=True`` (or call ``rfc8785_canonicalize``) for full RFC 8785
    §3.2.2.3 IEEE-754 float serialization.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _format_jcs_number(value, allow_floats=allow_floats)
    if isinstance(value, str):
        return _escape_jcs_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(jcs_canonicalize(item, allow_floats=allow_floats) for item in value) + "]"
    if isinstance(value, dict):
        items: list[tuple[bytes, str, Any]] = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"RFC 8785 object keys must be strings, got {type(k).__name__}")
            # Verify valid UTF-16 encoding (rejects lone surrogates) and sort by UTF-16 code units
            sort_key = k.encode("utf-16-be")
            items.append((sort_key, k, v))
        items.sort(key=lambda entry: entry[0])
        parts = [
            f"{_escape_jcs_string(k)}:{jcs_canonicalize(v, allow_floats=allow_floats)}"
            for _, k, v in items
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"Unsupported type for JCS canonicalization: {type(value).__name__}")


def rfc8785_canonicalize(value: Any) -> str:
    """Serialize any JSON value per full RFC 8785 (JCS), including §3.2.2.3 ES6 floats."""
    return jcs_canonicalize(value, allow_floats=True)


def _normalize_scheduled_at_utc(
    scheduled_at_utc: datetime | None, *, trigger_label: str
) -> str:
    """Normalize a timezone-aware datetime to UTC ISO-8601 without truncating fractional seconds."""
    if scheduled_at_utc is None or not isinstance(scheduled_at_utc, datetime):
        raise ValueError(f"{trigger_label} occurrence requires a datetime scheduled_at_utc")
    if scheduled_at_utc.tzinfo is None or scheduled_at_utc.utcoffset() is None:
        raise ValueError(
            f"{trigger_label} scheduled_at_utc must be timezone-aware (naive datetimes are rejected)"
        )
    return scheduled_at_utc.astimezone(timezone.utc).isoformat(timespec="auto")


def build_trigger_occurrence_id(
    trigger_type: TriggerType,
    *,
    scheduled_at_utc: datetime | None = None,
    anchor_epoch_sec: int | None = None,
    interval_sec: int | None = None,
    tick_index: int | None = None,
    source_event_id: str | None = None,
    monitor_id: str | None = None,
    rising_edge_seq: int | None = None,
    request_idempotency_id: str | None = None,
) -> str:
    """Construct a stable, type-unambiguous trigger_occurrence_id per trigger class."""
    if trigger_type == TriggerType.CRON:
        ts = _normalize_scheduled_at_utc(scheduled_at_utc, trigger_label="CRON")
        return f"cron:{ts}"

    if trigger_type == TriggerType.INTERVAL:
        if anchor_epoch_sec is None or interval_sec is None or tick_index is None:
            raise ValueError("INTERVAL occurrence requires anchor_epoch_sec, interval_sec, tick_index")
        if (
            type(anchor_epoch_sec) is not int
            or type(interval_sec) is not int
            or type(tick_index) is not int
        ):
            raise TypeError("INTERVAL fields must strictly be integers")
        return f"interval:{anchor_epoch_sec}:every:{interval_sec}:tick:{tick_index}"

    if trigger_type == TriggerType.ONE_TIME:
        ts = _normalize_scheduled_at_utc(scheduled_at_utc, trigger_label="ONE_TIME")
        return f"onetime:{ts}"

    if trigger_type == TriggerType.EVENT:
        if not source_event_id:
            raise ValueError("EVENT occurrence requires non-empty source_event_id")
        return f"event:{source_event_id}"

    if trigger_type == TriggerType.CONDITION:
        if not monitor_id or rising_edge_seq is None:
            raise ValueError("CONDITION occurrence requires monitor_id and rising_edge_seq")
        if type(rising_edge_seq) is not int or rising_edge_seq < 1:
            raise ValueError("rising_edge_seq must be a positive integer")
        return f"condition:{monitor_id}:edge:{rising_edge_seq}"

    if trigger_type == TriggerType.MANUAL:
        if not request_idempotency_id:
            raise ValueError("MANUAL occurrence requires request_idempotency_id")
        return f"manual:{request_idempotency_id}"

    raise ValueError(f"Unsupported trigger_type: {trigger_type}")


def build_execution_identity_payload(
    *,
    tenant_id: str,
    automation_id: UUID | str,
    schedule_version: int,
    generation: int,
    trigger_type: TriggerType | str,
    trigger_occurrence_id: str,
) -> dict[str, Any]:
    """Validate strict data types and construct the execution identity payload."""
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id must be a non-empty string")
    if type(schedule_version) is not int or schedule_version < 1:
        raise TypeError("schedule_version must strictly be a positive int (not bool/str/float)")
    if type(generation) is not int or generation < 1:
        raise TypeError("generation must strictly be a positive int (not bool/str/float)")
    if not isinstance(trigger_occurrence_id, str) or not trigger_occurrence_id:
        raise ValueError("trigger_occurrence_id must be a non-empty string")

    norm_uuid = str(UUID(str(automation_id))).lower()
    norm_trigger = TriggerType(trigger_type).value
    return {
        "schema": "orbi.automation.execution_idempotency.v1",
        "tenant_id": tenant_id,
        "automation_id": norm_uuid,
        "schedule_version": schedule_version,
        "generation": generation,
        "trigger_type": norm_trigger,
        "trigger_occurrence_id": trigger_occurrence_id,
    }


def compute_automation_idempotency_key(
    *,
    tenant_id: str,
    automation_id: UUID | str,
    schedule_version: int,
    generation: int,
    trigger_type: TriggerType | str,
    trigger_occurrence_id: str,
) -> tuple[str, str]:
    """Return (sha256_hex_key, rfc8785_canonical_json_string) for MOD-17 occurrence."""
    payload = build_execution_identity_payload(
        tenant_id=tenant_id,
        automation_id=automation_id,
        schedule_version=schedule_version,
        generation=generation,
        trigger_type=trigger_type,
        trigger_occurrence_id=trigger_occurrence_id,
    )
    canonical_str = jcs_canonicalize(payload, allow_floats=False)
    digest = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    return digest, canonical_str


def compute_tool_action_idempotency_key(
    *,
    tenant_id: str,
    task_id: UUID | str,
    execution_id: UUID | str,
    logical_step_id: str,
    tool_id: str,
    tool_version: str,
    arguments: dict[str, Any],
    target_resource: str,
    approval_id: UUID | str | None = None,
) -> str:
    """Compute MOD-08 Gate 2 action_idempotency_key using RFC 8785 JCS."""
    canonical_args_hash = hashlib.sha256(
        jcs_canonicalize(arguments, allow_floats=False).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema": "orbi.gateway.action_idempotency.v1",
        "tenant_id": tenant_id,
        "task_id": str(UUID(str(task_id))).lower(),
        "execution_id": str(UUID(str(execution_id))).lower(),
        "logical_step_id": logical_step_id,
        "tool_id": tool_id,
        "tool_version": tool_version,
        "canonical_arguments_hash": canonical_args_hash,
        "target_resource": target_resource,
        "approval_id": str(UUID(str(approval_id))).lower() if approval_id else None,
    }
    canonical = jcs_canonicalize(payload, allow_floats=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
