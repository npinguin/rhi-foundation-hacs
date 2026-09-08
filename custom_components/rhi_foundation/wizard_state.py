"""Pure wizard state helpers for RHI Foundation.

This module deliberately has no Home Assistant imports so configuration-shape
migration and multi-integration concept semantics can be unit-tested directly.
"""
from __future__ import annotations

from typing import Any


def mapping_key(domain: str, concept: str, integration: str) -> str:
    """Return stable configured-selection key for one concept + integration."""
    return f"{domain}.{concept}|{integration}"


def concept_prefix(domain: str, concept: str) -> str:
    return f"{domain}.{concept}|"


def concept_target_id(domain: str, concept: str, integration: str) -> str:
    return f"domain:{mapping_key(domain, concept, integration)}"


def mappings_for_concept(
    mappings: dict[str, dict[str, Any]], domain: str, concept: str
) -> dict[str, dict[str, Any]]:
    """Return mappings for one concept keyed by integration domain."""
    result: dict[str, dict[str, Any]] = {}
    for value in mappings.values():
        if not isinstance(value, dict):
            continue
        if value.get("domain") != domain or value.get("concept") != concept:
            continue
        integration = str(value.get("integration_domain") or "")
        if integration:
            result[integration] = value
    return result


def mapped_integrations(mappings: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(value.get("integration_domain"))
        for value in mappings.values()
        if isinstance(value, dict) and value.get("integration_domain")
    }


def canonicalize_multi_mapping_shape(
    mappings: dict[str, dict[str, Any]],
    device_selections: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Migrate legacy concept-only keys to concept+integration keys.

    Existing data is preserved. A mapping without an integration cannot be
    invented and therefore remains under its original key for diagnostics.
    """
    new_mappings: dict[str, dict[str, Any]] = {}
    target_rewrites: dict[str, str] = {}

    for old_key, raw in (mappings or {}).items():
        mapping = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(mapping, dict):
            new_mappings[old_key] = mapping
            continue
        domain = str(mapping.get("domain") or "")
        concept = str(mapping.get("concept") or "")
        integration = str(mapping.get("integration_domain") or "")
        if domain and concept and integration:
            new_key = mapping_key(domain, concept, integration)
        else:
            new_key = str(old_key)
        new_mappings[new_key] = mapping
        if new_key != old_key:
            target_rewrites[f"domain:{old_key}"] = f"domain:{new_key}"

    new_devices: dict[str, dict[str, Any]] = {}
    for old_target, selection in (device_selections or {}).items():
        new_devices[target_rewrites.get(old_target, old_target)] = selection
    return new_mappings, new_devices


def default_integration_selection(
    available_integrations: list[str],
    existing_integrations: set[str] | list[str],
) -> list[str]:
    """Return refinement-first integration defaults.

    A new concept defaults to every currently available integration so the
    user refines by deselecting rather than having to build a selection from
    scratch.  Reconfiguration preserves an existing explicit selection and
    does not silently re-enable integrations the user previously removed.
    """
    existing = {str(item) for item in existing_integrations}
    if existing:
        return sorted(existing)
    return sorted({str(item) for item in available_integrations})


def default_device_selection(
    available_device_ids: set[str],
    existing_selection: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """Return refinement-first device defaults for a domain concept.

    New selections default to all concrete integration-owned HA devices.
    Existing explicit device intent is preserved.  A previous all-matching /
    review-required scope is projected onto all currently visible concrete
    devices when possible, making the next action an explicit refinement.
    """
    devices = sorted(str(item) for item in available_device_ids)
    existing = existing_selection or {}
    mode = str(existing.get("device_filter_mode") or "")
    state = str(existing.get("selection_state") or "")
    selected = [
        str(item)
        for item in existing.get("selected_device_ids", [])
        if str(item) in available_device_ids
    ]

    if devices:
        if mode == "specific_devices" and selected and state != "review_required":
            return "specific_devices", sorted(set(selected))
        return "specific_devices", devices

    if mode == "all_matching":
        return "all_matching", []
    return "all_matching", []


def normalize_device_selection(
    mode: str,
    selected_devices: list[str] | None,
    available_device_ids: set[str],
    *,
    all_matching_token: str = "__all_matching__",
) -> tuple[list[str], str | None]:
    """Normalize one wizard device choice and fail closed on empty explicit selection.

    Returns ``(selected_device_ids, error_key)``.  ``all_matching`` is an
    explicit review-required choice.  ``specific_devices`` must contain at
    least one currently available device and never silently degrades to an
    empty configured selection.
    """
    if mode == "all_matching":
        return [all_matching_token], None
    if mode != "specific_devices":
        return [], "invalid_device_filter_mode"
    selected = [str(item) for item in (selected_devices or []) if str(item) in available_device_ids]
    if not selected:
        return [], "select_at_least_one_device"
    return sorted(set(selected)), None
