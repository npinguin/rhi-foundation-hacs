"""Shared registry mechanics for model-derived, runtime-discoverable publications.

Foundation owns *transport and validation*, never Energy/Mobility semantics.
Each domain is the sole producer of its compiled publication inventory.
No HA entity scans, hidden compatibility aliases or per-telemetry rebuilds.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable

INVENTORY_CONTRACT = "RHI_DOMAIN_PUBLICATION_INVENTORY_V1"
REGISTRY_KEY = "rhi_domain_publication_inventory_registry"
CHANGED_EVENT = "rhi_domain_publication_inventory_changed"
MAX_PAGE_SIZE = 100


def _validated(domain: str, provider: Any) -> dict[str, Any]:
    getter = getattr(provider, "get_publication_inventory", None)
    if not callable(getter):
        raise ValueError("publication_inventory_provider_missing")
    data = getter()
    if not isinstance(data, dict) or data.get("contract_id") != INVENTORY_CONTRACT:
        raise ValueError("publication_inventory_contract_invalid")
    if data.get("domain_id") != domain:
        raise ValueError("publication_inventory_domain_mismatch")
    if not data.get("model_version") or not data.get("release"):
        raise ValueError("publication_inventory_identity_missing")
    entries = data.get("entries")
    if not isinstance(entries, (list, tuple)):
        raise ValueError("publication_inventory_entries_invalid")
    ids: set[str] = set()
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("publication_inventory_entry_invalid")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
            raise ValueError("publication_inventory_duplicate_or_missing_id")
        if not entry.get("kind") or not entry.get("producer") or not entry.get("authority"):
            raise ValueError("publication_inventory_entry_incomplete:" + entry_id)
        if str(entry.get("publication_status") or "") not in {
            "PUBLISHED", "DECLARED_NOT_PUBLISHED", "CONDITIONAL", "UNAVAILABLE"
        }:
            raise ValueError("publication_inventory_entry_status_invalid:" + entry_id)
        ids.add(entry_id)
        rows.append(dict(entry))
    rows.sort(key=lambda item: item["id"])
    return {
        "contract_id": INVENTORY_CONTRACT,
        "domain_id": domain,
        "model_version": str(data["model_version"]),
        "release": str(data["release"]),
        "entries": rows,
    }


def register_domain_publication_inventory_provider(
    hass: Any, *, publisher_domain: str, provider: Any,
) -> Callable[[], None]:
    """Generation-safe, fail-fast structural registration, never mutable truth."""
    _validated(publisher_domain, provider)
    registry = hass.data.setdefault(REGISTRY_KEY, {})
    existing = registry.get(publisher_domain)
    # A new registration is a new lifecycle generation even when the same
    # provider object is reused. Otherwise the first unload callback can
    # unregister the newer registration by sharing its token.
    same_provider = isinstance(existing, dict) and existing["provider"] is provider
    token = object()
    registry[publisher_domain] = {"provider": provider, "token": token}
    if not same_provider:
        hass.bus.async_fire(CHANGED_EVENT, {
            "publisher_domain": publisher_domain,
            "reason": "provider_updated" if existing else "provider_registered",
        })

    def unsubscribe() -> None:
        row = registry.get(publisher_domain)
        if row is not None and row["token"] is token:
            registry.pop(publisher_domain, None)
            hass.bus.async_fire(CHANGED_EVENT, {
                "publisher_domain": publisher_domain, "reason": "provider_unregistered",
            })
    return unsubscribe


def publication_inventory_summary(hass: Any) -> dict[str, Any]:
    registry = hass.data.get(REGISTRY_KEY, {}) or {}
    summaries = []
    for domain in sorted(registry):
        payload = _validated(domain, registry[domain]["provider"])
        counts = {status: 0 for status in (
            "PUBLISHED", "DECLARED_NOT_PUBLISHED", "CONDITIONAL", "UNAVAILABLE",
        )}
        for entry in payload["entries"]:
            counts[entry["publication_status"]] += 1
        summaries.append({
            "domain_id": domain, "model_version": payload["model_version"],
            "release": payload["release"], "total_entries": len(payload["entries"]),
            "publication_status_counts": counts,
        })
    return {
        "contract_id": INVENTORY_CONTRACT,
        "service": "rhi_foundation.get_publication_inventory",
        "domains": summaries,
    }


def get_domain_publication_inventory(
    hass: Any, *, domain_id: str, offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("publication_inventory_offset_invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("publication_inventory_limit_invalid")
    registry = hass.data.get(REGISTRY_KEY, {}) or {}
    registration = registry.get(domain_id)
    if registration is None:
        return {
            "contract_id": INVENTORY_CONTRACT, "domain_id": domain_id,
            "status": "UNAVAILABLE", "reason": "domain_inventory_not_registered",
            "total_entries": 0, "offset": offset, "entries": [], "next_offset": None,
        }
    data = _validated(domain_id, registration["provider"])
    entries = data.pop("entries")
    # Stable hash of full canonical publication declarations; quality/telemetry
    # cannot mutate the contract fingerprint or cause churn.
    fingerprint = sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    page = entries[offset:offset + limit]
    return {
        **data, "status": "AVAILABLE", "fingerprint": fingerprint,
        "total_entries": len(entries), "offset": offset, "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(entries) else None,
        "entries": page,
    }


class FoundationPublicationInventoryProvider:
    """Foundation's explicit framework/technical contracts, not domain semantics."""
    def __init__(self, release: str):
        self.release = release

    def get_publication_inventory(self) -> dict[str, Any]:
        publications = [
            ("foundation.health", "health", "FoundationHealthSensor"),
            ("foundation.configuration", "configuration", "FoundationConfigurationSensor"),
            ("foundation.submodules", "discovery", "FoundationSubmodulesSensor"),
            ("foundation.visual_assets", "visual_catalog", "FoundationVisualAssetRegistrySensor"),
            ("foundation.domain_build_specifications", "discovery", "shared_registry.register_domain_build_specification_provider"),
            ("foundation.selected_build_inputs", "configuration", "shared_registry.get_selected_domain_build_input_entry"),
            ("foundation.supervision", "health", "shared_registry.register_domain_supervisory_status_provider"),
            ("foundation.framework_resources", "discovery", "shared_registry.register_framework_resource_provider"),
            ("foundation.publication_inventory", "contract_inventory", "publication_inventory.get_domain_publication_inventory"),
        ]
        return {
            "contract_id": INVENTORY_CONTRACT, "domain_id": "rhi_foundation",
            "model_version": "foundation-framework", "release": self.release,
            "entries": [
                {
                    "id": key, "kind": kind, "producer": producer,
                    "authority": "rhi_foundation",
                    "publication_status": "PUBLISHED",
                    "transport": (
                        "HA_SENSOR" if "Sensor" in producer
                        else "FOUNDATION_REGISTRY" if producer.startswith("shared_registry")
                        else "HA_RESPONSE_SERVICE"
                    ),
                }
                for key, kind, producer in publications
            ],
        }
