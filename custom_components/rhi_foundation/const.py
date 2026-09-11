"""Constants for Robotix Home Intelligence - Foundation Module."""
from __future__ import annotations

DOMAIN = "rhi_foundation"
MODULE_DISPLAY_NAME = "Robotix Home Intelligence - Foundation Module"
RELEASE = "F1.7.5"
RELEASE_NAME = "HINT_ONLY_PUBLICATION_VALIDATION"
SHARED_BASELINE_ID = "RHI_SHARED_ARCHITECTURE_BASELINE"
SHARED_BASELINE_VERSION = "1.7.1"
EXECUTION_MODEL = "configuration_time_active_runtime_passive"
MINIMUM_HOME_ASSISTANT = "2026.8"

PLATFORMS = ["sensor"]

CONF_DEVELOPER_MODE = "developer_mode"
CONF_SELECTED_INTEGRATIONS = "selected_integrations"
CONF_CONCEPT_MAPPINGS = "concept_mappings"
CONF_TECHNICAL_SELECTIONS = "technical_selections"
CONF_DEVICE_SELECTIONS = "device_selections"
CONF_CONFIGURATION_REVISION = "configuration_revision"

DEFAULT_DEVELOPER_MODE = False

DOMAIN_BUILD_SPECIFICATION_REGISTRY = "rhi_domain_build_specification_registry"
DOMAIN_BUILD_SPECIFICATIONS_CHANGED_EVENT = "rhi_domain_build_specifications_changed"
SELECTED_DOMAIN_BUILD_INPUT_REGISTRY = "rhi_selected_domain_build_input_registry"
SELECTED_DOMAIN_BUILD_INPUTS_CHANGED_EVENT = "rhi_selected_domain_build_inputs_changed"
FOUNDATION_REFRESH_SERVICE = "refresh_snapshot"
FOUNDATION_DISPATCH_SIGNAL = "rhi_foundation_snapshot_updated"

SAFETY = {
    "creates_binding": False,
    "creates_runtime_truth": False,
    "creates_public_contract": False,
    "executes_commands": False,
}

SOURCE_KINDS = (
    "entity",
    "service",
    "device_action",
    "config_entry_provider",
    "integration_api",
    "configured_product_capability",
)

ATTRIBUTE_PREVIEW_LIMIT = 20

HEALTH_STATES = ("OK", "DEGRADED", "STALE", "INVALID", "UNKNOWN")
