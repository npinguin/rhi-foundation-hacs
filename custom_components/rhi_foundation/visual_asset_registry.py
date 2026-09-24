"""Foundation-owned registry for cross-domain visual asset identity.

The registry owns mechanics, validation and the canonical visual_ref key space.
Domains own which visual_ref is assigned to their semantic assets.
UX packages own local image files and rendering.

No file paths or URLs are published here. Consumers resolve visual_ref against
their own packaged visual catalog so no UX package becomes a runtime dependency
of another.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any, Callable, Iterator

from .const import (
    VISUAL_ASSET_REGISTRY,
    VISUAL_ASSET_REGISTRY_CHANGED_EVENT,
    VISUAL_ASSET_REGISTRY_CONTRACT,
)

Unsubscribe = Callable[[], None]
_MAX_VISUAL_ASSETS_PER_PROVIDER = 256
_ALLOWED_VARIANTS = {"thumbnail", "card", "hero", "detail"}


def _materialize(provider: Any) -> list[dict[str, Any]]:
    if hasattr(provider, "get_visual_assets"):
        payload = provider.get_visual_assets()
    elif hasattr(provider, "visual_assets"):
        payload = provider.visual_assets
    elif callable(provider):
        payload = provider()
    else:
        raise ValueError("provider_has_no_visual_asset_surface")
    if inspect.isawaitable(payload):
        raise ValueError("visual_asset_provider_must_be_synchronous_and_bounded")
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, (list, tuple)):
        raise ValueError("visual_asset_provider_result_must_be_sequence")
    if len(payload) > _MAX_VISUAL_ASSETS_PER_PROVIDER:
        raise ValueError("visual_asset_provider_exceeds_bounded_limit")
    result: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("visual_asset_entry_must_be_object")
        result.append(dict(item))
    return result


def _valid_visual_ref(value: Any) -> bool:
    ref = str(value or "").strip()
    if not ref or len(ref) > 128 or ref.startswith(".") or ref.endswith("."):
        return False
    parts = ref.split(".")
    return len(parts) >= 2 and all(
        part and all(ch.islower() or ch.isdigit() or ch in {"_", "-"} for ch in part)
        for part in parts
    )


def validate_visual_asset_entry(
    entry: dict[str, Any],
    *,
    publisher_domain: str,
) -> list[str]:
    """Validate one visual identity record without taking over domain semantics."""
    issues: list[str] = []
    visual_ref = str(entry.get("visual_ref") or "")
    asset_type = str(entry.get("asset_type") or "")
    owner_domain = str(entry.get("owner_domain") or "")
    revision = entry.get("revision")
    variants = entry.get("variant_keys", [])

    if not _valid_visual_ref(visual_ref):
        issues.append("invalid:visual_ref")
    if not asset_type:
        issues.append("invalid:asset_type")
    if owner_domain != publisher_domain:
        issues.append("invalid:owner_domain")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        issues.append("invalid:revision")
    if not isinstance(variants, list):
        issues.append("invalid:variant_keys")
    else:
        clean_variants = [str(value) for value in variants]
        if len(clean_variants) != len(set(clean_variants)):
            issues.append("duplicate:variant_key")
        if any(value not in _ALLOWED_VARIANTS for value in clean_variants):
            issues.append("invalid:variant_key")

    # The central registry deliberately never publishes package paths or URLs.
    for forbidden in ("path", "url", "image_url", "asset_path", "filename", "file"):
        if forbidden in entry:
            issues.append(f"forbidden:{forbidden}")
    return issues


def register_visual_asset_catalog_provider(
    hass: Any,
    *,
    publisher_domain: str,
    provider: Any,
    publication_revision: int | None = None,
) -> Unsubscribe:
    """Register one domain-owned bounded visual catalog provider.

    Foundation owns registry mechanics and key validation. The provider domain owns
    the meaning of its entries and the assignment of visual_ref to semantic assets.
    The returned unsubscribe is generation-safe across reload races.
    """
    registry = hass.data.setdefault(VISUAL_ASSET_REGISTRY, {})
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
            VISUAL_ASSET_REGISTRY_CHANGED_EVENT,
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
        unregister_visual_asset_catalog_provider(
            hass,
            publisher_domain=publisher_domain,
            registration_token=token,
        )

    return _unsubscribe


def unregister_visual_asset_catalog_provider(
    hass: Any,
    *,
    publisher_domain: str,
    registration_token: object | None = None,
) -> None:
    registry = hass.data.setdefault(VISUAL_ASSET_REGISTRY, {})
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
    hass.bus.async_fire(
        VISUAL_ASSET_REGISTRY_CHANGED_EVENT,
        {
            "publisher_domain": publisher_domain,
            "publication_revision": int(previous.get("publication_revision", 1) or 1),
            "reason": "provider_unregistered",
        },
    )


def iter_visual_asset_catalog_providers(hass: Any) -> Iterator[tuple[str, Any]]:
    registry = hass.data.get(VISUAL_ASSET_REGISTRY, {}) or {}
    for key in sorted(registry):
        yield str(key), registry[key]


def visual_asset_registry_snapshot(hass: Any) -> dict[str, Any]:
    """Return one bounded immutable-style public registry snapshot."""
    entries: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []
    owners: dict[str, str] = {}
    issues: list[str] = []

    for registry_key, registration in iter_visual_asset_catalog_providers(hass):
        publisher = str(
            registration.get("publisher_domain", registry_key)
            if isinstance(registration, dict)
            else registry_key
        )
        revision = int(
            registration.get("publication_revision", 1)
            if isinstance(registration, dict)
            else 1
        )
        provider = registration.get("provider") if isinstance(registration, dict) else registration
        provider_issues: list[str] = []
        count = 0
        try:
            materialized = _materialize(provider)
            count = len(materialized)
            for raw in materialized:
                row_issues = validate_visual_asset_entry(raw, publisher_domain=publisher)
                visual_ref = str(raw.get("visual_ref") or "")
                if visual_ref and visual_ref in owners and owners[visual_ref] != publisher:
                    row_issues.append("duplicate:visual_ref_global")
                if row_issues:
                    provider_issues.extend(f"{visual_ref or 'missing'}:{issue}" for issue in row_issues)
                    continue
                owners[visual_ref] = publisher
                entries.append(
                    {
                        "visual_ref": visual_ref,
                        "asset_type": str(raw["asset_type"]),
                        "owner_domain": publisher,
                        "revision": int(raw["revision"]),
                        "variant_keys": sorted({str(value) for value in raw.get("variant_keys", [])}),
                    }
                )
        except Exception as exc:
            provider_issues.append(f"provider_error:{type(exc).__name__}:{exc}")
        providers.append(
            {
                "publisher_domain": publisher,
                "publication_revision": revision,
                "entry_count": count,
                "status": "valid" if not provider_issues else "invalid",
                "issues": sorted(set(provider_issues)),
            }
        )
        issues.extend(f"{publisher}:{issue}" for issue in provider_issues)

    entries.sort(key=lambda row: row["visual_ref"])
    providers.sort(key=lambda row: row["publisher_domain"])
    return {
        "contract_id": VISUAL_ASSET_REGISTRY_CONTRACT,
        "contract_version": "1.0.0",
        "ownership": {
            "registry": "rhi_foundation",
            "assignment": "owning_domain",
            "packaged_asset": "consuming_ux",
            "rendering": "consuming_ux",
        },
        "path_or_url_publication": False,
        "entries": deepcopy(entries),
        "providers": deepcopy(providers),
        "entry_count": len(entries),
        "provider_count": len(providers),
        "status": "valid" if not issues else "degraded",
        "issues": sorted(set(issues)),
    }
