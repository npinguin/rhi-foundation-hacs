"""Read-only administrator websocket transport for the Foundation engineering UX.

No alternate discovery, command execution or configuration write path.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .engineering_view import current_engineering_view, current_module_evidence

_COMMAND = "rhi_foundation/engineering"
_REGISTERED_KEY = "rhi_foundation_engineering_ws_registered"


@websocket_api.websocket_command({
    vol.Required("type"): _COMMAND,
    vol.Optional("kind", default=""): vol.In((
        "", "integration_entry", "device", "entity",
        "framework_capability", "service_surface", "area", "floor", "label",
    )),
    vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=50000)),
    vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional("query", default=""): vol.All(str, vol.Length(max=96)),
    vol.Optional("module_export", default=""): vol.All(str, vol.Length(min=0, max=96)),
    vol.Optional("module_values", default=False): bool,
    vol.Optional("module_choices", default=False): bool,
    vol.Optional("save_module"): vol.Schema({
        vol.Required("module_id"): vol.All(str, vol.Length(min=1, max=96)),
        vol.Required("expected_revision"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required("changes"): vol.All(list, vol.Length(min=1, max=100)),
        vol.Optional("confirm_all", default=False): bool,
    }),
})
@websocket_api.async_response
async def websocket_engineering(hass: HomeAssistant, connection, msg) -> None:
    """Expose only whitelisted source evidence to an authenticated HA admin."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator required")
        return
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1 or entry_state(entries[0]) != "loaded":
        connection.send_error(msg["id"], "not_loaded", "Foundation is not loaded")
        return
    try:
        if msg.get("save_module") is not None:
            from .config_flow import apply_engineering_selection
            payload = msg["save_module"]
            response = await apply_engineering_selection(
                hass, entries[0], domain_id=payload["module_id"],
                expected_revision=payload["expected_revision"],
                changes=payload["changes"], confirm_all=payload.get("confirm_all", False),
            )
            connection.send_result(msg["id"], response)
            return
        module_id = msg.get("module_export") or ""
        response = (
            current_module_evidence(hass, entries[0], module_id,
                include_values=msg.get("module_values", False),
                include_choices=msg.get("module_choices", False))
            if module_id else current_engineering_view(
                hass, entries[0], kind=msg.get("kind", ""),
                offset=msg.get("offset", 0), limit=msg.get("limit", 50),
                query=msg.get("query", ""),
            )
        )
    except (TypeError, ValueError, KeyError) as exc:
        connection.send_error(msg["id"], "projection_unavailable",
                              str(exc)[:120])
        return
    connection.send_result(msg["id"], response)


def entry_state(entry) -> str:
    """Normalize HA ConfigEntryState enum; never infer readiness from presence."""
    state = getattr(entry, "state", None)
    return str(getattr(state, "value", state) or "").lower()


def async_register_engineering_ws(hass: HomeAssistant) -> None:
    """One registered command per HA instance, not per reload/config entry."""
    if hass.data.get(_REGISTERED_KEY):
        return
    websocket_api.async_register_command(hass, websocket_engineering)
    hass.data[_REGISTERED_KEY] = True
