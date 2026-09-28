"""Home Assistant diagnostics for bounded, structured Foundation evidence."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, RELEASE, SHARED_BASELINE_VERSION


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the authoritative troubleshooting payload; sensors remain summaries."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    snapshot = deepcopy(runtime.get("snapshot", {}))
    published_surfaces: list[dict[str, Any]] = []
    for specification in snapshot.get("domain_build_specifications", []) or []:
        if not isinstance(specification, dict):
            continue
        domain_id = str(specification.get("domain_id") or "")
        concept_id = str((specification.get("concept") or {}).get("concept_id") or "")
        for surface in specification.get("configuration_surfaces", []) or []:
            if not isinstance(surface, dict):
                continue
            published_surfaces.append({
                "domain_id": domain_id,
                "concept_id": concept_id,
                "surface_id": surface.get("surface_id"),
                "object_type": surface.get("object_type"),
                "cardinality": surface.get("cardinality"),
                "minimum": deepcopy(surface.get("minimum")),
                "fields": [
                    {
                        "field_id": field.get("field_id"),
                        "display_name": field.get("display_name"),
                        "technical_capabilities": deepcopy(field.get("technical_capabilities") or []),
                        "units": deepcopy(field.get("units") or []),
                    }
                    for field in (surface.get("fields") or [])
                    if isinstance(field, dict)
                ],
            })
    return {
        "identity": {
            "integration_domain": DOMAIN,
            "release": RELEASE,
            "shared_baseline_version": SHARED_BASELINE_VERSION,
            "config_entry_id": entry.entry_id,
            "config_entry_version": entry.version,
        },
        "health": deepcopy(runtime.get("runtime_health", {})),
        "supervision": {
            "system": snapshot.get("system_supervision", {}),
            "domains": snapshot.get("domain_supervisory_statuses", []),
        },
        "configuration": {
            "data": deepcopy(dict(entry.data)),
            "options": deepcopy(dict(entry.options)),
            "configuration_revision": snapshot.get("configuration_revision"),
            "developer_mode": snapshot.get("developer_mode", False),
            "selected_integrations": snapshot.get("selected_integrations", []),
            "concept_mappings": snapshot.get("concept_mappings", {}),
            "device_selections": snapshot.get("device_selections", {}),
            "configuration_surface_selections": snapshot.get("configuration_surface_selections", {}),
        },
        "build_handoff": {
            "configuration_surfaces": published_surfaces,
            "publication_index": snapshot.get("publication_index", []),
            "domain_build_specifications": snapshot.get("domain_build_specifications", []),
            "concept_trace": snapshot.get("concept_trace", []),
            "configured_candidate_groups": snapshot.get("configured_candidate_groups", []),
            "selected_domain_build_inputs": snapshot.get("selected_domain_build_inputs", []),
        },
        "technical_discovery": {
            "integration_inventory": snapshot.get("integration_inventory", []),
            "capability_catalog": snapshot.get("capability_catalog", {}),
        },
        "execution": {
            "generated_at": snapshot.get("generated_at"),
            "refresh_reason": snapshot.get("refresh_reason"),
            "safety": snapshot.get("safety", {}),
        },
    }
