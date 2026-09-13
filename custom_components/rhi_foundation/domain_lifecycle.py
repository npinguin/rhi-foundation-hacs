"""Foundation-owned lifecycle operations for persisted domain technical intent.

Transient provider unload is intentionally not handled here. A producer domain calls
this API only from its genuine Home Assistant config-entry removal lifecycle. That
keeps ownership explicit: the domain knows it is being removed; Foundation alone
mutates Foundation-owned persisted configuration.
"""
from __future__ import annotations

from typing import Any

from .const import DOMAIN
from .domain_config import configured_domains, remove_domain_configuration


def _publisher_owns_domain(*, publisher_domain: str, domain_id: str) -> bool:
    return publisher_domain == f"rhi_{domain_id}"


async def async_remove_domain_configuration(
    hass: Any,
    *,
    domain_id: str,
    publisher_domain: str,
) -> bool:
    """Remove one producer domain's persisted Foundation configuration.

    Returns ``True`` when Foundation configuration changed and ``False`` when there
    was nothing to remove. The producer cannot remove another domain's state.
    Home Assistant's normal Foundation update listener performs the resulting reload
    and republishes SelectedDomainBuildInput without the removed domain.
    """
    domain_id = str(domain_id or "")
    publisher_domain = str(publisher_domain or "")
    if not domain_id or not _publisher_owns_domain(
        publisher_domain=publisher_domain,
        domain_id=domain_id,
    ):
        raise ValueError("publisher_does_not_own_domain")

    changed = False
    for entry in list(hass.config_entries.async_entries(DOMAIN)):
        current = dict(entry.data)
        current.update(dict(entry.options))
        if domain_id not in configured_domains(current):
            continue
        cleaned = remove_domain_configuration(current, domain_id=domain_id)
        hass.config_entries.async_update_entry(entry, options=cleaned)
        changed = True
    return changed
