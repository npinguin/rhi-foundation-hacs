"""Fail-closed ranking of structurally eligible capability candidates.

Ranking is generic and domain-neutral. Hints can break a tie only after technical
eligibility has been proven. Generic state observations are deliberately excluded:
a name such as ``lock_state`` must never promote a plug lock to whole-object security.
Per-group cardinalities are ranked independently per HA technical object.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .matching import rule_hint_score

_SINGLE_CARDINALITIES = {
    "one", "exactly_one", "one_per_system", "exactly_one_per_group",
    "zero_or_one", "zero_or_one_per_group",
}
_HINT_RANKABLE_CAPABILITIES = {
    "power_measurement", "energy_counter", "energy_capacity", "percentage_measurement",
    "current_measurement", "voltage_measurement", "frequency_measurement", "temperature_measurement",
    "price_measurement", "number_write_surface", "select_write_surface", "binary_write_surface",
    "service_command_surface", "power_capacity", "current_limit", "percentage_configuration",
}


def candidate_group_key(candidate: dict[str, Any]) -> str:
    source = candidate.get("source_identity") or {}
    return str(
        source.get("device_registry_id")
        or source.get("config_entry_id")
        or source.get("entity_registry_id")
        or source.get("resource_id")
        or candidate.get("candidate_id")
        or "ungrouped"
    )


def _rank_one_group(
    matches: list[dict[str, Any]],
    match_rules_by_candidate: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if len(matches) <= 1:
        return matches, {str(c.get("candidate_id") or ""): match_rules_by_candidate.get(str(c.get("candidate_id") or ""), []) for c in matches}
    candidate_capabilities = {
        str((candidate.get("technical_capability") or {}).get("capability_class") or "")
        for candidate in matches
    }
    if not candidate_capabilities or not candidate_capabilities <= _HINT_RANKABLE_CAPABILITIES:
        return matches, {str(c.get("candidate_id") or ""): match_rules_by_candidate.get(str(c.get("candidate_id") or ""), []) for c in matches}
    scored: list[tuple[int, dict[str, Any]]] = []
    for candidate in matches:
        candidate_id = str(candidate.get("candidate_id") or "")
        rules = match_rules_by_candidate.get(candidate_id, [])
        score = max((rule_hint_score(candidate, rule) for rule in rules), default=0)
        scored.append((score, candidate))
    best = max(score for score, _ in scored)
    if best <= 0:
        return matches, {str(c.get("candidate_id") or ""): match_rules_by_candidate.get(str(c.get("candidate_id") or ""), []) for c in matches}
    winners = [candidate for score, candidate in scored if score == best]
    if len(winners) != 1:
        return matches, {str(c.get("candidate_id") or ""): match_rules_by_candidate.get(str(c.get("candidate_id") or ""), []) for c in matches}
    winner = winners[0]
    winner_id = str(winner.get("candidate_id") or "")
    return winners, {winner_id: match_rules_by_candidate.get(winner_id, [])}


def rank_structural_matches(
    matches: list[dict[str, Any]],
    match_rules_by_candidate: dict[str, list[dict[str, Any]]],
    *,
    cardinality: str | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Narrow uniquely hinted typed candidates without crossing object boundaries."""
    token = str(cardinality or "")
    if len(matches) <= 1 or token not in _SINGLE_CARDINALITIES:
        return matches, match_rules_by_candidate
    if not token.endswith("_per_group"):
        return _rank_one_group(matches, match_rules_by_candidate)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in matches:
        grouped[candidate_group_key(candidate)].append(candidate)
    selected: list[dict[str, Any]] = []
    selected_rules: dict[str, list[dict[str, Any]]] = {}
    for group_id in sorted(grouped):
        group_matches, group_rules = _rank_one_group(grouped[group_id], match_rules_by_candidate)
        selected.extend(group_matches)
        selected_rules.update(group_rules)
    return selected, selected_rules
