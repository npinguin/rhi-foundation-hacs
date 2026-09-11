"""Explicit Foundation-owned technical candidate selection.

A domain publishes what technical input it needs. Foundation may discover one or
many eligible candidates. When generic evidence cannot distinguish them, user intent
selects the exact technical candidate per input/object. This remains technical
configuration: it does not assign domain meaning beyond the domain-published input.
"""
from __future__ import annotations

from typing import Any

from .candidate_ranking import candidate_group_key


def configured_candidate_id(
    device_selection: dict[str, Any] | None,
    *,
    input_id: str,
    group_id: str,
) -> str | None:
    selections = (device_selection or {}).get("candidate_selections") or {}
    if not isinstance(selections, dict):
        return None
    by_group = selections.get(str(input_id)) or {}
    if not isinstance(by_group, dict):
        return None
    value = by_group.get(str(group_id))
    return str(value) if isinstance(value, str) and value else None


def apply_configured_candidate_selection(
    matches: list[dict[str, Any]],
    match_rules_by_candidate: dict[str, list[dict[str, Any]]],
    *,
    input_id: str,
    cardinality: str | None,
    device_selection: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    """Apply exact configured candidate IDs per technical group, fail closed on drift."""
    token = str(cardinality or "")
    if token not in {
        "one", "exactly_one", "one_per_system", "exactly_one_per_group",
        "zero_or_one", "zero_or_one_per_group",
    } or not matches:
        return matches, match_rules_by_candidate, []

    per_group = token.endswith("_per_group")
    groups: dict[str, list[dict[str, Any]]] = {}
    if per_group:
        for candidate in matches:
            groups.setdefault(candidate_group_key(candidate), []).append(candidate)
    else:
        groups["__selection__"] = list(matches)

    result: list[dict[str, Any]] = []
    result_rules: dict[str, list[dict[str, Any]]] = {}
    issues: list[str] = []
    for group_id, candidates in sorted(groups.items()):
        configured = configured_candidate_id(
            device_selection, input_id=input_id, group_id=group_id
        )
        if not configured:
            result.extend(candidates)
            for candidate in candidates:
                cid = str(candidate.get("candidate_id") or "")
                result_rules[cid] = match_rules_by_candidate.get(cid, [])
            continue
        winners = [candidate for candidate in candidates if candidate.get("candidate_id") == configured]
        if len(winners) != 1:
            issues.append(f"configured_candidate_unavailable:{input_id}:group_{group_id}:{configured}")
            result.extend(candidates)
            for candidate in candidates:
                cid = str(candidate.get("candidate_id") or "")
                result_rules[cid] = match_rules_by_candidate.get(cid, [])
            continue
        winner = winners[0]
        cid = str(winner["candidate_id"])
        result.append(winner)
        result_rules[cid] = match_rules_by_candidate.get(cid, [])
    return result, result_rules, issues
