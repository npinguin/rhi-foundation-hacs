"""Typed source identities for Foundation capability candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class EntitySourceIdentity:
    source_kind: Literal["entity"]
    integration_domain: str
    config_entry_id: str
    entity_registry_id: str
    unique_id: str
    current_entity_id: str
    device_registry_id: str | None = None
    target_scope: Literal["entity"] = "entity"


@dataclass(frozen=True, slots=True)
class ServiceSourceIdentity:
    source_kind: Literal["service"]
    integration_domain: str
    service_domain: str
    service_name: str
    target: dict[str, str]
    config_entry_id: str | None = None
    target_scope: Literal["entity", "device", "config_entry", "none"] = "none"


@dataclass(frozen=True, slots=True)
class DeviceActionSourceIdentity:
    source_kind: Literal["device_action"]
    integration_domain: str
    config_entry_id: str
    device_registry_id: str
    action_domain: str
    action_type: str
    action_subtype: str | None = None
    target_scope: Literal["device"] = "device"


@dataclass(frozen=True, slots=True)
class ConfigEntryProviderSourceIdentity:
    source_kind: Literal["config_entry_provider"]
    integration_domain: str
    config_entry_id: str
    provider_key: str
    target_scope: Literal["config_entry"] = "config_entry"


@dataclass(frozen=True, slots=True)
class IntegrationApiSourceIdentity:
    source_kind: Literal["integration_api"]
    integration_domain: str
    config_entry_id: str
    api_capability_id: str
    resource_id: str
    adapter_contract_version: str
    target_scope: Literal["resource"] = "resource"


@dataclass(frozen=True, slots=True)
class ConfiguredProductCapabilitySourceIdentity:
    source_kind: Literal["configured_product_capability"]
    configuration_owner: Literal["rhi_foundation"]
    configuration_revision: int
    selection_id: str
    capability_key: str
    target_scope: Literal["configured_selection"] = "configured_selection"


SourceIdentity: TypeAlias = (
    EntitySourceIdentity
    | ServiceSourceIdentity
    | DeviceActionSourceIdentity
    | ConfigEntryProviderSourceIdentity
    | IntegrationApiSourceIdentity
    | ConfiguredProductCapabilitySourceIdentity
)
