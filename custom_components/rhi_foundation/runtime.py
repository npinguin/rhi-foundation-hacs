"""Atomic bounded Foundation snapshot lifecycle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers import device_registry as dr

from .build_input import build_domain_inputs, rematerialize_concept_mapping_metadata
from .configured_surfaces import (
    build_configured_surface_inputs,
    configured_surface_entity_registry_ids,
    configured_surface_integrations,
)
from .catalog_builder import build_catalog
from .domain_config import effective_entry_configuration
from .const import (
    CONF_CONFIGURATION_REVISION, CONF_CONFIGURATION_SURFACE_SELECTIONS, CONF_CONCEPT_MAPPINGS, CONF_DEVELOPER_MODE, CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS, CONF_TECHNICAL_SELECTIONS, DOMAIN, SELECTED_DOMAIN_BUILD_INPUT_REGISTRY,
    SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, SAFETY,
)
from .publications import publication_summary, read_publications
from .shared_registry import async_refresh_framework_resource_providers, iter_framework_resource_providers, get_framework_resource_provider
from .health import derive_success_health
from .handoff import replace_entry_slice, structural_slice_changed
from .concept_trace import build_concept_trace
from .wizard_candidates import configured_entity_candidates
from .wizard_state import legacy_empty_framework_selection


def _config(entry: Any) -> dict[str, Any]:
    """Read one Foundation entry through the canonical domain-safe merge boundary."""
    return effective_entry_configuration(entry.data, entry.options)


def _integration_inventory(hass: Any, selected: list[str], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    published = {str(item["integration_domain"]) for spec in specs for item in spec.get("supported_sources", [])}
    rows: dict[str, dict[str, Any]] = {}
    for entry in hass.config_entries.async_entries():
        row = rows.setdefault(entry.domain, {"config_entry_count": 0, "framework_provider": False})
        row["config_entry_count"] += 1
    for domain, _provider in iter_framework_resource_providers(hass):
        row = rows.setdefault(domain, {"config_entry_count": 0, "framework_provider": False})
        row["framework_provider"] = True
    return [{
        "integration_domain": domain,
        "config_entry_count": int(row["config_entry_count"]),
        "framework_provider": bool(row["framework_provider"]),
        "selected": domain in selected,
        "domain_supported": domain in published,
    } for domain, row in sorted(rows.items())]


def _device_config_entry_map(
    hass: Any,
    selected_device_ids: set[str],
) -> dict[str, set[str]]:
    """Resolve only explicitly selected HA devices to their owning config entry."""
    registry = dr.async_get(hass)
    out: dict[str, set[str]] = {}
    for device_id in sorted(selected_device_ids):
        device = registry.async_get(device_id)
        if device is None:
            continue
        entry_id = getattr(device, "config_entry_id", None)
        if entry_id:
            out[str(device_id)] = {str(entry_id)}
    return out



def _suppress_legacy_empty_framework_selections(
    hass: Any,
    concept_mappings: dict[str, dict[str, Any]],
    device_selections: dict[str, dict[str, Any]],
    specifications: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """Ignore only legacy auto-created empty framework selections.

    F1.8.25 could persist an all-matching integration scope even when a framework
    provider exposed no resources for that concept. That state was never explicit
    user intent. Suppression is fail-safe: it applies only when the provider is
    currently registered, the selection carries the legacy origin marker, and the
    current specification confirms this was a framework-resource scope that predates explicit resource selection.
    """
    specs_by_builder = {
        str(spec.get("builder_id") or ""): spec
        for spec in specifications
        if isinstance(spec, dict)
    }
    mappings = dict(concept_mappings or {})
    selections = dict(device_selections or {})
    suppressed: list[str] = []
    for mapping_key, mapping in list(mappings.items()):
        if not isinstance(mapping, dict):
            continue
        target_id = f"domain:{mapping_key}"
        selection = selections.get(target_id)
        if not isinstance(selection, dict):
            continue
        if str(selection.get("selection_origin") or "") != "integration_scope_no_devices":
            continue
        if str(selection.get("device_filter_mode") or "") != "all_matching":
            continue
        integration = str(mapping.get("integration_domain") or "")
        provider = get_framework_resource_provider(hass, integration)
        if provider is None:
            continue
        spec = specs_by_builder.get(str(mapping.get("builder_id") or ""))
        if spec is None:
            continue
        getter = getattr(provider, "get_framework_resources", None)
        resources = getter() if callable(getter) else []
        if not legacy_empty_framework_selection(selection, resources or [], integration, spec):
            continue
        mappings.pop(mapping_key, None)
        selections.pop(target_id, None)
        suppressed.append(mapping_key)
    return mappings, selections, sorted(suppressed)


def build_snapshot(hass: Any, entry: Any, *, refresh_reason: str) -> dict[str, Any]:
    config = _config(entry)
    revision = max(1, int(config.get(CONF_CONFIGURATION_REVISION, 1)))
    selected = list(config.get(CONF_SELECTED_INTEGRATIONS, []))
    records, specs = read_publications(hass)
    surface_selections = dict(config.get(CONF_CONFIGURATION_SURFACE_SELECTIONS, {}) or {})
    configured_integrations = {
        str(value)
        for value in (
            list(config.get(CONF_SELECTED_INTEGRATIONS, []) or [])
            + list(config.get(CONF_TECHNICAL_SELECTIONS, []) or [])
        )
        if value
    }
    configured_integrations.update(configured_surface_integrations(surface_selections))
    # Ordinary boot/refresh must inspect only explicit Foundation intent.
    # Published support is configuration metadata, not permission to scan HA.
    relevant_integrations = sorted(configured_integrations)
    explicit_surface_entity_ids = configured_surface_entity_registry_ids(surface_selections)
    catalog = build_catalog(
        hass,
        revision=revision,
        selected_integrations=relevant_integrations,
    )
    surface_entity_candidates = configured_entity_candidates(
        hass, explicit_surface_entity_ids
    )
    persisted_concept_mappings = dict(config.get(CONF_CONCEPT_MAPPINGS, {}) or {})
    persisted_device_selections = dict(config.get(CONF_DEVICE_SELECTIONS, {}) or {})
    persisted_concept_mappings, persisted_device_selections, legacy_suppressed = (
        _suppress_legacy_empty_framework_selections(
            hass,
            persisted_concept_mappings,
            persisted_device_selections,
            specs,
        )
    )
    concept_mappings = rematerialize_concept_mapping_metadata(persisted_concept_mappings, specs)
    device_selections = persisted_device_selections
    selected_device_ids = {
        str(device_id)
        for selection in device_selections.values()
        if isinstance(selection, dict)
        for device_id in (selection.get("selected_device_ids") or [])
        if device_id and str(device_id) != "__all_matching__"
    }
    by_domain, selected_inputs = build_domain_inputs(
        specifications=specs,
        concept_mappings=concept_mappings,
        device_selections=device_selections,
        catalog=catalog,
        configuration_revision=revision,
        device_config_entries=_device_config_entry_map(hass, selected_device_ids),
    )
    surface_by_domain, surface_inputs = build_configured_surface_inputs(
        specifications=specs,
        selections=surface_selections,
        catalog=catalog,
        configuration_revision=revision,
        configured_entity_candidates=surface_entity_candidates,
    )
    for domain_id, inputs in surface_by_domain.items():
        by_domain.setdefault(domain_id, []).extend(inputs)
    all_selected_inputs = selected_inputs + surface_inputs
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
        "catalog_scope_integrations": relevant_integrations,
        "configured_surface_entity_registry_ids": sorted(explicit_surface_entity_ids),
        "concept_mappings": concept_mappings,
        "technical_selections": list(config.get(CONF_TECHNICAL_SELECTIONS, []) or []),
        "device_selections": device_selections,
        "configuration_surface_selections": surface_selections,
        "legacy_suppressed_selections": legacy_suppressed,
        "integration_inventory": _integration_inventory(hass, selected, specs),
        "publication_index": publication_summary(records),
        "domain_build_specifications": specs,
        "capability_catalog": catalog,
        "configured_candidate_groups": configured_groups,
        "concept_trace": concept_trace,
        "selected_domain_build_inputs": all_selected_inputs,
        "selected_domain_build_inputs_by_domain": by_domain,
        "safety": dict(SAFETY),
    }


def _prepare_selected_input_publication(
    hass: Any,
    entry_id: str,
    by_domain: dict[str, list[dict[str, Any]]],
    *,
    reason: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Prepare a handoff registry replacement without making it externally visible."""
    registry = hass.data.setdefault(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {})
    next_registry, events = replace_entry_slice(
        registry, entry_id=entry_id, by_domain=by_domain, reason=reason
    )
    return registry, next_registry, events


async def async_refresh_snapshot(hass: Any, entry: Any, *, reason: str) -> bool:
    """Run one structural Foundation refresh including discovery and handoff publication."""
    data = hass.data[DOMAIN][entry.entry_id]
    lock = data.setdefault("refresh_lock", asyncio.Lock())
    async with lock:
        try:
            await async_refresh_framework_resource_providers(hass)
            candidate = build_snapshot(hass, entry, refresh_reason=reason)
            registry = hass.data.get(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {}) or {}
            persisted_generation = max(
                [
                    int(item.get("build_input_revision", 0) or 0)
                    for value in registry.values()
                    if isinstance(value, dict)
                    for item in (value.get("inputs", []) or [])
                    if isinstance(item, dict)
                ],
                default=0,
            )
            current_generation = max(
                int(data.get("handoff_generation", 0) or 0),
                persisted_generation,
            )
            changed = structural_slice_changed(
                registry,
                entry_id=entry.entry_id,
                by_domain=candidate["selected_domain_build_inputs_by_domain"],
            )
            generation = current_generation + 1 if changed else max(1, current_generation)
            for inputs in candidate["selected_domain_build_inputs_by_domain"].values():
                for selected_input in inputs:
                    selected_input["build_input_revision"] = generation
            for selected_input in candidate["selected_domain_build_inputs"]:
                selected_input["build_input_revision"] = generation
            candidate["handoff_generation"] = generation
            candidate["handoff_structural_changed"] = changed

            # Construct the complete candidate before mutating either Foundation's
            # local snapshot or the shared handoff registry.  This keeps structural
            # publication atomic from downstream domains' point of view.
            current_health = data.setdefault("runtime_health", {})
            derived = derive_success_health(candidate)
            next_health = {
                **current_health,
                **derived,
                "revision": candidate["configuration_revision"],
                "last_success": candidate["generated_at"],
                "last_refresh_at": candidate["generated_at"],
                "last_refresh_reason": reason,
                "last_error": None,
                "successful_refreshes": int(current_health.get("successful_refreshes", 0)) + 1,
                "failed_refreshes": int(current_health.get("failed_refreshes", 0)),
            }
            registry, next_registry, events = _prepare_selected_input_publication(
                hass,
                entry.entry_id,
                candidate["selected_domain_build_inputs_by_domain"],
                reason=reason,
            )

            # Commit local truth and shared handoff in one non-awaiting section.
            # Downstream listeners are notified only after both views are coherent.
            snapshot = data.setdefault("snapshot", {})
            snapshot.clear()
            snapshot.update(candidate)
            registry.clear()
            registry.update(next_registry)
            data["handoff_generation"] = generation
            current_health.clear()
            current_health.update(next_health)
            for event_data in events:
                hass.bus.async_fire(SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, event_data)
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


async def async_refresh_supervision_snapshot(hass: Any, entry: Any, *, reason: str) -> bool:
    """Record one supervision lifecycle observation without caching mutable domain truth.

    Domain status is a live pull contract. Foundation owns the observation trigger,
    but never stores a second copy of domain health inside its structural snapshot.
    This keeps discovery/handoff immutable while eliminating stale duplicate truth.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    observed_at = datetime.now(timezone.utc).isoformat()
    health = data.setdefault("runtime_health", {})
    health.update({
        "last_supervision_refresh_at": observed_at,
        "last_supervision_refresh_reason": reason,
        "last_supervision_error": None,
        "successful_supervision_refreshes": int(
            health.get("successful_supervision_refreshes", 0)
        ) + 1,
        "failed_supervision_refreshes": int(
            health.get("failed_supervision_refreshes", 0)
        ),
    })
    return True

def remove_published_inputs(hass: Any, entry_id: str) -> None:
    registry = hass.data.get(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {})
    removed: list[tuple[str, int, int]] = []
    for key in [key for key, value in registry.items() if isinstance(value, dict) and value.get("foundation_entry_id") == entry_id]:
        value = registry.pop(key)
        inputs = value.get("inputs", []) if isinstance(value.get("inputs"), list) else []
        build_input_revision = max(
            (
                int(item.get("build_input_revision", 0) or 0)
                for item in inputs
                if isinstance(item, dict)
            ),
            default=int(value.get("configuration_revision", 1) or 1),
        )
        removed.append((
            str(value.get("domain_id") or key),
            int(value.get("configuration_revision", 1) or 1),
            build_input_revision,
        ))
    for domain, configuration_revision, build_input_revision in removed:
        hass.bus.async_fire(SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT, {
            "foundation_entry_id": entry_id,
            "domain_id": domain,
            "configuration_revision": max(1, configuration_revision),
            "build_input_revision": max(1, build_input_revision),
            "reason": "removed",
        })