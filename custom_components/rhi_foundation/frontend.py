"""Optional, owned Home Assistant frontend transport for Foundation engineering.

A static JS card is served without credentials; all evidence comes from the
separate admin-only, read-only websocket. Native HA owns dashboard/resources.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
ROOT_URL = "/rhi_foundation/frontend"
SCRIPT = "rhi-foundation-engineering.js"
URL = ROOT_URL + "/" + SCRIPT
UX_OPTION = "enable_ux"
_TRANSPORT_KEY = "rhi_foundation_engineering_transport"
_LIFECYCLE_KEY = "rhi_foundation_engineering_lifecycle"


def enabled(entry) -> bool:
    """Optional UX defaults on; Foundation's backend works without it."""
    return entry.options.get(UX_OPTION, entry.data.get(UX_OPTION, True)) is True


async def async_register_frontend(hass) -> None:
    """Register the single fixed static route, never HA data or arbitrary files."""
    if hass.http is None or hass.data.get(_TRANSPORT_KEY):
        return
    from aiohttp import web
    from homeassistant.components.http import HomeAssistantView

    try:
        payload = await hass.async_add_executor_job(
            (Path(__file__).parent / "frontend" / SCRIPT).read_bytes
        )
    except OSError as exc:
        _LOGGER.warning("Foundation engineering frontend unavailable: %s", type(exc).__name__)
        hass.data[_TRANSPORT_KEY] = {"verified": False, "error": type(exc).__name__}
        return

    # Compilation parity and exact UX Core provenance are verified by source
    # build checks, not by a remote or self-modifying browser resource.
    class FoundationEngineeringFrontendView(HomeAssistantView):
        url = URL
        name = "rhi_foundation:engineering_frontend"
        requires_auth = False

        async def get(self, request):
            entries = hass.config_entries.async_entries(DOMAIN)
            loaded = len(entries) == 1 and str(
                getattr(entries[0].state, "value", entries[0].state)
            ).lower() == "loaded"
            if not loaded or not enabled(entries[0]):
                return web.Response(status=404, headers={"Cache-Control": "no-store"})
            return web.Response(
                body=payload, content_type="text/javascript",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    hass.http.register_view(FoundationEngineeringFrontendView())
    hass.data[_TRANSPORT_KEY] = {"verified": True, "bytes": len(payload)}


async def _reconcile_lovelace_resource(collection, *, present: bool, version: str) -> dict:
    """Touch only the exact resource marked as Foundation-managed; leave user resources."""
    await collection.async_get_info()
    managed, manual = [], []
    for item in collection.async_items():
        parts = urlsplit(str(item.get("url") or ""))
        if parts.scheme or parts.netloc or parts.path != URL:
            continue
        if parse_qs(parts.query).get("rhi_managed") == [DOMAIN]:
            managed.append(item)
        else:
            manual.append(item)
    if not present or manual:
        for item in managed:
            await collection.async_delete_item(item["id"])
        return {"registration": "manual" if manual else "disabled", "duplicate": len(manual) > 1}
    expected = f"{URL}?rhi_managed={DOMAIN}&v={version}"
    if managed:
        first, *duplicates = managed
        if first.get("url") != expected or first.get("type") != "module":
            await collection.async_update_item(
                first["id"], {"url": expected, "res_type": "module"}
            )
        for item in duplicates:
            await collection.async_delete_item(item["id"])
    else:
        await collection.async_create_item({"url": expected, "res_type": "module"})
    return {"registration": "managed", "resource_count": 1, "duplicate": False}


def async_setup_frontend_lifecycle(hass, entry) -> None:
    """Register one native resource in storage or YAML mode; preserve opt-out."""
    from homeassistant.components import frontend
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.core import callback

    state = hass.data.setdefault(_LIFECYCLE_KEY, {"lock": asyncio.Lock()})
    active = True
    owned_extra = False

    async def reconcile():
        nonlocal owned_extra
        async with state["lock"]:
            available = bool(hass.data.get(_TRANSPORT_KEY, {}).get("verified"))
            should_register = active and available and enabled(entry)
            lovelace = hass.data.get(LOVELACE_DATA)
            manager = hass.data.get(frontend.DATA_EXTRA_MODULE_URL)
            try:
                if lovelace is not None and lovelace.resource_mode == "storage":
                    if owned_extra and manager is not None and URL in manager.urls:
                        frontend.remove_extra_js_url(hass, URL)
                        owned_extra = False
                    from .const import RELEASE
                    state["status"] = await _reconcile_lovelace_resource(
                        lovelace.resources, present=should_register,
                        version=RELEASE.removeprefix("F"),
                    )
                    return
                manual = [] if lovelace is None else [
                    item for item in lovelace.resources.async_items()
                    if urlsplit(str(item.get("url") or "")).path == URL
                    and item.get("type") == "module"
                ]
                use_extra = should_register and not manual and manager is not None
                if use_extra and URL not in manager.urls:
                    frontend.add_extra_js_url(hass, URL)
                    owned_extra = True
                if not use_extra and owned_extra:
                    if manager is not None and URL in manager.urls:
                        frontend.remove_extra_js_url(hass, URL)
                    owned_extra = False
                state["status"] = {
                    "registration": "yaml_manual" if manual else
                                    "extra_module" if use_extra else "disabled",
                    "duplicate": len(manual) > 1,
                }
            except (KeyError, ValueError, AttributeError, RuntimeError) as exc:
                state["status"] = {"registration": "error", "error_type": type(exc).__name__}
                _LOGGER.warning("Foundation engineering UX registration: %s", type(exc).__name__)

    @callback
    def refresh():
        hass.async_create_task(reconcile())

    async def on_options_change(_hass, _entry):
        await reconcile()

    @callback
    def cleanup():
        nonlocal active
        active = False
        refresh()

    entry.async_on_unload(entry.add_update_listener(on_options_change))
    entry.async_on_unload(cleanup)
    refresh()
