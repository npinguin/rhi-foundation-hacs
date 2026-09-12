"""Robotix Home Intelligence - Foundation Module."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_CONFIGURATION_REVISION,
    CONF_CONCEPT_MAPPINGS,
    CONF_DEVELOPER_MODE,
    CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS,
    CONF_TECHNICAL_SELECTIONS,
    DOMAIN,
    DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
    FOUNDATION_DISPATCH_SIGNAL,
    FOUNDATION_REFRESH_SERVICE,
    PLATFORMS,
)
from .runtime import async_refresh_snapshot, remove_published_inputs
from .wizard_state import canonicalize_multi_mapping_shape

CONFIG_ENTRY_VERSION = 5


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate F1.1.x configuration without silently inventing mappings."""
    if entry.version >= CONFIG_ENTRY_VERSION:
        return True

    def _migrate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload.setdefault(CONF_DEVELOPER_MODE, False)
        payload.setdefault(CONF_SELECTED_INTEGRATIONS, [])
        payload.setdefault(CONF_CONCEPT_MAPPINGS, {})
        payload.setdefault(CONF_TECHNICAL_SELECTIONS, [])
        payload.setdefault(CONF_DEVICE_SELECTIONS, {})
        payload.setdefault(CONF_CONFIGURATION_REVISION, 1)
        mappings = dict(payload.get(CONF_CONCEPT_MAPPINGS, {}) or {})
        for mapping in mappings.values():
            if not isinstance(mapping, dict):
                continue
            if not mapping.get("builder_id"):
                builder_ids = list(mapping.get("builder_ids", []) or [])
                if len(builder_ids) == 1:
                    mapping["builder_id"] = builder_ids[0]
            mapping.pop("builder_ids", None)
            mapping.pop("contract_kinds", None)
        device_selections = dict(payload.get(CONF_DEVICE_SELECTIONS, {}) or {})
        mappings, device_selections = canonicalize_multi_mapping_shape(mappings, device_selections)
        payload[CONF_CONCEPT_MAPPINGS] = mappings
        payload[CONF_DEVICE_SELECTIONS] = device_selections
        mapped = {
            str(mapping.get("integration_domain"))
            for mapping in mappings.values()
            if isinstance(mapping, dict) and mapping.get("integration_domain")
        }
        technical = set(payload.get(CONF_TECHNICAL_SELECTIONS, []) or [])
        technical.update(set(payload.get(CONF_SELECTED_INTEGRATIONS, []) or []) - mapped)
        payload[CONF_TECHNICAL_SELECTIONS] = sorted(technical)
        return payload

    data = _migrate_payload(dict(entry.data))
    options = _migrate_payload(dict(entry.options)) if entry.options else {}
    hass.config_entries.async_update_entry(
        entry, data=data, options=options, version=CONFIG_ENTRY_VERSION
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a single configuration-time-active Foundation instance."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "snapshot": {},
        "runtime_health": {
            "state": "UNKNOWN",
            "reason": "starting",
            "revision": 0,
            "last_success": None,
            "affected_scope": ["foundation"],
            "successful_refreshes": 0,
            "failed_refreshes": 0,
            "last_error": None,
        },
    }

    async def _refresh(reason: str) -> None:
        await async_refresh_snapshot(hass, entry, reason=reason)
        async_dispatcher_send(hass, FOUNDATION_DISPATCH_SIGNAL, entry.entry_id)

    async def _on_publication_changed(event: Event) -> None:
        await _refresh(
            f"domain_publication:{event.data.get('reason', 'changed')}:"
            f"{event.data.get('publisher_domain', 'unknown')}"
        )

    async def _on_supervision_changed(event: Event) -> None:
        await _refresh(
            f"domain_supervision:{event.data.get('reason', 'changed')}:"
            f"{event.data.get('domain_id', 'unknown')}"
        )

    async def _handle_refresh_service(call: ServiceCall) -> None:
        await _refresh("explicit_service_refresh")

    unsub_publication = hass.bus.async_listen(
        DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
        _on_publication_changed,
    )
    entry.async_on_unload(unsub_publication)
    unsub_supervision = hass.bus.async_listen(
        DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
        _on_supervision_changed,
    )
    entry.async_on_unload(unsub_supervision)

    if not hass.services.has_service(DOMAIN, FOUNDATION_REFRESH_SERVICE):
        hass.services.async_register(
            DOMAIN,
            FOUNDATION_REFRESH_SERVICE,
            _handle_refresh_service,
        )

    await _refresh("setup")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload only after an explicit config/options save."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        remove_published_inputs(hass, entry.entry_id)
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if hass.services.has_service(DOMAIN, FOUNDATION_REFRESH_SERVICE):
            hass.services.async_remove(DOMAIN, FOUNDATION_REFRESH_SERVICE)
    return unloaded
