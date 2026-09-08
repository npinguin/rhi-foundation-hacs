"""Home Assistant 2026.8 registry access helpers."""
from __future__ import annotations

from typing import Any


def device_belongs_to_config_entry(device: Any, config_entry_id: str) -> bool:
    """HA 2026.8 devices have one owning config entry."""
    return getattr(device, "config_entry_id", None) == config_entry_id


def devices_for_config_entry(device_registry: Any, config_entry_id: str) -> list[Any]:
    return [
        device
        for device in device_registry.devices.values()
        if device_belongs_to_config_entry(device, config_entry_id)
    ]


def integration_entry_ids(hass: Any, integration_domain: str) -> set[str]:
    return {
        entry.entry_id
        for entry in hass.config_entries.async_entries()
        if entry.domain == integration_domain
    }
