"""Generic technical capability classification. No domain semantics are allowed here."""
from __future__ import annotations

from typing import Any

POWER_UNITS = {"W", "kW", "MW"}
ENERGY_UNITS = {"Wh", "kWh", "MWh"}
CURRENT_UNITS = {"A", "mA"}
VOLTAGE_UNITS = {"V", "mV", "kV"}
FREQUENCY_UNITS = {"Hz", "kHz"}
PERCENT_UNITS = {"%"}
TEMPERATURE_UNITS = {"°C", "°F", "K"}
PRICE_HINTS = {"monetary", "price"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def classify_entity(*, entity_domain: str, device_class: Any, state_class: Any, unit: Any) -> list[str]:
    """Return domain-neutral capability classes from HA metadata only."""
    classes: list[str] = []
    dc = _text(device_class).lower()
    u = _text(unit)

    if dc == "power" or u in POWER_UNITS:
        classes.append("power_measurement")
    if dc in {"energy", "energy_storage"} or u in ENERGY_UNITS:
        classes.append("energy_counter" if dc == "energy" else "energy_capacity")
    if dc in {"battery", "humidity"} or u in PERCENT_UNITS:
        classes.append("percentage_measurement")
    if dc == "current" or u in CURRENT_UNITS:
        classes.append("current_measurement")
    if dc == "voltage" or u in VOLTAGE_UNITS:
        classes.append("voltage_measurement")
    if dc == "frequency" or u in FREQUENCY_UNITS:
        classes.append("frequency_measurement")
    if dc in {"temperature", "apparent_temperature"} or u in TEMPERATURE_UNITS:
        classes.append("temperature_measurement")
    if dc in PRICE_HINTS:
        classes.append("price_measurement")

    # Write surfaces are technical interaction surfaces, not domain commands.
    if entity_domain == "number":
        classes.append("number_write_surface")
    elif entity_domain == "select":
        classes.append("select_write_surface")
    elif entity_domain in {"switch", "input_boolean"}:
        classes.append("binary_write_surface")

    if not classes:
        classes.append("state_observation")
    return list(dict.fromkeys(classes))


def classify_config_key(key: str) -> str | None:
    """Classify technical-unit configuration metadata without exposing its value."""
    token = key.lower()
    if token.endswith(("_kw", "_w", "_mw")):
        return "power_capacity"
    if token.endswith(("_kwh", "_wh", "_mwh")):
        return "energy_capacity"
    if token.endswith(("_a", "_ma")):
        return "current_limit"
    if token.endswith(("_pct", "_percent", "_percentage")):
        return "percentage_configuration"
    return None


def requirement_capabilities(requirement: dict[str, Any]) -> set[str]:
    capabilities = requirement.get("technical_capabilities") or {}
    result: set[str] = set()
    for key in ("any_of", "all_of"):
        values = capabilities.get(key) or []
        result.update(str(item) for item in values if item)
    return result
