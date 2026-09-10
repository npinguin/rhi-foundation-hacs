"""Atomic bounded Foundation snapshot lifecycle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers import device_registry as dr

from .build_input import build_domain_inputs
from .catalog_builder import build_catalog
from .const import (
    CONF_CONFIGURATION_REVISION, CONF_CONCEPT_MAPPINGS, CONF_DEVELOPER_MODE, CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS, CONF_TECHNICAL_SELECTIONS, DOMAIN, SELECTED_DOMAIN_BUILD_INPUT_REGISTRY,
    SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, SAFETY,
)
from .publications import publication_summary, read_publications
from .health import derive_success_health
from .handoff import replace_entry_slice
from .concept_trace import build_concept_trace


def _config(entry: Any) -> dict[str, Any]:
    config = dict(entry.data)
    config.update(dict(entry.options))
    return config


def _integration_inventory(hass: Any, selected: list[str], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    published = {str(item["integration_domain"]) for spec in specs for item in spec.get("supported_sources", [])}
    counts: dict[str, int] = {}
    for entry in hass.config_entries.async_entries():
        counts[entry.domain] = counts.get(entry.domain, 0) + 1
    return [{
        "integration_domain": domain,
        "config_entry_count": count,
        "selected": domain in selected,
        "domain_supported": domain in published,
    } for domain, count in sorted(counts.items())]


def _device_config_entry_map(hass: Any) -> dict[str, set[str]]:
    """Map HA device ids to their technical config-entry ownership.

    This is a topology fact owned by Home Assistant, not a domain semantic. It lets
    Foundation honor a domain-published candidate_scope=config_entry while preserving
    the exact device ids the user originally selected.
    """
    registry = dr.async_get(hass)
    out: dict[str, set[str]] = {}
    for device in registry.devices.values():
        entry_ids = {str(item) for item in (getattr(device, "config_entries", None) or set()) if item}
        if entry_ids:
            out[str(device.id)] = entry_ids
    return out


def build_snapshot(hass: Any, entry: Any, *, refresh_reason: str) -> dict[str, Any]:
    config = _config(entry)
    revision = max(1, int(config.get(CONF_CONFIGURATION_REVISION, 1)))
    selected = list(config.get(CONF_SELECTED_INTEGRATIONS, []))
    records, specs = read_publications(hass)
    catalog = build_catalog(hass, revision=revision, selected_integrations=None)
    concept_mappings = dict(config.get(CONF_CONCEPT_MAPPINGS, {}) or {})
    device_selections = dict(config.get(CONF_DEVICE_SELECTIONS, {}) or {})
    by_domain, selected_inputs = build_domain_inputs(
        specifications=specs,
        concept_mappings=concept_mappings,
        device_selections=device_selections,
        catalog=catalog,
        configuration_revision=revision,
        device_config_entries=_device_config_entry_map(hass),
    )
    concept_trace = build_concept_trace(
        specifications=specs,
        concept_mappings=concept_mappings,
        device_selections=device_selections,
        selected_inputs=selected_inputs,
    )
    configured_groups = [{
        "domain_id": item["domain_id"],
        "builder_id": item["builder_id"],
        "input_id": group.get("input_id"),
        "candidate_count": group.get("candidate_count", 0),
        "required": group.get("required", False),
        "review_required": item["discovery_assessment"]["review_required"],
    } for item in selected_inputs for group in item.get("candidate_groups", [])]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "refresh_reason": refresh_reason,
        "configuration_revision": revision,
        "developer_mode": bool(config.get(CONF_DEVELOPER_MODE, False)),
        "selected_integrations": selected,
        "concept_mappings": concept_mappings,
        "technical_selections": list(config.get(CONF_TECHNICAL_SELECTIONS, []) or []),
        "device_selections": device_selections,
        "integration_inventory": _integration_inventory(hass, selected, specs),
        "publication_index": publication_summary(records),
        "domain_build_specifications": specs,
        "capability_catalog": catalog,
        "configured_candidate_groups": configured_groups,
        "concept_trace": concept_trace,
        "selected_domain_build_inputs": selected_inputs,
        "selected_domain_build_inputs_by_domain": by_domain,
        "safety": dict(SAFETY),
    }


def _publish_selected_inputs(hass: Any, entry_id: str, by_domain: dict[str, list[dict[str, Any]]], *, reason: str) -> None:
    registry = hass.data.setdefault(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {})
    next_registry, events = replace_entry_slice(
        registry, entry_id=entry_id, by_domain=by_domain, reason=reason
    )
    registry.clear()
    registry.update(next_registry)
    for event_data in events:
        hass.bus.async_fire(SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, event_data)


async def async_refresh_snapshot(hass: Any, entry: Any, *, reason: str) -> bool:
    data = hass.data[DOMAIN][entry.entry_id]
    lock = data.setdefault("refresh_lock", asyncio.Lock())
    async with lock:
        try:
            candidate = build_snapshot(hass, entry, refresh_reason=reason)
            _publish_selected_inputs(hass, entry.entry_id, candidate["selected_domain_build_inputs_by_domain"], reason=reason)
            snapshot = data.setdefault("snapshot", {})
            snapshot.clear(); snapshot.update(candidate)
            health = data.setdefault("runtime_health", {})
            derived = derive_success_health(candidate)
            health.update({
                **derived,
                "revision": candidate["configuration_revision"],
                "last_success": candidate["generated_at"],
                "last_refresh_at": candidate["generated_at"],
                "last_refresh_reason": reason,
                "last_error": None,
                "successful_refreshes": int(health.get("successful_refreshes", 0)) + 1,
                "failed_refreshes": int(health.get("failed_refreshes", 0)),
            })
            return True
        except Exception as exc:
            health = data.setdefault("runtime_health", {})
            health.update({
                "state": "INVALID",
                "reason": "snapshot_refresh_failed",
                "revision": int(data.get("snapshot", {}).get("configuration_revision", 0) or 0),
                "last_success": health.get("last_success"),
                "affected_scope": ["foundation_snapshot"],
                "last_refresh_at": datetime.now(timezone.utc).isoformat(),
                "last_refresh_reason": reason,
                "last_error": f"{type(exc).__name__}: {exc}",
                "successful_refreshes": int(health.get("successful_refreshes", 0)),
                "failed_refreshes": int(health.get("failed_refreshes", 0)) + 1,
            })
            return False


def remove_published_inputs(hass: Any, entry_id: str) -> None:
    registry = hass.data.get(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {})
    removed: list[tuple[str, int]] = []
    for key in [key for key, value in registry.items() if isinstance(value, dict) and value.get("foundation_entry_id") == entry_id]:
        value = registry.pop(key)
        removed.append((str(value.get("domain_id") or key), int(value.get("configuration_revision", 1))))
    for domain, revision in removed:
        hass.bus.async_fire(SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, {
            "foundation_entry_id": entry_id,
            "domain_id": domain,
            "configuration_revision": max(1, revision),
            "build_input_revision": max(1, revision),
            "reason": "removed",
        })
