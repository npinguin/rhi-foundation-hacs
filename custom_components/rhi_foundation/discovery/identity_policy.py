"""Source-kind-specific identity and repair policy."""
from __future__ import annotations

from ..models.source_identity import SourceIdentity


def is_rename_only(previous: SourceIdentity, current: SourceIdentity) -> bool:
    if previous.source_kind != current.source_kind:
        return False

    if previous.source_kind == "entity":
        return (
            previous.integration_domain == current.integration_domain
            and previous.config_entry_id == current.config_entry_id
            and previous.entity_registry_id == current.entity_registry_id
            and previous.unique_id == current.unique_id
            and previous.device_registry_id == current.device_registry_id
        )

    # Other source kinds currently require exact dataclass equality.
    # This is deliberately conservative for the first V2 release.
    return previous == current


def replacement_required(previous: SourceIdentity, current: SourceIdentity) -> bool:
    return not is_rename_only(previous, current)


def availability_triggers_replacement(availability: str) -> bool:
    # Temporary unavailable/unknown never trigger replacement.
    return availability == "missing"
