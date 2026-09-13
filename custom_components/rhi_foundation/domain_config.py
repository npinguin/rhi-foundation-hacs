"""Pure helpers for domain-scoped Foundation configuration lifecycle.

Foundation persists technical user intent in one Home Assistant config entry, but
configuration ownership is domain-scoped. These helpers update or remove exactly one
domain while preserving every other domain byte-for-byte at the mapping/selection
level. No domain semantics live here.
"""
from __future__ import annotations

from typing import Any

from .const import (
    CONF_CONFIGURATION_REVISION,
    CONF_CONCEPT_MAPPINGS,
    CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS,
    CONF_TECHNICAL_SELECTIONS,
)
from .wizard_state import mapped_integrations


def mapping_belongs_to_domain(value: Any, domain_id: str) -> bool:
    """Return whether one configured concept mapping is owned by ``domain_id``."""
    return isinstance(value, dict) and str(value.get("domain") or "") == domain_id


def device_selection_belongs_to_domain(target_id: str, domain_id: str) -> bool:
    """Return whether one persisted device-selection target belongs to a domain."""
    return str(target_id).startswith(f"domain:{domain_id}.")


def configured_domains(config: dict[str, Any]) -> set[str]:
    """Return domain ids that currently own persisted Foundation intent."""
    result: set[str] = set()
    for value in (config.get(CONF_CONCEPT_MAPPINGS, {}) or {}).values():
        if not isinstance(value, dict):
            continue
        domain_id = str(value.get("domain") or "")
        if domain_id:
            result.add(domain_id)
    return result


def _recompute_selected_integrations(config: dict[str, Any]) -> None:
    mappings = dict(config.get(CONF_CONCEPT_MAPPINGS, {}) or {})
    technical = set(config.get(CONF_TECHNICAL_SELECTIONS, []) or [])
    config[CONF_SELECTED_INTEGRATIONS] = sorted(mapped_integrations(mappings) | technical)


def merge_domain_configuration(
    base_config: dict[str, Any],
    edited_config: dict[str, Any],
    *,
    domain_id: str,
    next_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically replace exactly one domain's Foundation intent.

    Global settings and all other domains come from ``base_config``. Only active
    concept mappings and their matching device selections for ``domain_id`` are taken
    from ``edited_config``. This deliberately drops stale selections for integrations
    deselected during the domain transaction.
    """
    merged = dict(base_config)

    base_mappings = dict(base_config.get(CONF_CONCEPT_MAPPINGS, {}) or {})
    edited_mappings = dict(edited_config.get(CONF_CONCEPT_MAPPINGS, {}) or {})
    edited_domain_mappings = {
        key: value
        for key, value in edited_mappings.items()
        if mapping_belongs_to_domain(value, domain_id)
    }
    mappings = {
        key: value
        for key, value in base_mappings.items()
        if not mapping_belongs_to_domain(value, domain_id)
    }
    mappings.update(edited_domain_mappings)
    merged[CONF_CONCEPT_MAPPINGS] = mappings

    base_devices = dict(base_config.get(CONF_DEVICE_SELECTIONS, {}) or {})
    edited_devices = dict(edited_config.get(CONF_DEVICE_SELECTIONS, {}) or {})
    valid_domain_device_targets = {f"domain:{mapping_key}" for mapping_key in edited_domain_mappings}
    devices = {
        key: value
        for key, value in base_devices.items()
        if not device_selection_belongs_to_domain(key, domain_id)
    }
    devices.update(
        {
            key: value
            for key, value in edited_devices.items()
            if key in valid_domain_device_targets
        }
    )
    merged[CONF_DEVICE_SELECTIONS] = devices

    previous_revision = int(base_config.get(CONF_CONFIGURATION_REVISION, 0) or 0)
    merged[CONF_CONFIGURATION_REVISION] = (
        int(next_revision) if next_revision is not None else previous_revision + 1
    )
    _recompute_selected_integrations(merged)
    return merged


def remove_domain_configuration(
    config: dict[str, Any],
    *,
    domain_id: str,
    next_revision: int | None = None,
) -> dict[str, Any]:
    """Remove exactly one domain's persisted Foundation intent.

    This is destructive user/config-entry lifecycle handling, not transient provider
    unavailability. Callers must only use it for explicit removal or a proven domain
    config-entry removal lifecycle.
    """
    cleaned = dict(config)
    cleaned[CONF_CONCEPT_MAPPINGS] = {
        key: value
        for key, value in (config.get(CONF_CONCEPT_MAPPINGS, {}) or {}).items()
        if not mapping_belongs_to_domain(value, domain_id)
    }
    cleaned[CONF_DEVICE_SELECTIONS] = {
        key: value
        for key, value in (config.get(CONF_DEVICE_SELECTIONS, {}) or {}).items()
        if not device_selection_belongs_to_domain(key, domain_id)
    }
    previous_revision = int(config.get(CONF_CONFIGURATION_REVISION, 0) or 0)
    cleaned[CONF_CONFIGURATION_REVISION] = (
        int(next_revision) if next_revision is not None else previous_revision + 1
    )
    _recompute_selected_integrations(cleaned)
    return cleaned
