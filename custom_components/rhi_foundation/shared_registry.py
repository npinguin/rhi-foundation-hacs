"""Shared in-process provider registries for RHI cross-module contracts.

Domains may import these helpers without requiring the Foundation config entry to
be loaded. Foundation owns registry mechanics and supervision; domains own the
semantics and lifetime of the payloads they publish.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator

from .const import (
    DOMAIN_BUILD_SPECIFICATION_REGISTRY,
    DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_REGISTRY,
)

Unsubscribe = Callable[[], None]


def register_domain_build_specification_provider(
    hass: Any,
    *,
    publisher_domain: str,
    provider: Any,
    publication_revision: int | None = None,
) -> Unsubscribe:
    """Register or replace one bounded domain build-specification provider.

    Registration is a structural lifecycle signal, not a keep-alive. Re-registering
    the exact same provider at the exact same publication revision is therefore a
    no-op and must not wake Foundation discovery.

    The returned unsubscribe handle owns exactly this registration generation. An
    older provider instance can therefore never remove a newer registration during a
    reload race. Producer domains should bind this handle to their config-entry unload.
    """
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
    if (
        isinstance(previous, dict)
        and previous.get("provider") is provider
        and previous.get("publisher_domain") == publisher_domain
        and int(previous.get("publication_revision", 0) or 0) == revision
    ):
        token = previous.get("registration_token")
    else:
        token = object()
        registry[publisher_domain] = {
            "publisher_domain": publisher_domain,
            "publication_revision": revision,
            "provider": provider,
            "registration_token": token,
        }
        hass.bus.async_fire(
            DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
            {
                "publisher_domain": publisher_domain,
                "publication_revision": revision,
                "reason": "provider_updated" if previous is not None else "provider_registered",
            },
        )

    def _unsubscribe() -> None:
        current = registry.get(publisher_domain)
        if not isinstance(current, dict) or current.get("registration_token") is not token:
            return
        unregister_domain_build_specification_provider(
            hass,
            publisher_domain=publisher_domain,
            registration_token=token,
        )

    return _unsubscribe


def unregister_domain_build_specification_provider(
    hass: Any,
    *,
    publisher_domain: str,
    registration_token: object | None = None,
) -> None:
    """Unregister a provider and publish one structural lifecycle event.

    When ``registration_token`` is supplied, removal is generation-safe. The
    token-less form remains available for explicit administrative cleanup and legacy
    callers, but producer integrations should use the unsubscribe returned by
    ``register_domain_build_specification_provider``.
    """
    registry = hass.data.setdefault(DOMAIN_BUILD_SPECIFICATION_REGISTRY, {})
    current = registry.get(publisher_domain)
    if current is None:
        return
    if (
        registration_token is not None
        and isinstance(current, dict)
        and current.get("registration_token") is not registration_token
    ):
        return
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
) -> Unsubscribe:
    """Register one domain-owned supervisory provider and emit one lifecycle update.

    Supervision is intentionally not a telemetry/event stream. Registration or
    replacement is the only automatic refresh trigger in Baseline 1.8.1. Runtime
    source changes stay entirely inside the owning domain.

    The returned unsubscribe handle owns exactly this registration generation.
    """
    registry = hass.data.setdefault(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {})
    previous = registry.get(domain_id)
    if (
        isinstance(previous, dict)
        and previous.get("provider") is provider
        and previous.get("publisher_domain") == publisher_domain
    ):
        token = previous.get("registration_token")
    else:
        token = object()
        registry[domain_id] = {
            "domain_id": domain_id,
            "publisher_domain": publisher_domain,
            "provider": provider,
            "registration_token": token,
        }
        hass.bus.async_fire(
            DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
            {
                "domain_id": domain_id,
                "publisher_domain": publisher_domain,
                "reason": "provider_updated" if previous is not None else "provider_registered",
            },
        )

    def _unsubscribe() -> None:
        current = registry.get(domain_id)
        if not isinstance(current, dict) or current.get("registration_token") is not token:
            return
        unregister_domain_supervisory_status_provider(
            hass,
            domain_id=domain_id,
            publisher_domain=publisher_domain,
            registration_token=token,
        )

    return _unsubscribe


def notify_domain_supervisory_status_changed(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
    reason: str = "status_changed",
) -> bool:
    """Compatibility no-op for former runtime-driven supervision notifications.

    Baseline 1.8.1 deliberately forbids domain telemetry/runtime changes from waking
    Foundation. Foundation reads the registered provider once at registration/setup.
    A future periodic refresh, when introduced, is Foundation-owned and bounded.
    Returning ``False`` makes legacy callers harmless while domains migrate away from
    this helper.
    """
    return False


def unregister_domain_supervisory_status_provider(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
    registration_token: object | None = None,
) -> None:
    """Remove one domain supervisory provider and emit one lifecycle update."""
    registry = hass.data.setdefault(DOMAIN_SUPERVISORY_STATUS_REGISTRY, {})
    current = registry.get(domain_id)
    if current is None:
        return
    if (
        registration_token is not None
        and isinstance(current, dict)
        and current.get("registration_token") is not registration_token
    ):
        return
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


async def async_remove_domain_configuration(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
) -> bool:
    """Remove Foundation technical intent after a genuine producer config-entry removal.

    This is deliberately separate from provider unregister/unload. Producer domains
    own the knowledge that their Home Assistant config entry is actually being
    deleted; Foundation owns mutation of Foundation-persisted technical intent.
    """
    from .domain_lifecycle import async_remove_domain_configuration as _remove

    return await _remove(
        hass,
        domain_id=domain_id,
        publisher_domain=publisher_domain,
    )
