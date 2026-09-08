"""Foundation candidate grouping and SelectedDomainBuildInput preparation."""
from __future__ import annotations

from typing import Any

from .classifier import requirement_capabilities
from .matching import candidate_matches_requirement
from .const import SAFETY


def _cardinality_assessment(cardinality: str | None, count: int) -> tuple[bool, bool, str | None]:
    """Return (complete, ambiguous, issue) for a single cardinality scope."""
    token = cardinality or "zero_or_more"
    exact_one = {"one", "exactly_one", "one_per_system", "exactly_one_per_group"}
    zero_or_one = {"zero_or_one", "zero_or_one_per_group"}
    one_or_more = {"one_or_more", "one_or_more_per_group"}
    zero_or_more = {"zero_or_more", "zero_or_more_per_group"}
    if token in exact_one:
        if count == 1:
            return True, False, None
        if count == 0:
            return False, False, "expected_exactly_one"
        return True, True, "expected_exactly_one"
    if token in zero_or_one:
        if count <= 1:
            return True, False, None
        return True, True, "expected_zero_or_one"
    if token in one_or_more:
        if count >= 1:
            return True, False, None
        return False, False, "expected_one_or_more"
    if token in zero_or_more:
        return True, False, None
    # Unknown cardinality is fail-closed. Publication validation should normally
    # prevent this path.
    return False, True, "unknown_cardinality"


def _is_per_group(cardinality: str | None) -> bool:
    return str(cardinality or "").endswith("_per_group")


def _candidate_group_key(candidate: dict[str, Any]) -> str:
    """Return the stable technical group used for *_per_group cardinality.

    Device identity is preferred because domain topologies such as charger_device
    and vehicle_device are explicitly per physical/technical HA device. For
    non-device candidates Foundation falls back to the narrowest stable technical
    identity it has. This does not create domain semantics.
    """
    source = candidate.get("source_identity") or {}
    return str(
        source.get("device_registry_id")
        or source.get("config_entry_id")
        or source.get("entity_registry_id")
        or source.get("resource_id")
        or source.get("candidate_id")
        or candidate.get("candidate_id")
        or "ungrouped"
    )


def _assess_cardinality(
    cardinality: str | None,
    matches: list[dict[str, Any]],
    *,
    selected_device_ids: set[str] | None,
) -> tuple[bool, bool, list[str]]:
    """Assess cardinality globally or once per technical group.

    *_per_group cardinalities are never evaluated against the total number of
    matches across multiple devices. When the user explicitly selected devices,
    those device ids define the required group universe; otherwise the observed
    matching candidate groups define it.
    """
    if not _is_per_group(cardinality):
        complete, ambiguous, issue = _cardinality_assessment(cardinality, len(matches))
        return complete, ambiguous, ([f"{issue}:count_{len(matches)}"] if issue else [])

    grouped: dict[str, int] = {}
    for candidate in matches:
        key = _candidate_group_key(candidate)
        grouped[key] = grouped.get(key, 0) + 1

    group_ids = set(grouped)
    if selected_device_ids:
        group_ids.update(str(item) for item in selected_device_ids)

    # With no observed or explicitly selected group there is no per-group
    # instance to assess. Required-input handling below remains responsible for
    # reporting a missing input globally.
    if not group_ids:
        return True, False, []

    complete = True
    ambiguous = False
    issues: list[str] = []
    for group_id in sorted(group_ids):
        count = grouped.get(group_id, 0)
        group_complete, group_ambiguous, group_issue = _cardinality_assessment(cardinality, count)
        complete = complete and group_complete
        ambiguous = ambiguous or group_ambiguous
        if group_issue:
            issues.append(f"{group_issue}:group_{group_id}:count_{count}")
    return complete, ambiguous, issues
def build_selected_input(*, specification: dict[str, Any] | None, mapping: dict[str, Any], device_selection: dict[str, Any] | None, catalog: dict[str, Any], configuration_revision: int) -> dict[str, Any]:
    """Prepare a self-contained technical handoff; never create semantic binding."""
    builder_id = str(mapping.get("builder_id") or (specification or {}).get("builder_id") or "unresolved")
    domain_id = str(mapping.get("domain") or (specification or {}).get("domain_id") or "unknown")
    integration_domain = str(mapping.get("integration_domain") or "")
    previous_fingerprint = mapping.get("specification_fingerprint")
    current_fingerprint = (specification or {}).get("specification_fingerprint")
    filter_mode = (device_selection or {}).get("device_filter_mode", "all_matching")
    selected_ids = set((device_selection or {}).get("selected_device_ids", []) or [])
    if "__all_matching__" in selected_ids:
        selected_ids = set()
    selected_scope = None if filter_mode == "all_matching" else selected_ids
    issues: list[str] = []
    groups: list[dict[str, Any]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    required_complete = True
    ambiguous = False

    if specification is None:
        required_complete = False
        issues.append("publisher_or_specification_unavailable")
    else:
        if previous_fingerprint and current_fingerprint and previous_fingerprint != current_fingerprint:
            issues.append("specification_changed_since_configuration")
        for requirement in (specification.get("candidate_requirements") or {}).get("normalized_inputs", []):
            caps = requirement_capabilities(requirement)
            kinds = set(requirement.get("allowed_source_kinds") or [])
            matches: list[dict[str, Any]] = []
            match_rules_by_candidate: dict[str, list[dict[str, Any]]] = {}
            for candidate in catalog.get("candidates", []):
                is_match, matched_rules = candidate_matches_requirement(
                    candidate, requirement,
                    integration_domain=integration_domain,
                    allowed_source_kinds=kinds,
                    capabilities=caps,
                    selected_device_ids=selected_scope,
                )
                if not is_match:
                    continue
                matches.append(candidate)
                candidate_id = str(candidate.get("candidate_id") or "")
                if candidate_id:
                    match_rules_by_candidate[candidate_id] = matched_rules
            required = bool(requirement.get("required"))
            cardinality = requirement.get("cardinality")
            if required and not matches:
                required_complete = False
                issues.append(f"required_input_missing:{requirement.get('input_id')}")
            cardinality_complete, cardinality_ambiguous, cardinality_issues = _assess_cardinality(
                cardinality,
                matches,
                selected_device_ids=selected_scope,
            )
            if not cardinality_complete:
                required_complete = False
            if cardinality_ambiguous:
                ambiguous = True
            for cardinality_issue in cardinality_issues:
                issues.append(f"cardinality_violation:{requirement.get('input_id')}:{cardinality_issue}")
            candidate_ids: list[str] = []
            candidate_matches: list[dict[str, Any]] = []
            for candidate in matches:
                candidate_id = str(candidate.get("candidate_id") or "")
                if not candidate_id:
                    continue
                candidate_ids.append(candidate_id)
                evidence_by_id[candidate_id] = candidate
                for published_match in match_rules_by_candidate.get(candidate_id, []):
                    candidate_matches.append({
                        "candidate_id": candidate_id,
                        "integration_domain": integration_domain,
                        "raw_capability_id": published_match.get("raw_capability_id"),
                        "source_kind": published_match.get("source_kind"),
                        "published_match": published_match,
                    })
            groups.append({
                "input_id": requirement.get("input_id"),
                "required": required,
                "cardinality": cardinality,
                "technical_capabilities": sorted(caps),
                "allowed_source_kinds": sorted(kinds),
                "candidate_ids": candidate_ids,
                "candidate_count": len(candidate_ids),
                "candidate_matches": candidate_matches,
            })

    all_matching = filter_mode == "all_matching"
    review_required = bool(issues or ambiguous or all_matching)
    topology_state = "incomplete" if not required_complete else ("ambiguous" if ambiguous else "unambiguous")
    return {
        "kind": "selected_domain_build_input",
        "contract_version": "1.2.0",
        "domain_id": domain_id,
        "builder_id": builder_id,
        "configuration_revision": max(1, int(configuration_revision)),
        "candidate_revision": max(1, int(catalog.get("catalog_revision", 1))),
        "build_input_revision": max(1, int(configuration_revision)),
        "selection": {
            "integration_domain": integration_domain,
            "concept": mapping.get("concept"),
            "device_filter_mode": filter_mode,
            "selected_device_ids": sorted(selected_ids),
            "configured_specification_fingerprint": previous_fingerprint,
            "current_specification_fingerprint": current_fingerprint,
            "publication_revision": (specification or {}).get("publication_revision"),
        },
        "candidate_groups": groups,
        "candidate_evidence": [evidence_by_id[key] for key in sorted(evidence_by_id)],
        "discovery_assessment": {
            "required_inputs_complete": required_complete,
            "topology_state": topology_state,
            "review_required": review_required,
            "issues": sorted(set(issues)),
        },
        "safety": dict(SAFETY),
    }


def build_domain_inputs(*, specifications: list[dict[str, Any]], concept_mappings: dict[str, dict[str, Any]], device_selections: dict[str, dict[str, Any]], catalog: dict[str, Any], configuration_revision: int) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    specs_by_builder = {str(spec["builder_id"]): spec for spec in specifications}
    by_domain: dict[str, list[dict[str, Any]]] = {}
    diagnostics = []
    for logical_key, mapping in sorted(concept_mappings.items()):
        builder_id = mapping.get("builder_id")
        spec = specs_by_builder.get(str(builder_id)) if builder_id else None
        target_id = f"domain:{logical_key}"
        selected = build_selected_input(
            specification=spec,
            mapping=mapping,
            device_selection=device_selections.get(target_id),
            catalog=catalog,
            configuration_revision=configuration_revision,
        )
        by_domain.setdefault(str(selected["domain_id"]), []).append(selected)
        diagnostics.append(selected)
    return by_domain, diagnostics
