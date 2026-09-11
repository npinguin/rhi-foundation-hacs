"""Mechanical application of domain-published technical matching rules.

Foundation never interprets ``raw_capability_id``. Domain rules may contain
structural ``all_of`` predicates plus optional ``hints``. Hints never authorize a
candidate; ranking is performed only after integration/source-kind/technical-class
and all structural predicates have made the candidate eligible.
"""
from __future__ import annotations

from typing import Any

_ALLOWED_FIELDS = {
    "source_identity.unique_id", "source_identity.service_domain", "source_identity.service_name",
    "source_identity.action_domain", "source_identity.action_type", "source_identity.action_subtype",
    "source_identity.provider_key", "source_identity.api_capability_id", "source_identity.capability_key",
    "technical_capability.device_class", "technical_capability.state_class", "technical_capability.native_unit",
}
_ALLOWED_OPERATORS = {"equals", "starts_with", "ends_with", "contains"}


def _read_path(candidate: dict[str, Any], path: str) -> Any:
    value: Any = candidate
    for token in path.split("."):
        if not isinstance(value, dict): return None
        value = value.get(token)
    return value


def _condition_matches(candidate: dict[str, Any], condition: dict[str, Any]) -> bool:
    field = str(condition.get("field") or ""); operator = str(condition.get("operator") or ""); expected = str(condition.get("value") or "")
    if field not in _ALLOWED_FIELDS or operator not in _ALLOWED_OPERATORS or not expected: return False
    actual_value = _read_path(candidate, field)
    if actual_value is None: return False
    actual = str(actual_value)
    if operator == "equals": return actual == expected
    if operator == "starts_with": return actual.startswith(expected)
    if operator == "ends_with": return actual.endswith(expected)
    if operator == "contains": return expected in actual
    return False


def _conditions_match(candidate: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    return all(_condition_matches(candidate, item) for item in conditions)


def rule_hint_score(candidate: dict[str, Any], rule: dict[str, Any]) -> int:
    """Score supporting evidence for an already structurally eligible candidate."""
    hints = list(rule.get("hints") or [])
    if not hints or not _conditions_match(candidate, hints): return 0
    return len(hints)


def published_matches_for_candidate(candidate: dict[str, Any], requirement: dict[str, Any], *, integration_domain: str) -> list[dict[str, Any]]:
    """Return structurally valid domain rules for one technical candidate."""
    source = candidate.get("source_identity") or {}; source_kind = str(source.get("source_kind") or ""); matched=[]
    for rule in requirement.get("integration_matches") or []:
        if str(rule.get("integration_domain") or "") != integration_domain: continue
        if str(rule.get("source_kind") or "") != source_kind: continue
        if not _conditions_match(candidate, list(rule.get("all_of") or [])): continue
        matched.append(rule)
    return matched


def candidate_matches_requirement(candidate: dict[str, Any], requirement: dict[str, Any], *, integration_domain: str, allowed_source_kinds: set[str], capabilities: set[str], selected_device_ids: set[str] | None, selected_config_entry_ids: set[str] | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Apply generic technical guards plus domain-owned structural rules."""
    source = candidate.get("source_identity") or {}
    if source.get("integration_domain") != integration_domain: return False, []
    if source.get("source_kind") not in allowed_source_kinds: return False, []
    if candidate.get("technical_capability", {}).get("capability_class") not in capabilities: return False, []
    if selected_device_ids is not None:
        device_id = source.get("device_registry_id"); config_entry_id = source.get("config_entry_id")
        direct_match = device_id is not None and device_id in selected_device_ids
        config_entry_match = selected_config_entry_ids is not None and config_entry_id is not None and config_entry_id in selected_config_entry_ids
        if device_id is not None and not direct_match and not config_entry_match: return False, []
        if device_id is None and selected_config_entry_ids is not None and not config_entry_match: return False, []
    rules = requirement.get("integration_matches") or []
    if rules:
        matched = published_matches_for_candidate(candidate, requirement, integration_domain=integration_domain)
        return bool(matched), matched
    return True, []
