"""Cross-domain supervisory aggregation owned by Foundation.

Foundation consumes only the shared RHI_DOMAIN_SUPERVISORY_STATUS_V1 envelope.
Domain-specific semantics, detailed diagnostics, property resolution and business
intelligence remain owned by the publishing domain.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

CONTRACT_ID = "RHI_DOMAIN_SUPERVISORY_STATUS_V1"
CONTRACT_VERSION = "1.0.0"
ALLOWED_STATUSES = {
    "OK",
    "READY",
    "DEGRADED",
    "BLOCKED",
    "STALE",
    "CONFIGURATION_REQUIRED",
    "UNKNOWN",
}

_STATUS_PRIORITY = {
    "BLOCKED": 60,
    "STALE": 50,
    "CONFIGURATION_REQUIRED": 40,
    "DEGRADED": 30,
    "UNKNOWN": 20,
    "OK": 10,
    "READY": 10,
}


def _status(value: Any) -> str:
    raw = str(value or "UNKNOWN").upper()
    return raw if raw in ALLOWED_STATUSES else "UNKNOWN"


def validate_domain_supervisory_status(payload: Any) -> dict[str, Any]:
    """Validate the shared envelope without learning domain semantics."""
    if not isinstance(payload, dict):
        raise ValueError("domain supervisory status must be an object")
    row = deepcopy(payload)
    if row.get("contract_id") != CONTRACT_ID:
        raise ValueError("unsupported domain supervisory contract_id")
    if row.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported domain supervisory contract_version")
    for key in ("domain_id", "publisher_domain", "release", "last_observed_at"):
        if not str(row.get(key) or "").strip():
            raise ValueError(f"missing required supervisory field: {key}")
    for key in (
        "configuration_revision",
        "build_input_revision",
        "publication_revision",
        "issue_count",
        "blocking_issue_count",
        "warning_count",
    ):
        try:
            value = int(row.get(key, 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid supervisory integer field: {key}") from exc
        if value < 0:
            raise ValueError(f"negative supervisory integer field: {key}")
        row[key] = value
    for key in (
        "configuration_status",
        "contract_status",
        "build_status",
        "runtime_status",
        "intelligence_status",
        "overall_domain_readiness",
    ):
        value = _status(row.get(key))
        if value != str(row.get(key) or "").upper():
            raise ValueError(f"unsupported supervisory status: {key}={row.get(key)!r}")
        row[key] = value
    issues = row.get("issues_summary") or []
    if not isinstance(issues, list):
        raise ValueError("issues_summary must be an array")
    if row["issue_count"] < len(issues):
        raise ValueError("issue_count cannot be lower than issues_summary length")
    normalized_issues: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError("supervisory issue summary must be an object")
        issue_id = str(issue.get("issue_id") or "").strip()
        reason_code = str(issue.get("reason_code") or "").strip()
        if not issue_id or not reason_code:
            raise ValueError("supervisory issue requires issue_id and reason_code")
        normalized_issues.append(deepcopy(issue))
    row["issues_summary"] = normalized_issues
    return row


def read_domain_supervisory_statuses(hass: Any) -> list[dict[str, Any]]:
    """Read and validate domain envelopes from the shared in-process registry."""
    from .const import DOMAIN_SUPERVISORY_STATUS_REGISTRY

    registry = hass.data.get(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {}) or {}
    statuses: list[dict[str, Any]] = []
    for domain_id in sorted(registry):
        record = registry[domain_id]
        provider = record.get("provider") if isinstance(record, dict) else record
        try:
            snapshot = provider.snapshot() if callable(getattr(provider, "snapshot", None)) else provider
            row = validate_domain_supervisory_status(snapshot)
            row["registry_status"] = "valid"
        except Exception as exc:  # fail closed but keep supervisor observable
            row = {
                "contract_id": CONTRACT_ID,
                "contract_version": CONTRACT_VERSION,
                "domain_id": str(domain_id),
                "publisher_domain": str((record or {}).get("publisher_domain") or domain_id) if isinstance(record, dict) else str(domain_id),
                "release": "unknown",
                "configuration_revision": 0,
                "build_input_revision": 0,
                "publication_revision": 0,
                "configuration_status": "UNKNOWN",
                "contract_status": "BLOCKED",
                "build_status": "UNKNOWN",
                "runtime_status": "UNKNOWN",
                "intelligence_status": "UNKNOWN",
                "overall_domain_readiness": "BLOCKED",
                "issue_count": 1,
                "blocking_issue_count": 1,
                "warning_count": 0,
                "issues_summary": [{
                    "issue_id": f"{domain_id}:supervision:invalid_contract",
                    "severity": "CRITICAL",
                    "category": "CONTRACT",
                    "status": "OPEN",
                    "reason_code": "INVALID_DOMAIN_SUPERVISORY_STATUS",
                    "blocking": True,
                    "affected_scope": [str(domain_id)],
                    "details_reference": None,
                }],
                "last_success_at": None,
                "last_observed_at": datetime.now(timezone.utc).isoformat(),
                "details_reference": None,
                "registry_status": "invalid",
                "registry_error": f"{type(exc).__name__}: {exc}",
            }
        statuses.append(row)
    return statuses


def aggregate_system_supervision(
    domain_statuses: Iterable[dict[str, Any]],
    *,
    foundation_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate readiness only; never reconstruct domain business semantics."""
    rows = [deepcopy(row) for row in domain_statuses]
    domain_readiness = [_status(row.get("overall_domain_readiness")) for row in rows]
    foundation_state = str((foundation_health or {}).get("state") or "UNKNOWN").upper()
    foundation_map = {
        "OK": "READY",
        "DEGRADED": "DEGRADED",
        "STALE": "STALE",
        "INVALID": "BLOCKED",
        "UNKNOWN": "UNKNOWN",
    }
    candidates = domain_readiness + [foundation_map.get(foundation_state, "UNKNOWN")]
    system_readiness = max(candidates, key=lambda value: _STATUS_PRIORITY.get(value, 20)) if candidates else "UNKNOWN"
    issues = [issue for row in rows for issue in row.get("issues_summary", [])]
    return {
        "contract_id": "RHI_SYSTEM_SUPERVISORY_STATUS_V1",
        "contract_version": "1.0.0",
        "system_readiness": system_readiness,
        "domain_count": len(rows),
        "ready_domain_count": sum(1 for value in domain_readiness if value in {"OK", "READY"}),
        "degraded_domain_count": sum(1 for value in domain_readiness if value == "DEGRADED"),
        "blocked_domain_count": sum(1 for value in domain_readiness if value == "BLOCKED"),
        "stale_domain_count": sum(1 for value in domain_readiness if value == "STALE"),
        "configuration_required_domain_count": sum(1 for value in domain_readiness if value == "CONFIGURATION_REQUIRED"),
        "blocking_issue_count": sum(int(row.get("blocking_issue_count", 0) or 0) for row in rows),
        "warning_count": sum(int(row.get("warning_count", 0) or 0) for row in rows),
        "issue_count": sum(int(row.get("issue_count", 0) or 0) for row in rows),
        "affected_domains": sorted(
            str(row.get("domain_id"))
            for row in rows
            if _status(row.get("overall_domain_readiness")) not in {"OK", "READY"}
        ),
        "domain_statuses": rows,
        "issues_summary": issues,
        "foundation_health": deepcopy(foundation_health or {}),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
