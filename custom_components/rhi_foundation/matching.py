"""Mechanical application of domain-published raw candidate matching rules.

Foundation does not interpret raw_capability_id. It only applies the bounded
matching rule published by the owning domain to observed technical evidence.
"""
from __future__ import annotations

from typing import Any

_ALLOWED_FIELDS = {
    "source_identity.unique_id",
    "source_identity.service_domain",
    "source_identity.service_name",
    "source_identity.action_domain",
    "source_identity.action_type",
    "source_identity.action_subtype",
    "source_identity.provider_key",
    "source_identity.api_capability_id",
    "source_identity.capability_key",
    "technical_capability.device_class",
    "technical_capability.state_class",
    "technical_capability.native_unit",
}
_ALLOWED_OPERATORS = {"equals", "starts_with", "ends_with", "contains"}


def _read_path(candidate: dict[str, Any], path: str) -> Any:
    value: Any = candidate
    for token in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(token)
    return value


def _condition_matches(candidate: dict[str, Any], condition: dict[str, Any]) -> bool:
    field = str(condition.get("field") or "")
    operator = str(condition.get("operator") or "")
    expected = str(condition.get("value") or "")
    if field not in _ALLOWED_FIELDS or operator not in _ALLOWED_OPERATORS or not expected:
        return False
    actual_value = _read_path(candidate, field)
    if actual_value is None:
        return False
    actual = str(actual_value)
    if operator == "equals":
        return actual == expected
    if operator == "starts_with":
        return actual.startswith(expected)
    if operator == "ends_with":
        return actual.endswith(expected)
    if operator == "contains":
        return expected in actual
    return False


def published_matches_for_candidate(
    candidate: dict[str, Any],
    requirement: dict[str, Any],
    *,
    integration_domain: str,
) -> list[dict[str, Any]]:
    """Return domain-published raw matches satisfied by one technical candidate.

    If a requirement has no integration_matches (legacy/dev input), no raw match
    is produced. Baseline 1.7.0 domain specifications are validated to publish
    integration_matches for every normalized input.
    """
    source = candidate.get("source_identity") or {}
    source_kind = str(source.get("source_kind") or "")
    matched: list[dict[str, Any]] = []
    for rule in requirement.get("integration_matches") or []:
        if str(rule.get("integration_domain") or "") != integration_domain:
            continue
        if str(rule.get("source_kind") or "") != source_kind:
            continue
        conditions = list(rule.get("all_of") or [])
        if not conditions or not all(_condition_matches(candidate, item) for item in conditions):
            continue
        matched.append(rule)
    return matched


def candidate_matches_requirement(
    candidate: dict[str, Any],
    requirement: dict[str, Any],
    *,
    integration_domain: str,
    allowed_source_kinds: set[str],
    capabilities: set[str],
    selected_device_ids: set[str] | None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Apply generic technical guards plus domain-owned raw matching rules."""
    source = candidate.get("source_identity") or {}
    if source.get("integration_domain") != integration_domain:
        return False, []
    if source.get("source_kind") not in allowed_source_kinds:
        return False, []
    if candidate.get("technical_capability", {}).get("capability_class") not in capabilities:
        return False, []
    device_id = source.get("device_registry_id")
    if selected_device_ids is not None and device_id is not None and device_id not in selected_device_ids:
        return False, []

    rules = requirement.get("integration_matches") or []
    if rules:
        matched = published_matches_for_candidate(candidate, requirement, integration_domain=integration_domain)
        return bool(matched), matched
    # Only retained for internal/dev compatibility; Baseline 1.7.0 published
    # DomainBuildSpecification 1.2.0 is required to carry integration_matches.
    return True, []
