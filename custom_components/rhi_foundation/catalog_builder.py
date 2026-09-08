"""Bounded Home Assistant technical capability catalog builder."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers import entity_registry as er

from .classifier import classify_config_key, classify_entity
from .contracts.serialization import catalog_to_contract
from .discovery.candidate_id import build_candidate_id
from .models.capability import CapabilityEvidence, CapabilityQuality, FoundationCapabilityCandidate, TechnicalCapability
from .models.catalog import FoundationCapabilityCatalog
from .models.source_identity import ConfigEntryProviderSourceIdentity, EntitySourceIdentity, ServiceSourceIdentity


def _availability(hass: Any, entity_id: str) -> str:
    state=hass.states.get(entity_id)
    if state is None: return "missing"
    if state.state in {"unavailable","unknown"}: return "temporarily_unavailable"
    return "available"


def _candidate(source: Any, capability_class: str, *, value_type: str, writable: bool, device_class: Any=None, state_class: Any=None, unit: Any=None, provenance: tuple[str,...], availability: str="available") -> FoundationCapabilityCandidate:
    return FoundationCapabilityCandidate(
        candidate_id=build_candidate_id(source, capability_class),
        candidate_revision=1,
        source_identity=source,
        technical_capability=TechnicalCapability(
            capability_class=capability_class, value_type=value_type, writable=writable,
            device_class=None if device_class is None else str(device_class),
            state_class=None if state_class is None else str(state_class),
            native_unit=None if unit is None else str(unit),
        ),
        evidence=CapabilityEvidence(provenance=provenance),
        quality=CapabilityQuality(technical_match_confidence="high", availability=availability, ambiguity="none"),
    )


def build_catalog(hass: Any, *, revision: int, selected_integrations: list[str] | None=None) -> dict[str, Any]:
    """Build the generic catalog only at a bounded configuration/structural trigger."""
    selected=set(selected_integrations) if selected_integrations is not None else None
    entries=list(hass.config_entries.async_entries())
    entry_by_id={entry.entry_id:entry for entry in entries}
    registry=er.async_get(hass)
    candidates: list[FoundationCapabilityCandidate]=[]

    for entity in registry.entities.values():
        entry_id=getattr(entity,"config_entry_id",None)
        entry=entry_by_id.get(entry_id)
        if entry is None: continue
        if selected is not None and entry.domain not in selected: continue
        entity_domain=str(entity.entity_id).split(".",1)[0]
        dc=getattr(entity,"device_class",None)
        sc=getattr(entity,"state_class",None)
        unit=getattr(entity,"unit_of_measurement",None)
        source=EntitySourceIdentity(
            source_kind="entity", integration_domain=str(entry.domain), config_entry_id=str(entry.entry_id),
            entity_registry_id=str(entity.id), unique_id=str(getattr(entity,"unique_id",None) or entity.id),
            current_entity_id=str(entity.entity_id), device_registry_id=getattr(entity,"device_id",None), target_scope="entity",
        )
        availability=_availability(hass, str(entity.entity_id))
        for capability_class in classify_entity(entity_domain=entity_domain, device_class=dc, state_class=sc, unit=unit):
            candidate=_candidate(source, capability_class, value_type="number" if capability_class.endswith(("measurement","counter","capacity")) else "unknown", writable=capability_class.endswith("write_surface"), device_class=dc, state_class=sc, unit=unit, provenance=("entity_registry","entity_metadata"), availability=availability)
            candidates.append(candidate)

    # Config-entry technical metadata contributes capability presence only; values are deliberately not exported.
    for entry in entries:
        if selected is not None and entry.domain not in selected: continue
        merged={}
        merged.update(dict(getattr(entry,"data",{}) or {}))
        merged.update(dict(getattr(entry,"options",{}) or {}))
        for key in sorted(merged):
            capability_class=classify_config_key(str(key))
            if not capability_class: continue
            source=ConfigEntryProviderSourceIdentity(
                source_kind="config_entry_provider", integration_domain=str(entry.domain),
                config_entry_id=str(entry.entry_id), provider_key=str(key), target_scope="config_entry",
            )
            candidates.append(_candidate(source, capability_class, value_type="number", writable=False, provenance=("config_entry_metadata","technical_unit_key")))

    # Generic service surfaces are safe to catalogue for selected integration domains.
    service_map=hass.services.async_services()
    for service_domain, services in sorted(service_map.items()):
        if selected is not None and service_domain not in selected: continue
        matching_entries=[entry for entry in entries if entry.domain == service_domain]
        for entry in matching_entries:
            for service_name in sorted(services):
                source=ServiceSourceIdentity(
                    source_kind="service", integration_domain=str(service_domain), service_domain=str(service_domain),
                    service_name=str(service_name), target={"config_entry_id":str(entry.entry_id)},
                    config_entry_id=str(entry.entry_id), target_scope="config_entry",
                )
                candidates.append(_candidate(source,"service_command_surface",value_type="service",writable=True,provenance=("service_registry","config_entry_scope")))

    candidates.sort(key=lambda c:c.candidate_id)
    catalog=FoundationCapabilityCatalog(
        catalog_revision=max(1,revision), candidates=tuple(candidates), generated_at=datetime.now(timezone.utc).isoformat()
    )
    return catalog_to_contract(catalog)
