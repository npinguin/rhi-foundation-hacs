"""Robotix Home Intelligence - Foundation Module."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_CONFIGURATION_REVISION,
    CONF_CONCEPT_MAPPINGS,
    CONF_DEVELOPER_MODE,
    CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS,
    CONF_TECHNICAL_SELECTIONS,
    DOMAIN,
    DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
    DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
    FOUNDATION_DISPATCH_SIGNAL,
    FOUNDATION_REFRESH_SERVICE,
    PLATFORMS,
)
from .runtime import async_refresh_snapshot, async_refresh_supervision_snapshot, remove_published_inputs
from .wizard_state import canonicalize_multi_mapping_shape

CONFIG_ENTRY_VERSION = 5
_MAX_COALESCED_REASONS = 8


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate F1.1.x configuration without silently inventing mappings."""
    if entry.version >= CONFIG_ENTRY_VERSION:
        return True

    def _migrate_payload(payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload.setdefault(CONF_DEVELOPER_MODE, False)
        payload.setdefault(CONF_SELECTED_INTEGRATIONS, [])
        payload.setdefault(CONF_CONCEPT_MAPPINGS, {})
        payload.setdefault(CONF_TECHNICAL_SELECTIONS, [])
        payload.setdefault(CONF_DEVICE_SELECTIONS, {})
        payload.setdefault(CONF_CONFIGURATION_REVISION, 1)
        mappings = dict(payload.get(CONF_CONCEPT_MAPPINGS, {}) or {})
        for mapping in mappings.values():
            if not isinstance(mapping, dict):
                continue
            if not mapping.get("builder_id"):
                builder_ids = list(mapping.get("builder_ids", []) or [])
                if len(builder_ids) == 1:
                    mapping["builder_id"] = builder_ids[0]
            mapping.pop("builder_ids", None)
            mapping.pop("contract_kinds", None)
        device_selections = dict(payload.get(CONF_DEVICE_SELECTIONS, {}) or {})
        mappings, device_selections = canonicalize_multi_mapping_shape(mappings, device_selections)
        payload[CONF_CONCEPT_MAPPINGS] = mappings
        payload[CONF_DEVICE_SELECTIONS] = device_selections
        mapped = {
            str(mapping.get("integration_domain"))
            for mapping in mappings.values()
            if isinstance(mapping, dict) and mapping.get("integration_domain")
        }
        technical = set(payload.get(CONF_TECHNICAL_SELECTIONS, []) or [])
        technical.update(set(payload.get(CONF_SELECTED_INTEGRATIONS, []) or []) - mapped)
        payload[CONF_TECHNICAL_SELECTIONS] = sorted(technical)
        return payload

    data = _migrate_payload(dict(entry.data))
    options = _migrate_payload(dict(entry.options)) if entry.options else {}
    hass.config_entries.async_update_entry(
        entry, data=data, options=options, version=CONFIG_ENTRY_VERSION
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a single configuration-time-active Foundation instance."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "snapshot": {},
        "pending_structural_refresh_reasons": set(),
        "structural_refresh_task": None,
        "structural_refresh_shutdown": False,
        "runtime_health": {
            "state": "UNKNOWN",
            "reason": "starting",
            "revision": 0,
            "last_success": None,
            "affected_scope": ["foundation"],
            "successful_refreshes": 0,
            "failed_refreshes": 0,
            "successful_supervision_refreshes": 0,
            "failed_supervision_refreshes": 0,
            "structural_refresh_requests": 0,
            "coalesced_structural_refresh_requests": 0,
            "structural_refresh_runs": 0,
            "last_error": None,
            "last_supervision_error": None,
        },
    }
    data = hass.data[DOMAIN][entry.entry_id]

    async def _refresh(reason: str) -> None:
        await async_refresh_snapshot(hass, entry, reason=reason)
        async_dispatcher_send(hass, FOUNDATION_DISPATCH_SIGNAL, entry.entry_id)

    async def _refresh_supervision(reason: str) -> None:
        await async_refresh_supervision_snapshot(hass, entry, reason=reason)
        async_dispatcher_send(hass, FOUNDATION_DISPATCH_SIGNAL, entry.entry_id)

    def _combined_reason(reasons: set[str]) -> str:
        ordered = sorted(reasons)
        visible = ordered[:_MAX_COALESCED_REASONS]
        suffix = f":+{len(ordered) - len(visible)}" if len(ordered) > len(visible) else ""
        return "coalesced:" + "|".join(visible) + suffix

    def _ensure_structural_refresh_task() -> None:
        if data.get("structural_refresh_shutdown"):
            return
        task = data.get("structural_refresh_task")
        if task is not None and not task.done():
            return

        async def _run_once() -> None:
            # One event-loop yield groups domain registrations/reloads that happen in
            # the same startup burst. There is deliberately no unbounded worker loop.
            await asyncio.sleep(0)
            pending = data.setdefault("pending_structural_refresh_reasons", set())
            reasons = set(pending)
            pending.clear()
            if not reasons or data.get("structural_refresh_shutdown"):
                return
            health = data.setdefault("runtime_health", {})
            health["structural_refresh_runs"] = int(health.get("structural_refresh_runs", 0)) + 1
            try:
                await _refresh(_combined_reason(reasons))
            finally:
                data["structural_refresh_task"] = None
                # An event arriving while the bounded refresh was running receives one
                # follow-up task. Continuous runtime telemetry cannot enter this path.
                if data.get("pending_structural_refresh_reasons") and not data.get("structural_refresh_shutdown"):
                    _ensure_structural_refresh_task()

        data["structural_refresh_task"] = hass.async_create_task(_run_once())

    def _request_structural_refresh(reason: str) -> None:
        if data.get("structural_refresh_shutdown"):
            return
        health = data.setdefault("runtime_health", {})
        health["structural_refresh_requests"] = int(health.get("structural_refresh_requests", 0)) + 1
        pending = data.setdefault("pending_structural_refresh_reasons", set())
        task = data.get("structural_refresh_task")
        already_pending = reason in pending
        if already_pending or (task is not None and not task.done()):
            health["coalesced_structural_refresh_requests"] = int(
                health.get("coalesced_structural_refresh_requests", 0)
            ) + 1
        pending.add(reason)
        _ensure_structural_refresh_task()

    def _cancel_pending_structural_refresh() -> None:
        data["structural_refresh_shutdown"] = True
        data.setdefault("pending_structural_refresh_reasons", set()).clear()
        task = data.get("structural_refresh_task")
        if task is not None and not task.done():
            task.cancel()
        data["structural_refresh_task"] = None

    data["cancel_pending_structural_refresh"] = _cancel_pending_structural_refresh

    async def _on_publication_changed(event: Event) -> None:
        _request_structural_refresh(
            f"domain_publication:{event.data.get('reason', 'changed')}:"
            f"{event.data.get('publisher_domain', 'unknown')}"
        )

    async def _on_supervision_changed(event: Event) -> None:
        await _refresh_supervision(
            f"domain_supervision:{event.data.get('reason', 'changed')}:"
            f"{event.data.get('domain_id', 'unknown')}"
        )

    async def _handle_refresh_service(call: ServiceCall) -> None:
        await _refresh("explicit_service_refresh")

    unsub_publication = hass.bus.async_listen(
        DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT,
        _on_publication_changed,
    )
    entry.async_on_unload(unsub_publication)
    unsub_supervision = hass.bus.async_listen(
        DOMAIN_SUPERVISORY_STATUS_CHANGED_EVENT,
        _on_supervision_changed,
    )
    entry.async_on_unload(unsub_supervision)

    if not hass.services.has_service(DOMAIN, FOUNDATION_REFRESH_SERVICE):
        hass.services.async_register(
            DOMAIN,
            FOUNDATION_REFRESH_SERVICE,
            _handle_refresh_service,
        )

    await _refresh("setup")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload only after an explicit config/options save."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    cancel_pending = data.get("cancel_pending_structural_refresh")
    if callable(cancel_pending):
        cancel_pending()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        remove_published_inputs(hass, entry.entry_id)
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if hass.services.has_service(DOMAIN, FOUNDATION_REFRESH_SERVICE):
            hass.services.async_remove(DOMAIN, FOUNDATION_REFRESH_SERVICE)
    return unloaded
