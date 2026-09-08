"""Shared in-process DomainBuildSpecification provider registry API.

Domain packages may import these helpers without requiring the Foundation config
entry to be loaded. The shared registry survives either setup order inside the
Home Assistant process; Foundation imports existing providers on setup and
listens only to the structural changed event afterwards.
"""
from __future__ import annotations

from typing import Any, Iterator

from .const import (
    DOMAIN_BUILD_SPECIFICATION_REGISTRY,
    DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
)


def register_domain_build_specification_provider(
    hass: Any,
    *,
    publisher_domain: str,
    provider: Any,
    publication_revision: int | None = None,
) -> None:
    """Register or replace one bounded domain build-specification provider."""
    registry = hass.data.setdefault(DOMAIN_BUILD_SPECIFICATION_REGISTRY, {})
    previous = registry.get(publisher_domain)
    revision = int(
        publication_revision
        or getattr(provider, "publication_revision", None)
        or (
            previous.get("publication_revision", 0) + 1
            if isinstance(previous, dict)
            else 1
        )
    )
    revision = max(1, revision)
    registry[publisher_domain] = {
        "publisher_domain": publisher_domain,
        "publication_revision": revision,
        "provider": provider,
    }
    hass.bus.async_fire(
        DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
        {
            "publisher_domain": publisher_domain,
            "publication_revision": revision,
            "reason": "provider_updated" if previous is not None else "provider_registered",
        },
    )


def unregister_domain_build_specification_provider(
    hass: Any,
    *,
    publisher_domain: str,
) -> None:
    """Unregister a provider and publish one structural lifecycle event."""
    registry = hass.data.setdefault(DOMAIN_BUILD_SPECIFICATION_REGISTRY, {})
    previous = registry.pop(publisher_domain, None)
    if previous is None:
        return
    revision = (
        int(previous.get("publication_revision", 1))
        if isinstance(previous, dict)
        else int(getattr(previous, "publication_revision", 1))
    )
    hass.bus.async_fire(
        DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
        {
            "publisher_domain": publisher_domain,
            "publication_revision": max(1, revision),
            "reason": "provider_unregistered",
        },
    )


def iter_domain_build_specification_providers(
    hass: Any,
) -> Iterator[tuple[str, Any]]:
    """Iterate the shared authoritative registry without exposing mutation."""
    registry = hass.data.get(DOMAIN_BUILD_SPECIFICATION_REGISTRY, {}) or {}
    for key in sorted(registry):
        yield str(key), registry[key]


def get_selected_domain_build_input_entry(hass: Any, *, domain_id: str) -> dict[str, Any] | None:
    """Return one authoritative Foundation handoff registry entry without exposing mutation."""
    from .const import SELECTED_DOMAIN_BUILD_INPUT_REGISTRY
    entry = (hass.data.get(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {}) or {}).get(domain_id)
    return dict(entry) if isinstance(entry, dict) else None
