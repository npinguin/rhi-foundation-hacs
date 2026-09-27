"""Pure helpers for optional domain configuration surface intent."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .const import SAFETY


def surface_selection_key(
    domain_id: str,
    concept_id: str,
    surface_id: str,
    instance_id: str,
) -> str:
    return f"surface:{domain_id}.{concept_id}|{surface_id}|{instance_id}"


def surface_fingerprint(surface: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(surface, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def configuration_surface_index(
    specifications: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return one canonical descriptor per domain/concept/surface and fail on drift."""
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    fingerprints: dict[tuple[str, str, str], str] = {}
    for spec in specifications:
        if not isinstance(spec, dict):
            continue
        domain_id = str(spec.get("domain_id") or "")
        concept_id = str((spec.get("concept") or {}).get("concept_id") or "")
        if not domain_id or not concept_id:
            continue
        for raw in spec.get("configuration_surfaces") or []:
            if not isinstance(raw, dict):
                continue
            surface_id = str(raw.get("surface_id") or "")
            if not surface_id:
                continue
            key = (domain_id, concept_id, surface_id)
            candidate = dict(raw)
            fingerprint = surface_fingerprint(candidate)
            previous = fingerprints.get(key)
            if previous is not None and previous != fingerprint:
                raise ValueError(
                    f"configuration_surface_drift:{domain_id}.{concept_id}:{surface_id}"
                )
            fingerprints[key] = fingerprint
            index[key] = candidate
    return dict(sorted(index.items(), key=lambda item: item[0]))


def surfaces_for_concept(
    specifications: list[dict[str, Any]],
    *,
    domain_id: str,
    concept_id: str,
) -> list[dict[str, Any]]:
    index = configuration_surface_index(specifications)
    return [
        dict(surface)
        for (domain, concept, _surface_id), surface in index.items()
        if domain == domain_id and concept == concept_id
    ]


def minimum_satisfied(minimum: dict[str, Any] | None, selected_fields: set[str]) -> bool:
    if not minimum:
        return True
    if "all_of" in minimum:
        values = minimum.get("all_of") or []
        return all(str(item) in selected_fields for item in values)
    if "any_of" in minimum:
        alternatives = minimum.get("any_of") or []
        return any(
            all(str(item) in selected_fields for item in group)
            for group in alternatives
            if isinstance(group, list)
        )
    return False


def configured_surface_integrations(selections: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for selection in selections.values():
        if not isinstance(selection, dict):
            continue
        for field in (selection.get("fields") or {}).values():
            if not isinstance(field, dict):
                continue
            source = field.get("source_identity") or {}
            integration = str(source.get("integration_domain") or "")
            if integration:
                result.add(integration)
    return result


def surface_selection_belongs_to_domain(
    key: str,
    selection: Any,
    domain_id: str,
) -> bool:
    if isinstance(selection, dict) and str(selection.get("domain") or "") == domain_id:
        return True
    return str(key).startswith(f"surface:{domain_id}.")


def _candidate_by_registry_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candidate in catalog.get("candidates", []) or []:
        if not isinstance(candidate, dict):
            continue
        source = candidate.get("source_identity") or {}
        registry_id = str(source.get("entity_registry_id") or "")
        if registry_id:
            result[registry_id] = candidate
    return result


def build_configured_surface_inputs(
    *,
    specifications: list[dict[str, Any]],
    selections: dict[str, Any],
    catalog: dict[str, Any],
    configuration_revision: int,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Build domain-neutral configured surface handoff from explicit user intent."""
    descriptors = configuration_surface_index(specifications)
    candidates = _candidate_by_registry_id(catalog)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    flat: list[dict[str, Any]] = []

    for selection_key, selection in sorted(selections.items()):
        if not isinstance(selection, dict):
            continue
        domain_id = str(selection.get("domain") or "")
        concept_id = str(selection.get("concept") or "")
        surface_id = str(selection.get("surface_id") or "")
        descriptor = descriptors.get((domain_id, concept_id, surface_id))
        selected_field_map = selection.get("fields") or {}
        selected_field_ids = {
            str(field_id)
            for field_id, value in selected_field_map.items()
            if isinstance(value, dict) and value.get("source_identity")
        }

        issues: list[str] = []
        if descriptor is None:
            issues.append("configuration_surface_unavailable")
        elif not minimum_satisfied(descriptor.get("minimum"), selected_field_ids):
            issues.append("configuration_surface_minimum_not_satisfied")

        fields: list[dict[str, Any]] = []
        for field_id, configured in sorted(selected_field_map.items()):
            if not isinstance(configured, dict):
                continue
            source = configured.get("source_identity") or {}
            registry_id = str(source.get("entity_registry_id") or "")
            candidate = candidates.get(registry_id)
            if candidate is None:
                issues.append(f"configured_entity_unavailable:{field_id}")
                fields.append({
                    "field_id": str(field_id),
                    "source_identity": dict(source),
                    "candidate_id": None,
                    "technical_capability": None,
                    "quality": {"availability": "missing"},
                })
                continue
            fields.append({
                "field_id": str(field_id),
                "source_identity": dict(candidate.get("source_identity") or {}),
                "candidate_id": candidate.get("candidate_id"),
                "technical_capability": dict(candidate.get("technical_capability") or {}),
                "quality": dict(candidate.get("quality") or {}),
            })

        handoff = {
            "kind": "configured_domain_surface_input",
            "contract_version": "1.0.0",
            "domain_id": domain_id,
            "concept_id": concept_id,
            "surface_id": surface_id,
            "object_type": selection.get("object_type"),
            "instance_id": selection.get("instance_id"),
            "cardinality": selection.get("cardinality"),
            "configuration_revision": max(1, int(configuration_revision)),
            "candidate_revision": max(1, int(catalog.get("catalog_revision", 1))),
            "build_input_revision": max(1, int(configuration_revision)),
            "configured_surface_fingerprint": selection.get("surface_fingerprint"),
            "current_surface_fingerprint": (
                surface_fingerprint(descriptor) if descriptor is not None else None
            ),
            "fields": fields,
            "discovery_assessment": {
                "required_inputs_complete": not issues,
                "review_required": bool(issues),
                "issues": sorted(set(issues)),
            },
            "safety": dict(SAFETY),
            "selection_key": selection_key,
        }
        by_domain.setdefault(domain_id, []).append(handoff)
        flat.append(handoff)

    return by_domain, flat
