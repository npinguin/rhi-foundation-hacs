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
        },
        "build_handoff": {
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
