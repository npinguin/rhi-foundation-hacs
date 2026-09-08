"""Pure end-to-end concept trace diagnostics for RHI Foundation."""
from __future__ import annotations

from typing import Any

def build_concept_trace(
    *,
    specifications: list[dict[str, Any]],
    concept_mappings: dict[str, dict[str, Any]],
    device_selections: dict[str, dict[str, Any]],
    selected_inputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build an end-to-end concept trace from existing authoritative objects.

    This is diagnostics-only observability.  It does not create new selection
    state, domain semantics, or runtime truth.  The trace makes the exact chain
    visible: DomainBuildSpecification -> configured intent -> candidate result ->
    SelectedDomainBuildInput.
    """
    specs_by_concept: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for spec in specifications:
        concept = spec.get("concept") or {}
        key = (str(spec.get("domain_id") or ""), str(concept.get("concept_id") or ""))
        if all(key):
            specs_by_concept.setdefault(key, []).append(spec)

    mappings_by_concept: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mapping in concept_mappings.values():
        if not isinstance(mapping, dict):
            continue
        key = (str(mapping.get("domain") or ""), str(mapping.get("concept") or ""))
        if all(key):
            mappings_by_concept.setdefault(key, []).append(mapping)

    inputs_by_concept: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in selected_inputs:
        selection = item.get("selection") or {}
        key = (str(item.get("domain_id") or ""), str(selection.get("concept") or ""))
        if all(key):
            inputs_by_concept.setdefault(key, []).append(item)

    keys = sorted(set(specs_by_concept) | set(mappings_by_concept) | set(inputs_by_concept))
    trace: list[dict[str, Any]] = []
    for domain_id, concept_id in keys:
        specs = specs_by_concept.get((domain_id, concept_id), [])
        mappings = mappings_by_concept.get((domain_id, concept_id), [])
        inputs = inputs_by_concept.get((domain_id, concept_id), [])
        first_concept = (specs[0].get("concept") or {}) if specs else {}

        published_builders = []
        published_integrations: set[str] = set()
        for spec in specs:
            supported = [
                str(source.get("integration_domain") or "")
                for source in spec.get("supported_sources", [])
                if source.get("integration_domain")
            ]
            published_integrations.update(supported)
            published_builders.append({
                "builder_id": spec.get("builder_id"),
                "builder_version": spec.get("builder_version"),
                "publication_revision": spec.get("publication_revision"),
                "specification_fingerprint": spec.get("specification_fingerprint"),
                "supported_integrations": sorted(supported),
                "topology": dict((spec.get("candidate_requirements") or {}).get("topology") or {}),
                "normalized_input_count": len((spec.get("candidate_requirements") or {}).get("normalized_inputs", []) or []),
            })

        configured = []
        unresolved_review = False
        missing_device_selection = False
        for mapping in sorted(mappings, key=lambda m: str(m.get("integration_domain") or "")):
            integration = str(mapping.get("integration_domain") or "")
            target_id = f"domain:{domain_id}.{concept_id}|{integration}"
            selection = device_selections.get(target_id) or {}
            state = str(selection.get("selection_state") or "missing")
            unresolved_review = unresolved_review or state == "review_required"
            missing_device_selection = missing_device_selection or not bool(selection)
            configured.append({
                "integration_domain": integration,
                "builder_id": mapping.get("builder_id"),
                "configured_publication_revision": mapping.get("publication_revision"),
                "configured_specification_fingerprint": mapping.get("specification_fingerprint"),
                "device_filter_mode": selection.get("device_filter_mode"),
                "selected_device_ids": list(selection.get("selected_device_ids", []) or []),
                "selection_state": state,
            })

        returned = []
        input_issues: list[str] = []
        for item in inputs:
            assessment = item.get("discovery_assessment") or {}
            input_issues.extend(str(issue) for issue in assessment.get("issues", []) or [])
            returned.append({
                "builder_id": item.get("builder_id"),
                "integration_domain": (item.get("selection") or {}).get("integration_domain"),
                "device_filter_mode": (item.get("selection") or {}).get("device_filter_mode"),
                "selected_device_ids": list((item.get("selection") or {}).get("selected_device_ids", []) or []),
                "candidate_group_count": len(item.get("candidate_groups", []) or []),
                "candidate_evidence_count": len(item.get("candidate_evidence", []) or []),
                "required_inputs_complete": assessment.get("required_inputs_complete"),
                "topology_state": assessment.get("topology_state"),
                "review_required": assessment.get("review_required"),
                "issues": list(assessment.get("issues", []) or []),
            })

        issues: list[str] = []
        if specs and not mappings:
            issues.append("missing_configured_selection")
        if mappings and missing_device_selection:
            issues.append("missing_device_selection")
        if unresolved_review:
            issues.append("review_required_not_resolved")
        if mappings and not inputs:
            issues.append("selected_build_input_not_published")
        issues.extend(input_issues)

        if not specs:
            flow_state = "publication_unavailable"
        elif not mappings:
            flow_state = "integration_selection_required"
        elif missing_device_selection:
            flow_state = "device_selection_required"
        elif unresolved_review:
            flow_state = "device_review_required"
        elif any(item.get("required_inputs_complete") is False for item in returned):
            flow_state = "build_input_incomplete"
        elif any(item.get("review_required") for item in returned):
            flow_state = "build_input_review_required"
        else:
            flow_state = "configured"

        trace.append({
            "domain_id": domain_id,
            "concept_id": concept_id,
            "display_name": first_concept.get("display_name") or next((m.get("display_name") for m in mappings if m.get("display_name")), concept_id),
            "published_as_separate_concept": bool(specs),
            "flow_state": flow_state,
            "published": {
                "specification_count": len(specs),
                "published_integrations": sorted(published_integrations),
                "builders": published_builders,
            },
            "foundation_configuration": configured,
            "returned_selected_domain_build_inputs": returned,
            "trace_issues": sorted(set(issues)),
        })
    return trace
