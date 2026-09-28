"""HA-native candidate helpers for generic configuration surfaces."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers import entity_registry as er

from .classifier import classify_entity


def compatible_entity_ids(hass: Any, field: dict[str, Any]) -> list[str]:
    """Return enabled HA entities matching only domain-neutral technical constraints."""
    registry = er.async_get(hass)
    accepted_caps = {str(item) for item in (field.get("technical_capabilities") or []) if item}
    accepted_units = {str(item) for item in (field.get("units") or []) if item}
    entity_ids: list[str] = []

    for entity in registry.entities.values():
        if getattr(entity, "disabled_by", None) is not None:
            continue
        entity_id = str(entity.entity_id)
        entity_domain = entity_id.split(".", 1)[0]
        state = hass.states.get(entity_id)
        attributes = state.attributes if state is not None else {}
        device_class = attributes.get("device_class") or getattr(entity, "device_class", None)
        state_class = attributes.get("state_class")
        unit = attributes.get("unit_of_measurement")
        capabilities = set(
            classify_entity(
                entity_domain=entity_domain,
                device_class=device_class,
                state_class=state_class,
                unit=unit,
            )
        )
        if accepted_caps and not (capabilities & accepted_caps):
            continue
        if accepted_units and str(unit or "") not in accepted_units:
            continue
        entity_ids.append(entity_id)
    return sorted(entity_ids)


def entity_source_selection(hass: Any, entity_id: str) -> dict[str, Any] | None:
    """Materialize stable Entity Registry identity for one explicit entity selection."""
    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    if entity is None or getattr(entity, "disabled_by", None) is not None:
        return None

    entry_id = getattr(entity, "config_entry_id", None)
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    integration_domain = str(
        getattr(entry, "domain", "")
        or getattr(entity, "platform", "")
        or str(entity.entity_id).split(".", 1)[0]
    )
    entity_domain = str(entity.entity_id).split(".", 1)[0]
    state = hass.states.get(str(entity.entity_id))
    attributes = state.attributes if state is not None else {}
    device_class = attributes.get("device_class") or getattr(entity, "device_class", None)
    state_class = attributes.get("state_class")
    unit = attributes.get("unit_of_measurement")

    source_identity = {
        "source_kind": "entity",
        "integration_domain": integration_domain,
        "entity_registry_id": str(entity.id),
        "unique_id": str(getattr(entity, "unique_id", None) or entity.id),
        "current_entity_id": str(entity.entity_id),
        "device_registry_id": getattr(entity, "device_id", None),
        "target_scope": "entity",
    }
    if entry_id:
        source_identity["config_entry_id"] = str(entry_id)

    return {
        "source_identity": source_identity,
        "technical_capabilities": classify_entity(
            entity_domain=entity_domain,
            device_class=device_class,
            state_class=state_class,
            unit=unit,
        ),
        "device_class": None if device_class is None else str(device_class),
        "state_class": None if state_class is None else str(state_class),
        "native_unit": None if unit is None else str(unit),
    }



def configured_entity_candidates(
    hass: Any, entity_registry_ids: set[str]
) -> list[dict[str, Any]]:
    """Re-materialize only explicitly configured Entity Registry rows.

    These records are local configured-surface evidence, not generic Foundation
    capability-catalog candidates.  Keeping them separate preserves the Shared
    Baseline EntitySourceIdentity contract, which requires a ConfigEntry, while
    still supporting legitimate YAML/template entities whose registry row has no
    ConfigEntry.
    """
    wanted = {str(item) for item in entity_registry_ids if item}
    if not wanted:
        return []

    registry = er.async_get(hass)
    rows = [
        entity
        for entity in registry.entities.values()
        if str(getattr(entity, "id", "") or "") in wanted
        and getattr(entity, "disabled_by", None) is None
    ]
    candidates: list[dict[str, Any]] = []
    for entity in rows:
        entity_id = str(entity.entity_id)
        state = hass.states.get(entity_id)
        attributes = state.attributes if state is not None else {}
        device_class = attributes.get("device_class") or getattr(entity, "device_class", None)
        state_class = attributes.get("state_class")
        unit = attributes.get("unit_of_measurement")
        entity_domain = entity_id.split(".", 1)[0]
        capabilities = classify_entity(
            entity_domain=entity_domain,
            device_class=device_class,
            state_class=state_class,
            unit=unit,
        )
        source = entity_source_selection(hass, entity_id)
        if source is None:
            continue
        # ConfigEntry-backed direct selections are already present in the ordinary
        # bounded catalog because configured_surface_integrations adds their owning
        # integration scope. Supplemental evidence is only needed for entryless
        # registry entities; adding both would create false candidate ambiguity.
        if (source.get("source_identity") or {}).get("config_entry_id"):
            continue
        availability = (
            "missing"
            if state is None
            else "temporarily_unavailable"
            if state.state in {"unknown", "unavailable"}
            else "available"
        )
        registry_id = str(entity.id)
        for capability in capabilities:
            candidates.append({
                "candidate_id": f"configured-entity:{registry_id}:{capability}",
                "source_identity": dict(source["source_identity"]),
                "technical_capability": {
                    "capability_class": capability,
                    "value_type": (
                        "number"
                        if capability.endswith(("measurement", "counter", "capacity"))
                        else "unknown"
                    ),
                    "writable": capability.endswith("write_surface"),
                    "device_class": None if device_class is None else str(device_class),
                    "state_class": None if state_class is None else str(state_class),
                    "native_unit": None if unit is None else str(unit),
                },
                "quality": {
                    "technical_match_confidence": "high",
                    "availability": availability,
                    "ambiguity": "none",
                },
            })
    return candidates
