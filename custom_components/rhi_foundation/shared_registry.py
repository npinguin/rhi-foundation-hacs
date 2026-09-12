"""Shared in-process provider registries for RHI cross-module contracts.

Domains may import these helpers without requiring the Foundation config entry to
be loaded. Foundation owns registry mechanics and supervision; domains own the
semantics of the payloads they publish.
"""
from __future__ import annotations

from typing import Any, Iterator

from .const import (
    DOMAIN_BUILD_SPECIFICATION_REGISTRY,
    DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_REGISTRY,
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


def register_domain_supervisory_status_provider(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
    provider: Any,
) -> None:
    """Register one domain-owned RHI_DOMAIN_SUPERVISORY_STATUS_V1 provider."""
    registry = hass.data.setdefault(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {})
    previous = registry.get(domain_id)
    registry[domain_id] = {
        "domain_id": domain_id,
        "publisher_domain": publisher_domain,
        "provider": provider,
    }
    hass.bus.async_fire(
        DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
        {
            "domain_id": domain_id,
            "publisher_domain": publisher_domain,
            "reason": "provider_updated" if previous is not None else "provider_registered",
        },
    )


def notify_domain_supervisory_status_changed(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
    reason: str = "status_changed",
) -> None:
    """Signal that a registered provider now returns a different status snapshot."""
    if domain_id not in (hass.data.get(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {}) or {}):
        return
    hass.bus.async_fire(
        DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
        {
            "domain_id": domain_id,
            "publisher_domain": publisher_domain,
            "reason": reason,
        },
    )


def unregister_domain_supervisory_status_provider(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
) -> None:
    """Remove one domain supervisory provider without transferring ownership."""
    registry = hass.data.setdefault(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {})
    if registry.pop(domain_id, None) is None:
        return
    hass.bus.async_fire(
        DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
        {
            "domain_id": domain_id,
            "publisher_domain": publisher_domain,
            "reason": "provider_unregistered",
        },
    )


def iter_domain_supervisory_status_providers(
    hass: Any,
) -> Iterator[tuple[str, Any]]:
    """Iterate domain supervisory providers without exposing registry mutation."""
    registry = hass.data.get(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {}) or {}
    for key in sorted(registry):
        yield str(key), registry[key]


def get_selected_domain_build_input_entry(hass: Any, *, domain_id: str) -> dict[str, Any] | None:
    """Return one authoritative Foundation handoff registry entry without exposing mutation."""
    from .const import SELECTED_DOMAIN_BUILD_INPUT_REGISTRY
    entry = (hass.data.get(SELECTED_DOMAIN_BUILD_INPUT_REGISTRY, {}) or {}).get(domain_id)
    return dict(entry) if isinstance(entry, dict) else None
