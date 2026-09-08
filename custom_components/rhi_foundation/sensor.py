"""Lean Home Assistant diagnostic surface for Foundation V2."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    FOUNDATION_DISPATCH_SIGNAL,
    MODULE_DISPLAY_NAME,
    RELEASE,
    RELEASE_NAME,
    SHARED_BASELINE_ID,
    SHARED_BASELINE_VERSION,
)

# F1.6.2 keeps exactly four bounded operator-facing diagnostics and preserves the
# ambiguous Build role with the Foundation-specific Submodules role.
_ENTITY_UNIQUE_ID_MIGRATIONS = {
    "rhi_foundation_release_identity": "release",
    "rhi_foundation_runtime_health": "health",
    "rhi_foundation_config_index": "configuration",
    "rhi_foundation_selected_build_input_index": "submodules",
    "build": "submodules",
}
_OBSOLETE_UNIQUE_IDS = {
    "rhi_foundation_execution_model",
    "rhi_foundation_ha_integrations_index",
    "rhi_foundation_domain_publication_index",
    "rhi_foundation_capability_catalog",
    "rhi_foundation_configured_candidates_index",
    "rhi_foundation_debug_index",
}


def _migrate_entity_registry(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    for old_unique_id, new_unique_id in _ENTITY_UNIQUE_ID_MIGRATIONS.items():
        old_entity_id = registry.async_get_entity_id("sensor", DOMAIN, old_unique_id)
        new_entity_id = registry.async_get_entity_id("sensor", DOMAIN, new_unique_id)
        if old_entity_id and not new_entity_id:
            desired_entity_id = f"sensor.rhi_foundation_{new_unique_id}"
            if registry.async_get(desired_entity_id) is None:
                registry.async_update_entity(
                    old_entity_id,
                    new_unique_id=new_unique_id,
                    new_entity_id=desired_entity_id,
                )
            else:
                registry.async_update_entity(old_entity_id, new_unique_id=new_unique_id)
    for unique_id in _OBSOLETE_UNIQUE_IDS:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _migrate_entity_registry(hass)
    async_add_entities(
        [
            FoundationReleaseSensor(entry.entry_id),
            FoundationHealthSensor(entry.entry_id),
            FoundationSubmodulesSensor(entry.entry_id),
            FoundationConfigurationSensor(entry.entry_id),
        ]
    )


class FoundationBaseSensor(SensorEntity):
    """Base diagnostic entity attached to the RHI Foundation module device."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=MODULE_DISPLAY_NAME,
            manufacturer="Robotix",
            model="Foundation V2",
            sw_version=RELEASE,
        )

    @property
    def _runtime(self) -> dict[str, Any]:
        return self.hass.data[DOMAIN][self._entry_id]

    @property
    def _snapshot(self) -> dict[str, Any]:
        return self._runtime.get("snapshot", {})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                FOUNDATION_DISPATCH_SIGNAL,
                self._handle_snapshot_updated,
            )
        )

    @callback
    def _handle_snapshot_updated(self, entry_id: str) -> None:
        if entry_id == self._entry_id:
            self.async_write_ha_state()


class FoundationReleaseSensor(FoundationBaseSensor):
    _attr_name = "RHI Foundation Release"
    _attr_native_value = RELEASE
    _attr_unique_id = "release"
    _attr_entity_registry_enabled_default = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "release_name": RELEASE_NAME,
            "shared_baseline_id": SHARED_BASELINE_ID,
            "shared_baseline_version": SHARED_BASELINE_VERSION,
            "module_display_name": MODULE_DISPLAY_NAME,
            "known_accepted_technical_debt": 0,
        }


class FoundationHealthSensor(FoundationBaseSensor):
    _attr_name = "RHI Foundation Health"
    _attr_unique_id = "health"

    @property
    def native_value(self) -> str:
        return str(self._runtime.get("runtime_health", {}).get("state", "UNKNOWN"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self._runtime.get("runtime_health", {})
        return {
            "reason": health.get("reason"),
            "revision": health.get("revision"),
            "last_success": health.get("last_success"),
            "affected_scope": list(health.get("affected_scope", []) or []),
            "last_refresh_at": health.get("last_refresh_at"),
            "last_refresh_reason": health.get("last_refresh_reason"),
            "successful_refreshes": int(health.get("successful_refreshes", 0)),
            "failed_refreshes": int(health.get("failed_refreshes", 0)),
        }


def _rhi_submodules(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return bounded per-submodule operator facts from the authoritative snapshot."""
    installed = {
        str(item.get("integration_domain"))
        for item in snapshot.get("integration_inventory", []) or []
        if str(item.get("integration_domain") or "").startswith("rhi_")
        and str(item.get("integration_domain")) != DOMAIN
    }
    publications = {
        str(item.get("publisher_domain")): item
        for item in snapshot.get("publication_index", []) or []
        if str(item.get("publisher_domain") or "").startswith("rhi_")
    }
    configured_domains = {
        str(mapping.get("domain"))
        for mapping in (snapshot.get("concept_mappings", {}) or {}).values()
        if isinstance(mapping, dict) and mapping.get("domain")
    }
    result: dict[str, dict[str, Any]] = {}
    for module in sorted(installed | set(publications)):
        publication = publications.get(module) or {}
        result[module] = {
            "installed": module in installed,
            "publication_status": publication.get("status", "missing"),
            "specification_count": int(publication.get("specification_count", 0) or 0),
            "issues": list(publication.get("issues", []) or []),
            "configured": module.removeprefix("rhi_") in configured_domains,
        }
    return result


class FoundationSubmodulesSensor(FoundationBaseSensor):
    _attr_name = "RHI Foundation Submodules"
    _attr_unique_id = "submodules"

    @property
    def native_value(self) -> int:
        return sum(1 for item in _rhi_submodules(self._snapshot).values() if item["installed"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        modules = _rhi_submodules(self._snapshot)
        published = [m for m in modules.values() if m["publication_status"] != "missing"]
        valid = [m for m in published if m["publication_status"] == "valid"]
        configurable = [m for m in valid if m["specification_count"] > 0]
        configured = [m for m in modules.values() if m["configured"]]
        return {
            "installed_count": sum(1 for m in modules.values() if m["installed"]),
            "published_count": len(published),
            "valid_publication_count": len(valid),
            "configurable_count": len(configurable),
            "configured_count": len(configured),
            "modules": modules,
        }


class FoundationConfigurationSensor(FoundationBaseSensor):
    _attr_name = "RHI Foundation Configuration"
    _attr_unique_id = "configuration"

    @property
    def native_value(self) -> str:
        snapshot = self._snapshot
        if not snapshot:
            return "UNKNOWN"
        modules = _rhi_submodules(snapshot)
        installed_count = sum(1 for m in modules.values() if m["installed"])
        invalid_count = sum(1 for m in modules.values() if m["publication_status"] == "invalid")
        configurable_count = sum(
            1 for m in modules.values()
            if m["publication_status"] == "valid" and m["specification_count"] > 0
        )
        mappings = snapshot.get("concept_mappings", {}) or {}
        inputs = snapshot.get("selected_domain_build_inputs", []) or []
        if installed_count == 0:
            return "NOT_REQUIRED"
        if invalid_count > 0 or configurable_count == 0:
            return "BLOCKED"
        if not mappings:
            return "REQUIRED"
        if not inputs:
            return "PENDING"
        stale_or_incomplete = any(
            not (item.get("discovery_assessment") or {}).get("required_inputs_complete", False)
            for item in inputs
        )
        return "PENDING" if stale_or_incomplete else "EXECUTED"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        modules = _rhi_submodules(snapshot)
        publications = snapshot.get("publication_index", []) or []
        invalid = {
            name: data["issues"]
            for name, data in modules.items()
            if data["publication_status"] == "invalid"
        }
        empty_valid = sorted(
            name for name, data in modules.items()
            if data["publication_status"] == "valid" and data["specification_count"] == 0
        )
        return {
            "configuration_revision": snapshot.get("configuration_revision"),
            "developer_mode": bool(snapshot.get("developer_mode", False)),
            "installed_submodule_count": sum(1 for m in modules.values() if m["installed"]),
            "publication_count": len(publications),
            "configurable_submodule_count": sum(
                1 for m in modules.values()
                if m["publication_status"] == "valid" and m["specification_count"] > 0
            ),
            "concept_mapping_count": len(snapshot.get("concept_mappings", {}) or {}),
            "selected_build_input_count": len(snapshot.get("selected_domain_build_inputs", []) or []),
            "invalid_publications": invalid,
            "valid_publications_without_specifications": empty_valid,
            "full_evidence": "Home Assistant integration diagnostics",
        }
