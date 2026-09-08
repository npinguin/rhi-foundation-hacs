"""Pure SelectedDomainBuildInput registry replacement and structural event delta."""
from __future__ import annotations

from typing import Any


def event_reason(refresh_reason: str) -> str:
    if refresh_reason in {"config_entry_updated", "options_updated", "configuration_saved"}:
        return "configured"
    if "provider" in refresh_reason or "publication" in refresh_reason:
        return "provider_changed"
    if "repair" in refresh_reason:
        return "repaired"
    return "refreshed"


def _structural_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return handoff content without diagnostic-only revision counters.

    Revision numbers are observability metadata, never synchronization
    primitives. A provider may re-register with a new publication revision
    without changing the actual DomainBuildSpecification or selected build
    input. Such a revision-only update must not wake a domain runtime.
    """
    result: list[dict[str, Any]] = []
    for raw in inputs:
        item = dict(raw)
        item.pop("configuration_revision", None)
        item.pop("candidate_revision", None)
        item.pop("build_input_revision", None)
        selection = dict(item.get("selection") or {})
        selection.pop("publication_revision", None)
        item["selection"] = selection
        result.append(item)
    return result


def replace_entry_slice(
    registry: dict[str, Any],
    *,
    entry_id: str,
    by_domain: dict[str, list[dict[str, Any]]],
    reason: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return next registry and structural events without mutating input."""
    previous_entries = {
        str(value.get("domain_id")): value
        for value in registry.values()
        if isinstance(value, dict)
        and value.get("foundation_entry_id") == entry_id
        and value.get("domain_id")
    }
    next_registry = {
        key: value
        for key, value in registry.items()
        if not (isinstance(value, dict) and value.get("foundation_entry_id") == entry_id)
    }
    events: list[dict[str, Any]] = []
    for domain, inputs in by_domain.items():
        configuration_revision = max([item["configuration_revision"] for item in inputs], default=1)
        build_input_revision = max([item["build_input_revision"] for item in inputs], default=configuration_revision)
        next_entry = {
            "foundation_entry_id": entry_id,
            "domain_id": domain,
            "inputs": inputs,
            "configuration_revision": configuration_revision,
        }
        next_registry[domain] = next_entry
        previous_entry = previous_entries.get(domain)
        structural_changed = (
            previous_entry is None
            or _structural_inputs(list(previous_entry.get("inputs", []) or []))
            != _structural_inputs(inputs)
        )
        if structural_changed:
            events.append({
                "foundation_entry_id": entry_id,
                "domain_id": domain,
                "configuration_revision": configuration_revision,
                "build_input_revision": build_input_revision,
                "reason": event_reason(reason),
            })
    for domain, previous in previous_entries.items():
        if domain not in by_domain:
            revision = max(1, int(previous.get("configuration_revision", 1)))
            events.append({
                "foundation_entry_id": entry_id,
                "domain_id": domain,
                "configuration_revision": revision,
                "build_input_revision": revision,
                "reason": "removed",
            })
    return next_registry, events
