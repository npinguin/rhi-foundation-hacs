"""Explicit, read-only Foundation site inventory export.

This engineering snapshot is never built by the normal Foundation runtime refresh.
All candidate IDs and capability classes reuse Foundation source contracts.
Only allowlisted metadata leaves HA; physical/semantic authority stays with domains.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from ..classifier import classify_entity
from ..models.source_identity import EntitySourceIdentity, ServiceSourceIdentity, FrameworkResourceSourceIdentity
from .candidate_id import build_candidate_id

CONTRACT = "RHI_SITE_TECHNICAL_INVENTORY_V1"
MAX_ROWS = 50000
MAX_PAGE = 200
REQ = ("config_entries", "devices", "entities", "areas", "states")
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
EMAIL = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s]+")
SECRET_HINT = re.compile(r"(?i)(password|token|secret|credential|api[_ -]?key|ssid|private[_ -]?key|latitude|longitude|gps)")
INTERACTION_DOMAINS = frozenset({
    "light", "cover", "lock", "climate", "water_heater", "humidifier", "fan",
    "switch", "number", "select", "input_boolean", "input_button", "button",
    "media_player", "remote", "alarm_control_panel", "siren", "valve",
    "vacuum", "lawn_mower", "scene", "script", "automation",
})


def value(record: Any, key: str, default: Any = None) -> Any:
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def clean(raw: Any, limit: int = 140) -> str:
    """Strict display-only scrub; exported names still require site-private storage."""
    text = str(raw if raw is not None else "").replace("\r", " ").replace("\n", " ")[:limit]
    if SECRET_HINT.search(text):
        return "[redacted]"
    return URL.sub("[url]", EMAIL.sub("[email]", MAC.sub("[mac]", IP.sub("[ip]", text))))


def private_entity_reference(entity_id: str, registry_id: str | None) -> tuple[str, bool]:
    """Retain unique private references instead of collapsing secret-like IDs.

    The placeholder is deliberately NOT a valid HA entity_id. Domain generators
    must not auto-bind it without an explicit approved source mapping.
    """
    # Home Assistant entity IDs are registry source identifiers, NOT secret
    # values. A normal identifier such as sensor.access_token_expiry is
    # critical for Basic/Premium source-map joins and must not be blanked by a
    # free-text redaction heuristic. Only invalid/oversized source IDs are
    # replaced with a non-executable opaque alias.
    if len(entity_id) <= 255 and re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id):
        return entity_id, False
    fingerprint = hashlib.sha256(
        ("ha-entity:" + str(registry_id or entity_id)).encode("utf-8")
    ).hexdigest()[:24]
    return "private_entity:" + fingerprint, True


def optional_name(record: Any, field: str) -> str | None:
    val = value(record, field)
    return clean(val) if val is not None else None


def _identity_for_entity(ent: Any, entry_domain: str) -> EntitySourceIdentity | None:
    """Use EXACT Foundation candidate identity; do not make another ID algorithm."""
    entry_id, reg_id = value(ent, "config_entry_id"), value(ent, "id")
    if not entry_id or not reg_id:
        return None
    return EntitySourceIdentity(
        source_kind="entity", integration_domain=entry_domain,
        config_entry_id=str(entry_id), entity_registry_id=str(reg_id),
        unique_id=str(value(ent, "unique_id") or reg_id),
        current_entity_id=str(value(ent, "entity_id")),
        device_registry_id=value(ent, "device_id"),
    )


def build_site_inventory(source: dict[str, Any], *, captured_at: str | None = None) -> dict[str, Any]:
    """Pure domain-neutral projection; never changes HA or accepts a Basic/Full owner."""
    errors = {k: clean(v) for k, v in (source.get("errors") or {}).items()
              if k in (*REQ, "floors", "labels", "services", "framework_resources", "row_limit")}
    for key in REQ:
        if not isinstance(source.get(key), list):
            errors[key] = errors.get(key, "unavailable_required_source")
    entries = [x for x in source.get("config_entries", []) or [] if value(x, "entry_id")]
    by_entry = {str(value(x, "entry_id")): x for x in entries}
    devices = [x for x in source.get("devices", []) or [] if value(x, "id")]
    by_device = {str(value(x, "id")): x for x in devices}
    ents = [x for x in source.get("entities", []) or [] if value(x, "entity_id")]
    by_entity = {str(value(x, "entity_id")): x for x in ents}
    states = [x for x in source.get("states", []) or [] if value(x, "entity_id")]
    by_state = {str(value(x, "entity_id")): x for x in states}
    results: list[dict[str, Any]] = []
    entity_counts = Counter(str(value(x, "config_entry_id")) for x in ents if value(x, "config_entry_id"))
    # Native Home Assistant DeviceEntry.config_entries is a set, and one
    # device can belong to multiple integrations. Counting a legacy
    # config_entry_id alone silently declares native devices unaccounted for.
    device_entry_map: dict[str, list[str]] = {}
    device_counts: Counter[str] = Counter()
    for device in devices:
        entry_ids = value(device, "config_entries")
        if entry_ids is None:
            fallback = value(device, "config_entry_id")
            entry_ids = [fallback] if fallback else []
        if not isinstance(entry_ids, (list, tuple, set, frozenset)):
            entry_ids = [entry_ids]
        associated = sorted({str(x) for x in entry_ids if x})
        device_entry_map[str(value(device, "id"))] = associated
        device_counts.update(associated)
    device_entity_counts = Counter(str(value(x, "device_id")) for x in ents if value(x, "device_id"))

    for e in entries:
        eid = str(value(e, "entry_id"))
        results.append({
            "kind": "integration_entry", "key": eid,
            "integration_domain": clean(value(e, "domain") or "", 100),
            "title": optional_name(e, "title"),
            "state": clean(value(e, "state") or "unknown", 60),
            "labels": sorted(str(x) for x in (value(e, "labels") or [])),
            "disabled_by": optional_name(e, "disabled_by"),
            "entity_count": entity_counts[eid],
            "device_count": device_counts[eid],
        })
    for d in devices:
        did = str(value(d, "id"))
        entry_ids = device_entry_map.get(did, [])
        results.append({
            "kind": "device", "key": did,
            "device_registry_id": did, "config_entry_ids": entry_ids,
            "via_device_registry_id": value(d, "via_device_id"),
            "area_id": value(d, "area_id"),
            "labels": sorted(str(x) for x in (value(d, "labels") or [])),
            "name": clean(value(d, "name_by_user") or value(d, "name")),
            "manufacturer": optional_name(d, "manufacturer"), "model": optional_name(d, "model"),
            "identifiers_present": bool(value(d, "identifiers")),
            "connections_present": bool(value(d, "connections")),
            "entity_count": device_entity_counts[did],
        })
    for eid in sorted(set(by_entity) | set(by_state)):
        e, state = by_entity.get(eid), by_state.get(eid)
        attrs = value(state, "attributes", {}) or {}
        entry_id = value(e, "config_entry_id")
        entry = by_entry.get(str(entry_id))
        integration_domain = str(value(entry, "domain") or value(e, "platform") or "")
        domain = eid.split(".", 1)[0]
        device_class = value(attrs, "device_class") or value(e, "device_class") or ""
        state_class = value(attrs, "state_class") or value(e, "state_class") or ""
        unit = value(attrs, "unit_of_measurement") or value(e, "unit_of_measurement") or ""
        caps = classify_entity(entity_domain=domain, device_class=device_class,
                               state_class=state_class, unit=unit)
        source_identity = _identity_for_entity(e, integration_domain) if e else None
        disabled = bool(value(e, "disabled_by"))
        candidates = ([{"capability_class": c, "candidate_id": build_candidate_id(source_identity, c)}
                       for c in caps] if source_identity and not disabled else [])
        state_value = value(state, "state")
        availability = ("missing" if state is None else "temporarily_unavailable"
                        if state_value in ("unknown", "unavailable") else "available")
        device = by_device.get(str(value(e, "device_id")))
        safe_entity_id, redacted_entity_id = private_entity_reference(eid, value(e, "id"))
        feature_bits = value(attrs, "supported_features")
        if type(feature_bits) is not int:
            feature_bits = None
        results.append({
            "kind": "entity", "key": safe_entity_id, "current_entity_id": safe_entity_id,
            "entity_id_redacted": redacted_entity_id,
            "entity_registry_id": value(e, "id"), "config_entry_id": entry_id,
            "integration_domain": clean(integration_domain, 100),
            "device_registry_id": value(e, "device_id"), "area_id":
                value(e, "area_id") or value(device, "area_id"),
            "labels": sorted(str(x) for x in (value(e, "labels") or [])),
            "disabled_by": optional_name(e, "disabled_by"),
            "hidden_by": optional_name(e, "hidden_by"),
            "name": clean(value(e, "name") or value(e, "original_name") or "", 140),
            # V1 identity_quality enum is backward-compatible; the new
            # registry_provenance field exposes the missing distinction without
            # breaking existing offline Basic consumers.
            "identity_quality": "FOUNDATION_REGISTRY" if source_identity else "UNSTABLE_OR_UNATTRIBUTED",
            "registry_provenance": (
                "FOUNDATION_CONFIG_ENTRY" if source_identity else
                "REGISTRY_NO_CONFIG_ENTRY" if value(e, "id") else "STATE_ONLY"
            ),
            "availability_at_capture": availability, "device_class": clean(device_class, 80),
            "state_class": clean(state_class, 80), "native_unit": clean(unit, 40),
            "supported_features": feature_bits, "technical_capabilities": caps,
            "candidate_refs": candidates if not redacted_entity_id else [],
            "technical_interaction_surface": domain in INTERACTION_DOMAINS,
            "semantic_owner": "UNASSIGNED_BY_FOUNDATION",
            "physical_writer": "NOT_AUTHORIZED_BY_DISCOVERY",
        })
    for kind, key, label in (("areas", "area_id", "area"), ("floors", "floor_id", "floor"),
                              ("labels", "label_id", "label")):
        for item in source.get(kind, []) or []:
            # Native HA registry entries typically expose `id` rather than
            # the relationship field (`area_id` / `label_id`).
            identifier = value(item, key) or value(item, "id")
            if not identifier:
                continue
            row = {"kind": label, "key": str(identifier), key: identifier,
                   "name": clean(value(item, "name"))}
            if kind == "areas":
                row["floor_id"] = value(item, "floor_id")
            row["labels"] = sorted(str(x) for x in (value(item, "labels") or []))
            results.append(row)
    services = source.get("services")
    if isinstance(services, dict):
        for sd, methods in services.items():
            if not isinstance(methods, dict):
                continue
            for sm in methods:
                sid = ServiceSourceIdentity(
                    source_kind="service", integration_domain=str(sd),
                    service_domain=str(sd), service_name=str(sm),
                    target={}, target_scope="none",
                )
                results.append({
                    "kind": "service_surface", "key": clean(str(sd) + "." + str(sm), 255),
                    "service_domain": clean(sd, 100), "service_name": clean(sm, 100),
                    "candidate_id": build_candidate_id(sid, "service_command_surface"),
                    "target_scope": "UNKNOWN_GLOBAL_SERVICE",
                    "semantic_owner": "UNASSIGNED_BY_FOUNDATION",
                    "physical_writer": "NOT_AUTHORIZED_BY_DISCOVERY",
                })
    elif services is None:
        errors.setdefault("services", "optional_service_catalog_unavailable")
    # Framework provider output is optional technical evidence; it does not
    # create selected domain build inputs or call an integration action.
    for raw in source.get("framework_resources", []) or []:
        try:
            identity = FrameworkResourceSourceIdentity(
                source_kind="framework_resource",
                integration_domain=str(raw["integration_domain"]),
                framework_domain=str(raw["framework_domain"]),
                resource_type=str(raw["resource_type"]),
                resource_id=str(raw["resource_id"]),
                capability_key=str(raw["capability_key"]),
            )
            cap = str(raw["capability_class"])
            results.append({
                "kind": "framework_capability",
                "key": build_candidate_id(identity, cap),
                "integration_domain": clean(identity.integration_domain),
                "framework_domain": clean(identity.framework_domain),
                "resource_type": clean(identity.resource_type),
                "resource_id": clean(identity.resource_id, 255),
                "capability_key": clean(identity.capability_key),
                "capability_class": clean(cap),
                "candidate_id": build_candidate_id(identity, cap),
                "writable_surface": bool(raw.get("writable", False)),
                "semantic_owner": "UNASSIGNED_BY_FOUNDATION",
                "physical_writer": "NOT_AUTHORIZED_BY_DISCOVERY",
            })
        except (KeyError, TypeError, ValueError):
            errors["framework_resources"] = "partial_invalid_provider_evidence"
    if source.get("framework_resources") is None:
        errors.setdefault("framework_resources", "optional_provider_catalog_unavailable")
    for field in ("floors", "labels"):
        if source.get(field) is None:
            errors.setdefault(field, "optional_registry_unavailable")

    # A successful registry read is not proof of a correct projection. Rooms
    # referenced by devices/entities must resolve to an exported area.
    exported_areas = {r["area_id"] for r in results if r["kind"] == "area"}
    used_areas = {str(r["area_id"]) for r in results
                  if r["kind"] in ("device", "entity") and r.get("area_id")}
    unresolved = used_areas - exported_areas
    if unresolved:
        errors["areas"] = f"unresolved_area_references:{len(unresolved)}"
    # Label relationships are technical inventory evidence too; missing
    # definitions can silently break site segmentation and Basic dashboards.
    exported_labels = {r["label_id"] for r in results if r["kind"] == "label"}
    used_labels = {label for r in results if r["kind"] in
                   ("integration_entry", "device", "entity", "area")
                   for label in r.get("labels", [])}
    unresolved_labels = used_labels - exported_labels
    if unresolved_labels:
        errors["labels"] = f"unresolved_label_references:{len(unresolved_labels)}"
    # Redaction must not produce duplicate downstream technical join keys.
    seen: set[tuple[str, str]] = set()
    for row in results:
        identity = (str(row["kind"]), str(row["key"]))
        if identity in seen:
            errors["row_identity"] = "duplicate_export_row_keys"
            break
        seen.add(identity)
    results.sort(key=lambda row: (row["kind"], str(row["key"])))
    total = len(results)
    if total > MAX_ROWS:
        errors["row_limit"] = f"site_export_row_cap_exceeded:{total}>{MAX_ROWS}"
        results = results[:MAX_ROWS]
    timestamp = captured_at or datetime.now(timezone.utc).isoformat()
    entity_rows = [row for row in results if row["kind"] == "entity"]
    coverage = {
        "entities_by_availability": {
            state: sum(row["availability_at_capture"] == state for row in entity_rows)
            for state in ("available", "temporarily_unavailable", "missing")
        },
        "entities_by_registry_provenance": {
            state: sum(row["registry_provenance"] == state for row in entity_rows)
            for state in ("FOUNDATION_CONFIG_ENTRY", "REGISTRY_NO_CONFIG_ENTRY", "STATE_ONLY")
        },
        "disabled_entities": sum(bool(row["disabled_by"]) for row in entity_rows),
        "entity_aliases": sum(bool(row["entity_id_redacted"]) for row in entity_rows),
        "unresolved_area_references": len(unresolved),
        "unresolved_label_references": len(unresolved_labels),
        "zero_entity_config_entries": sum(row["entity_count"] == 0 for row in results
                                         if row["kind"] == "integration_entry"),
        "zero_entity_devices": sum(row["entity_count"] == 0 for row in results
                                   if row["kind"] == "device"),
    }
    rows_encoded = json.dumps(results, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(rows_encoded.encode("utf-8")).hexdigest()
    return {
        "contract_id": CONTRACT, "schema_version": 1,
        "generated_at": timestamp, "ha_version": clean(source.get("ha_version")),
        "status": "COMPLETE_TECHNICAL" if not any(k in errors for k in (*REQ, "row_identity")) and not unresolved_labels and total <= MAX_ROWS else "PARTIAL_HOLD",
        "source_errors": dict(sorted(errors.items())),
        "source_completeness": {k: k not in errors for k in REQ},
        "row_count": len(results), "actual_row_count": total,
        "coverage": coverage,
        "scope": {
            "complete_claim": "GENERIC_HA_REGISTRIES_AND_KNOWN_TECHNICAL_SURFACES",
            "not_collected": [
                "integration_private_api_endpoints",
                "device_automation_action_schemas",
                "dashboard_storage_views",
                "recorder_history",
                "physical_command_validation",
            ],
            "meaning": "Technical structure only; not domain-feature or physical acceptance",
        },
        "fingerprint": digest, "snapshot_id": hashlib.sha256(
            (digest + "/" + timestamp).encode("utf-8")).hexdigest()[:24],
        "rows": results,
        "safety": {
            "creates_binding": False, "creates_runtime_truth": False,
            "executes_commands": False, "assigns_semantic_owner": False,
        },
    }


def site_inventory_page(export: dict[str, Any], *, offset: int, limit: int,
                        snapshot_id: str) -> dict[str, Any]:
    if snapshot_id != export["snapshot_id"]:
        raise ValueError("site_inventory_snapshot_identity_mismatch")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("site_inventory_invalid_offset")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE:
        raise ValueError("site_inventory_invalid_page_limit")
    rows = export["rows"]
    next_offset = offset + limit if offset + limit < len(rows) else None
    return {k: v for k, v in export.items() if k != "rows"} | {
        "offset": offset, "limit": limit,
        "entries": rows[offset:offset + limit], "next_offset": next_offset,
    }


def capture_native_site(hass: Any) -> dict[str, Any]:
    """Explicit HA-native registry capture; no runtime scan, persistence or writes."""
    from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

    errors: dict[str, str] = {}
    def collect(key: str, getter: Any, *, optional: bool = False) -> Any:
        try:
            return getter()
        except (AttributeError, ImportError, RuntimeError, ValueError) as exc:
            errors[key] = f"{type(exc).__name__}:unavailable"
            return None

    entries = collect("config_entries", lambda: list(hass.config_entries.async_entries()))
    devices = collect("devices", lambda: list(dr.async_get(hass).devices.values()))
    entities = collect("entities", lambda: list(er.async_get(hass).entities.values()))
    areas = collect("areas", lambda: list(ar.async_get(hass).areas.values()))
    states = collect("states", lambda: list(hass.states.async_all()))
    try:
        from homeassistant.helpers import floor_registry as fr
        floors = collect("floors", lambda: list(fr.async_get(hass).floors.values()), optional=True)
    except ImportError:
        floors = None
        errors["floors"] = "optional_registry_unavailable"
    try:
        from homeassistant.helpers import label_registry as lr
        labels = collect("labels", lambda: list(lr.async_get(hass).labels.values()), optional=True)
    except ImportError:
        labels = None
        errors["labels"] = "optional_registry_unavailable"
    services = collect("services", lambda: hass.services.async_services(), optional=True)
    framework_resources = []
    try:
        from ..shared_registry import iter_framework_resource_providers
        for integration_domain, provider in iter_framework_resource_providers(hass):
            getter = getattr(provider, "get_framework_resources", None)
            if not callable(getter):
                errors["framework_resources"] = "provider_without_read_contract"
                continue
            resources = getter()
            if not isinstance(resources, (list, tuple)):
                errors["framework_resources"] = "provider_invalid_resource_list"
                continue
            for resource in resources[:MAX_ROWS]:
                if not isinstance(resource, dict):
                    continue
                for capability in (resource.get("capabilities") or [])[:50]:
                    if not isinstance(capability, dict):
                        continue
                    framework_resources.append({
                        "integration_domain": str(integration_domain),
                        "framework_domain": str(getattr(provider, "framework_domain", "") or ""),
                        "resource_type": resource.get("resource_type"),
                        "resource_id": resource.get("resource_id"),
                        "capability_key": capability.get("capability_key"),
                        "capability_class": capability.get("capability_class"),
                        "writable": capability.get("writable", False),
                    })
            if len(resources) > MAX_ROWS:
                errors["framework_resources"] = "provider_resource_limit_exceeded"
    except (ImportError, AttributeError, RuntimeError, ValueError, TypeError):
        errors["framework_resources"] = "optional_provider_inventory_unavailable"
    try:
        from homeassistant.const import __version__ as ha_version
    except ImportError:
        ha_version = "UNKNOWN"
    return {
        "config_entries": entries, "devices": devices, "entities": entities,
        "areas": areas, "states": states, "floors": floors, "labels": labels,
        "services": services, "framework_resources": framework_resources,
        "errors": errors, "ha_version": ha_version,
    }
