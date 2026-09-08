"""Shared Foundation health semantics without Home Assistant dependencies."""
from __future__ import annotations

from typing import Any


def derive_success_health(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Derive shared health semantics without turning normal review state into failure."""
    stale_scopes = sorted({
        str(item.get("domain_id") or "unknown")
        for item in snapshot.get("selected_domain_build_inputs", [])
        if any(
            issue in {"publisher_or_specification_unavailable", "specification_changed_since_configuration"}
            for issue in (item.get("discovery_assessment") or {}).get("issues", [])
        )
    })
    if stale_scopes:
        return {
            "state": "STALE",
            "reason": "configured_build_input_stale_or_changed",
            "affected_scope": stale_scopes,
        }

    invalid_publishers = sorted({
        str(item.get("publisher_domain") or "unknown")
        for item in snapshot.get("publication_index", [])
        if item.get("status") == "invalid"
    })
    if invalid_publishers:
        return {
            "state": "DEGRADED",
            "reason": "one_or_more_domain_publications_invalid",
            "affected_scope": invalid_publishers,
        }

    return {
        "state": "OK",
        "reason": "snapshot_refresh_succeeded",
        "affected_scope": [],
    }
