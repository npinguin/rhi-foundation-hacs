"""Concept-centric configuration wizard for RHI Foundation V2."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_CONFIGURATION_REVISION,
    CONF_CONFIGURATION_SURFACE_SELECTIONS,
    CONF_CONCEPT_MAPPINGS,
    CONF_DEVELOPER_MODE,
    CONF_DEVICE_SELECTIONS,
    CONF_SELECTED_INTEGRATIONS,
    CONF_TECHNICAL_SELECTIONS,
    DEFAULT_DEVELOPER_MODE,
    DOMAIN,
    MODULE_DISPLAY_NAME,
)
from .configured_surfaces import configuration_surface_index
from .ha_registry import device_belongs_to_config_entry
from .publications import read_publications
from .shared_registry import async_refresh_framework_resource_providers, get_framework_resource_provider, iter_framework_resource_providers
from .wizard_surfaces import ConfigurationSurfaceWizardMixin
from .wizard_state import (
    concept_target_id,
    default_device_selection,
    default_integration_selection,
    framework_resources_available,
    mapped_integrations,
    mapping_key,
    mappings_for_concept,
    normalize_device_selection,
)

ALL_MATCHING = "__all_matching__"
FILTER_ALL = "all_matching"
FILTER_SPECIFIC = "specific_devices"


def _installed_entries(hass: Any) -> list[Any]:
    return list(hass.config_entries.async_entries())


def _installed_integrations(hass: Any) -> set[str]:
    """Return config-entry integrations plus registered HA framework providers."""
    installed = {str(entry.domain) for entry in _installed_entries(hass)}
    installed.update(str(domain) for domain, _provider in iter_framework_resource_providers(hass))
    return installed


def _specifications(hass: Any) -> list[dict[str, Any]]:
    _records, specs = read_publications(hass)
    return specs


def _framework_source_available(hass: Any, integration: str, spec: dict[str, Any]) -> bool | None:
    """Return resource-scoped availability for a registered framework provider."""
    provider = get_framework_resource_provider(hass, integration)
    if provider is None:
        return None
    getter = getattr(provider, "get_framework_resources", None)
    resources = getter() if callable(getter) else []
    return framework_resources_available(resources or [], integration, spec)

def _integration_label(integration: str) -> str:
    return integration.replace("_", " ").title()


def _domain_label(domain: str) -> str:
    return domain.replace("_", " ").title()


def _presentation_text(specs: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Return validated domain-owned presentation metadata from one concept specification set."""
    if not specs:
        return ({"display_name": "Unavailable domain", "description": "The publishing domain is currently unavailable.", "selection_guidance": "Review the preserved configuration before making changes."}, {"display_name": "Unavailable concept", "description": "The publishing domain is currently unavailable.", "selection_guidance": "Review the preserved configuration before making changes."})
    first = specs[0]
    domain = first.get("domain_presentation") or {}
    concept = first.get("concept") or {}
    return (
        {key: str(domain.get(key) or "") for key in ("display_name", "description", "selection_guidance")},
        {key: str(concept.get(key) or "") for key in ("display_name", "description", "selection_guidance")},
    )


def _domain_records(
    hass: Any, existing_mappings: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build domain/concept presentation records from publications + preserved intent."""
    installed = _installed_integrations(hass)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    specifications = _specifications(hass)
    surface_index = configuration_surface_index(specifications)

    for spec in specifications:
        concept = spec.get("concept") or {}
        domain = str(spec["domain_id"])
        concept_id = str(concept["concept_id"])
        key = (domain, concept_id)
        row = grouped.setdefault(
            key,
            {
                "domain": domain,
                "concept": concept_id,
                "label": str(concept["display_name"]),
                "specs": [],
                "builders": {},
                "published_integrations": set(),
            },
        )
        row["specs"].append(spec)
        for source in spec.get("supported_sources", []):
            integration = str(source.get("integration_domain") or "")
            if not integration:
                continue
            row["published_integrations"].add(integration)
            framework_available = _framework_source_available(hass, integration, spec)
            if integration in installed and framework_available is not False:
                row["builders"][integration] = {
                    "builder_id": str(spec["builder_id"]),
                    "builder_version": str(spec["builder_version"]),
                    "publication_revision": int(spec.get("publication_revision", 1)),
                    "specification_fingerprint": str(spec.get("specification_fingerprint") or ""),
                    "specification": spec,
                }

    # Preserve configured intent even when the provider/specification disappeared.
    for mapping in existing_mappings.values():
        if not isinstance(mapping, dict):
            continue
        domain = str(mapping.get("domain") or "")
        concept_id = str(mapping.get("concept") or "")
        integration = str(mapping.get("integration_domain") or "")
        if not domain or not concept_id:
            continue
        row = grouped.setdefault(
            (domain, concept_id),
            {
                "domain": domain,
                "concept": concept_id,
                "label": str(mapping.get("display_name") or concept_id),
                "specs": [],
                "builders": {},
                "published_integrations": set(),
            },
        )
        if integration:
            row.setdefault("configured_integrations", set()).add(integration)

    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in grouped.values():
        row["available_integrations"] = sorted(row["builders"])
        row["published_integrations"] = sorted(row["published_integrations"])
        row["configured_integrations"] = sorted(row.get("configured_integrations", set()))
        row["configuration_surfaces"] = [
            dict(surface)
            for (domain_id, concept_id, _surface_id), surface in surface_index.items()
            if domain_id == row["domain"] and concept_id == row["concept"]
        ]
        domain_presentation, concept_presentation = _presentation_text(row["specs"])
        row["domain_presentation"] = domain_presentation
        row["concept_presentation"] = concept_presentation
        if row["specs"]:
            row["label"] = concept_presentation["display_name"]
        by_domain[row["domain"]].append(row)

    return [
        {
            "domain": domain,
            "label": (concepts[0].get("domain_presentation") or {}).get("display_name") or _domain_label(domain),
            "description": (concepts[0].get("domain_presentation") or {}).get("description") or "",
            "selection_guidance": (concepts[0].get("domain_presentation") or {}).get("selection_guidance") or "",
            "concepts": sorted(concepts, key=lambda item: item["concept"]),
        }
        for domain, concepts in sorted(by_domain.items())
    ]


def _candidate_devices(hass: Any, integration: str, resource_types: set[str] | None = None) -> dict[str, str]:
    """Return integration devices or framework resources for explicit selection.

    The device chooser is intentionally *not* pre-filtered by domain raw
    matching rules.  Device choice defines the technical scope first;
    domain-published matching rules are then applied mechanically inside that
    selected scope while preparing SelectedDomainBuildInput.  This avoids a
    circular UX where a device disappears merely because no raw capability
    match has yet been found.

    """
    framework_provider = get_framework_resource_provider(hass, integration)
    if framework_provider is not None:
        getter = getattr(framework_provider, "get_framework_resources", None)
        resources = getter() if callable(getter) else []
        options = {
            str(item.get("resource_id")): str(item.get("display_name") or item.get("resource_id"))
            for item in (resources or [])
            if isinstance(item, dict)
            and item.get("resource_id")
            and (not resource_types or str(item.get("resource_type") or "") in resource_types)
        }
        return dict(sorted(options.items(), key=lambda item: item[1].lower()))

    registry = dr.async_get(hass)
    config_entry_ids = {
        entry.entry_id for entry in _installed_entries(hass) if entry.domain == integration
    }
    options: dict[str, str] = {}
    for device in registry.devices.values():
        if not any(
            device_belongs_to_config_entry(device, entry_id)
            for entry_id in config_entry_ids
        ):
            continue
        label = device.name_by_user or device.name or device.model or device.id
        detail = " / ".join(part for part in (device.manufacturer, device.model) if part)
        options[device.id] = f"{label} — {detail}" if detail else label
    return dict(sorted(options.items(), key=lambda item: item[1].lower()))



class FoundationWizardMixin(ConfigurationSurfaceWizardMixin):
    """Concept-centric wizard state."""

    _developer_mode: bool
    _selected_integrations: list[str]
    _concept_mappings: dict[str, dict[str, Any]]
    _technical_selections: list[str]
    _device_selections: dict[str, dict[str, Any]]
    _domain_queue: list[dict[str, Any]]
    _domain_index: int
    _concept_queue: list[dict[str, Any]]
    _concept_index: int
    _concept_device_queue: list[dict[str, Any]]
    _concept_device_index: int
    _technical_device_queue: list[dict[str, Any]]
    _technical_device_index: int
    _initialized: bool

    def _initialize_state(self, defaults: dict[str, Any] | None = None) -> None:
        defaults = defaults or {}
        self._developer_mode = bool(defaults.get(CONF_DEVELOPER_MODE, DEFAULT_DEVELOPER_MODE))
        self._selected_integrations = list(defaults.get(CONF_SELECTED_INTEGRATIONS, []) or [])
        self._concept_mappings = dict(defaults.get(CONF_CONCEPT_MAPPINGS, {}) or {})
        self._technical_selections = list(defaults.get(CONF_TECHNICAL_SELECTIONS, []) or [])
        self._device_selections = dict(defaults.get(CONF_DEVICE_SELECTIONS, {}) or {})
        self._configuration_surface_selections = dict(
            defaults.get(CONF_CONFIGURATION_SURFACE_SELECTIONS, {}) or {}
        )
        self._surface_queue = []
        self._surface_index = 0
        self._active_surface_instance_id = None
        self._domain_queue = []
        self._domain_index = 0
        self._concept_queue = []
        self._concept_index = 0
        self._concept_device_queue = []
        self._concept_device_index = 0
        self._technical_device_queue = []
        self._technical_device_index = 0
        self._initialized = True

    def _recompute_selected_integrations(self) -> None:
        self._selected_integrations = sorted(
            mapped_integrations(self._concept_mappings) | set(self._technical_selections)
        )

    def _current_domain(self) -> dict[str, Any]:
        return self._domain_queue[self._domain_index]

    def _current_concept(self) -> dict[str, Any]:
        return self._concept_queue[self._concept_index]

    async def async_step_mode(self, user_input=None):
        """Step 1 — normal or developer diagnostics mode."""
        if user_input is not None:
            self._developer_mode = bool(user_input[CONF_DEVELOPER_MODE])
            await async_refresh_framework_resource_providers(self.hass)
            self._domain_queue = _domain_records(self.hass, self._concept_mappings)
            self._domain_index = 0
            return await self.async_step_domain_intro()
        return self.async_show_form(
            step_id="mode",
            data_schema=vol.Schema({vol.Required(CONF_DEVELOPER_MODE, default=self._developer_mode): bool}),
        )

    async def async_step_domain_intro(self, user_input=None):
        """Introduce one domain and the concepts it publishes."""
        if self._domain_index >= len(self._domain_queue):
            if self._developer_mode:
                return await self.async_step_technical_integrations()
            if not self._domain_queue:
                return await self.async_step_no_configurable_domains()
            return await self.async_step_review()

        domain = self._current_domain()
        if user_input is not None:
            self._concept_queue = list(domain["concepts"])
            self._concept_index = 0
            return await self.async_step_concept_intro()

        concept_lines = "\n".join(f"- {item['label']}" for item in domain["concepts"]) or "- None"
        return self.async_show_form(
            step_id="domain_intro",
            data_schema=vol.Schema({}),
            description_placeholders={
                "domain": domain["label"],
                "position": f"{self._domain_index + 1}/{len(self._domain_queue)}",
                "concepts": concept_lines,
                "description": domain.get("description", ""),
                "selection_guidance": domain.get("selection_guidance", ""),
            },
        )


    async def async_step_no_configurable_domains(self, user_input=None):
        """Explain why normal mode has no configurable domain concepts."""
        if user_input is not None:
            return await self.async_step_review()
        records, _specs = read_publications(self.hass)
        publication_lines: list[str] = []
        for record in records:
            status = str(getattr(record, "status", "unknown")).upper()
            count = len(getattr(record, "specifications", ()) or ())
            issues = list(getattr(record, "issues", ()) or ())
            detail = f"{record.publisher_domain}: {status}, specifications={count}"
            if issues:
                detail += f", issues={'; '.join(str(item) for item in issues[:3])}"
            publication_lines.append(f"- {detail}")
        if not publication_lines:
            publication_lines = ["- No RHI domain publications are currently registered."]
        return self.async_show_form(
            step_id="no_configurable_domains",
            data_schema=vol.Schema({}),
            description_placeholders={
                "publication_status": "\n".join(publication_lines),
            },
        )

    async def async_step_concept_intro(self, user_input=None):
        """Explain one domain concept before technical choices are shown."""
        if self._concept_index >= len(self._concept_queue):
            self._domain_index += 1
            return await self.async_step_domain_intro()

        concept = self._current_concept()
        if user_input is not None:
            return await self.async_step_concept_source()

        available = concept["available_integrations"]
        unavailable = sorted(set(concept["published_integrations"]) - set(available))
        configured = concept["configured_integrations"]
        return self.async_show_form(
            step_id="concept_intro",
            data_schema=vol.Schema({}),
            description_placeholders={
                "domain": _domain_label(concept["domain"]),
                "concept": concept["label"],
                "description": concept["concept_presentation"]["description"],
                "usage_description": concept["concept_presentation"]["description"],
                "selection_guidance": concept["concept_presentation"]["selection_guidance"],
                "available_integrations": ", ".join(_integration_label(i) for i in available) or "None",
                "unavailable_integrations": ", ".join(_integration_label(i) for i in unavailable) or "None",
                "configured_integrations": ", ".join(_integration_label(i) for i in configured) or "None",
                "position": f"{self._concept_index + 1}/{len(self._concept_queue)}",
            },
        )

    async def async_step_concept_source(self, user_input=None):
        """Choose the source path before configuring source-specific details."""
        concept = self._current_concept()
        if not concept.get("configuration_surfaces"):
            return await self.async_step_concept_integrations()

        available = ", ".join(
            _integration_label(item) for item in concept.get("available_integrations", [])
        ) or "No installed published sources"
        return self.async_show_menu(
            step_id="concept_source",
            menu_options={
                "concept_source_integrations": "Use integration / Home Assistant sources",
                "concept_source_entities": "Configure directly from Home Assistant entities",
                "concept_source_back": "Back",
            },
            description_placeholders={
                "concept": str(concept.get("label") or concept.get("concept") or ""),
                "available_integrations": available,
            },
        )

    async def async_step_concept_source_integrations(self, user_input=None):
        """Use the published integration/device discovery path for this concept."""
        concept = self._current_concept()
        domain = str(concept["domain"])
        concept_id = str(concept["concept"])
        self._configuration_surface_selections = {
            key: value
            for key, value in self._configuration_surface_selections.items()
            if not (
                isinstance(value, dict)
                and str(value.get("domain") or "") == domain
                and str(value.get("concept") or "") == concept_id
            )
        }
        return await self.async_step_concept_integrations()

    async def async_step_concept_source_entities(self, user_input=None):
        """Use the domain-published direct entity-mapping path for this concept."""
        concept = self._current_concept()
        domain = str(concept["domain"])
        concept_id = str(concept["concept"])
        existing = mappings_for_concept(self._concept_mappings, domain, concept_id)
        for integration in list(existing):
            self._concept_mappings.pop(mapping_key(domain, concept_id, integration), None)
            self._device_selections.pop(
                concept_target_id(domain, concept_id, integration), None
            )
        self._recompute_selected_integrations()
        self._prepare_surface_queue()
        return await self.async_step_concept_surfaces()

    async def async_step_concept_source_back(self, user_input=None):
        """Return to the concept explanation without committing working state."""
        return await self.async_step_concept_intro()

    async def async_step_concept_integrations(self, user_input=None):
        """Choose zero, one, or many integrations for the current concept."""
        concept = self._current_concept()
        domain = concept["domain"]
        concept_id = concept["concept"]
        existing = mappings_for_concept(self._concept_mappings, domain, concept_id)

        choices = {
            integration: _integration_label(integration)
            for integration in concept["available_integrations"]
        }
        # A stale configured integration remains visible/selectable so user intent is
        # never silently removed merely because publication/installation disappeared.
        for integration in existing:
            if integration not in choices:
                choices[integration] = f"{_integration_label(integration)} — currently unavailable"

        if user_input is not None:
            selected = set(user_input.get("concept_integrations", []))
            # Explicit unchecking is the only path that removes a configured mapping.
            for integration in set(existing) - selected:
                key = mapping_key(domain, concept_id, integration)
                self._concept_mappings.pop(key, None)
                self._device_selections.pop(concept_target_id(domain, concept_id, integration), None)

            for integration in selected:
                builder = concept["builders"].get(integration)
                key = mapping_key(domain, concept_id, integration)
                if builder is None:
                    # Preserve stale configured intent unchanged; never invent a builder.
                    if integration in existing:
                        self._concept_mappings[key] = existing[integration]
                    continue
                self._concept_mappings[key] = {
                    "selection_kind": "configured_domain_selection",
                    "domain": domain,
                    "concept": concept_id,
                    "display_name": concept["label"],
                    "integration_domain": integration,
                    "builder_id": builder["builder_id"],
                    "builder_version": builder["builder_version"],
                    "publication_revision": builder["publication_revision"],
                    "specification_fingerprint": builder["specification_fingerprint"],
                    "creates_binding": False,
                    "creates_runtime_truth": False,
                    "creates_public_contract": False,
                    "executes_commands": False,
                }

            self._recompute_selected_integrations()
            self._prepare_concept_device_queue()
            return await self.async_step_concept_devices()

        defaults = default_integration_selection(concept["available_integrations"], set(existing))
        defaults = [integration for integration in defaults if integration in choices]
        return self.async_show_form(
            step_id="concept_integrations",
            data_schema=vol.Schema({vol.Required("concept_integrations", default=defaults): cv.multi_select(choices)}),
            description_placeholders={
                "domain": _domain_label(domain),
                "concept": concept["label"],
                "available_count": str(len(concept["available_integrations"])),
            },
        )

    def _prepare_concept_device_queue(self) -> None:
        concept = self._current_concept()
        mappings = mappings_for_concept(self._concept_mappings, concept["domain"], concept["concept"])
        self._concept_device_queue = []
        for integration in sorted(mappings):
            builder = concept.get("builders", {}).get(integration)
            # Preserve stale/unavailable configured intent, but never refine it against
            # unrelated resources while its published provider scope is unavailable.
            if not builder:
                continue
            specification = builder.get("specification") or {}
            resource_types = {
                str(source.get("resource_type") or "")
                for source in (specification.get("supported_sources") or [])
                if isinstance(source, dict)
                and str(source.get("integration_domain") or "") == integration
                and source.get("resource_type")
            }
            self._concept_device_queue.append({
                "target_id": concept_target_id(concept["domain"], concept["concept"], integration),
                "integration_domain": integration,
                "title": f"{concept['label']} — {_integration_label(integration)}",
                "framework_resource_types": sorted(resource_types),
            })
        self._concept_device_index = 0

    async def async_step_concept_devices(self, user_input=None):
        """Choose devices per selected integration for the current concept."""
        if self._concept_device_index >= len(self._concept_device_queue):
            return await self.async_step_concept_review()

        target = self._concept_device_queue[self._concept_device_index]
        target_id = target["target_id"]
        integration = target["integration_domain"]
        resource_types = set(target.get("framework_resource_types") or [])
        devices = _candidate_devices(self.hass, integration, resource_types)

        # Some HA integrations expose useful entities without separate DeviceEntry
        # objects. In that case there is nothing meaningful to refine at device
        # level: the user's explicit integration selection is the complete scope.
        # Persist the technical all-matching token for downstream build input, but
        # do not misrepresent it as an explicit "select all devices" choice.
        if not devices and user_input is None:
            self._device_selections[target_id] = {
                "selection_kind": "configured_domain_selection",
                "integration_domain": integration,
                "device_filter_mode": FILTER_ALL,
                "selected_device_ids": [ALL_MATCHING],
                "selection_state": "configured",
                "selection_origin": "integration_scope_no_devices",
                "creates_binding": False,
                "creates_runtime_truth": False,
                "creates_public_contract": False,
                "executes_commands": False,
            }
            self._concept_device_index += 1
            return await self.async_step_concept_devices()

        errors: dict[str, str] = {}
        if user_input is not None:
            mode = str(user_input["device_filter_mode"])
            selected_devices, error = normalize_device_selection(
                mode,
                list(user_input.get("selected_devices", [])),
                set(devices),
                all_matching_token=ALL_MATCHING,
            )
            if error is None:
                self._device_selections[target_id] = {
                    "selection_kind": "configured_domain_selection",
                    "integration_domain": integration,
                    "device_filter_mode": mode,
                    "selected_device_ids": selected_devices,
                    "selection_state": "review_required" if mode == FILTER_ALL else "configured",
                    "creates_binding": False,
                    "creates_runtime_truth": False,
                    "creates_public_contract": False,
                    "executes_commands": False,
                }
                self._concept_device_index += 1
                return await self.async_step_concept_devices()
            errors["base"] = error

        existing = self._device_selections.get(target_id, {})
        default_mode, default_devices = default_device_selection(set(devices), existing)
        is_framework = get_framework_resource_provider(self.hass, integration) is not None
        choice_labels = (
            {
                FILTER_ALL: "All matching framework resources — review required",
                FILTER_SPECIFIC: "Specific framework resources",
            }
            if is_framework
            else {
                FILTER_ALL: "All matching devices — review required",
                FILTER_SPECIFIC: "Specific devices",
            }
        )
        schema: dict[Any, Any] = {
            vol.Required("device_filter_mode", default=default_mode): vol.In(choice_labels)
        }
        if devices:
            schema[vol.Optional("selected_devices", default=default_devices)] = cv.multi_select(devices)
        return self.async_show_form(
            step_id="concept_devices",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "concept": self._current_concept()["label"],
                "integration": _integration_label(integration),
                "position": f"{self._concept_device_index + 1}/{len(self._concept_device_queue)}",
                "device_count": str(len(devices)),
            },
        )

    async def async_step_concept_review(self, user_input=None):
        """Review one concept before moving to the next concept.

        ``review_required`` is a UX obligation, not a passive persisted state.
        Any all-matching selection must therefore be explicitly acknowledged
        during this concept review before the wizard can continue.
        """
        concept = self._current_concept()
        mappings = mappings_for_concept(self._concept_mappings, concept["domain"], concept["concept"])
        lines: list[str] = []
        review_required: list[str] = []
        for integration in sorted(mappings):
            target_id = concept_target_id(concept["domain"], concept["concept"], integration)
            selection = self._device_selections.get(target_id, {})
            is_framework = get_framework_resource_provider(self.hass, integration) is not None
            if selection.get("device_filter_mode") == FILTER_SPECIFIC:
                count = len(selection.get("selected_device_ids", []))
                noun = "framework resource(s)" if is_framework else "device(s)"
                lines.append(f"- {_integration_label(integration)}: {count} selected {noun}")
            elif selection.get("selection_origin") == "integration_scope_no_devices":
                detail = "framework scope (no matching resources)" if is_framework else "integration scope (no separate HA devices)"
                lines.append(f"- {_integration_label(integration)}: {detail}")
            else:
                detail = "all matching framework resources" if is_framework else "all matching devices"
                lines.append(f"- {_integration_label(integration)}: {detail}")
                if selection.get("selection_state") == "review_required":
                    review_required.append(_integration_label(integration))
        if not lines:
            lines = ["- Not configured"]

        # Refinement-first UX: explicit concrete selections do not need an
        # extra per-concept confirmation screen.  The final Foundation review
        # still shows the complete configuration.  Only exceptional scopes
        # such as all-matching remain a mandatory concept-review step.
        if not review_required and user_input is None:
            self._prepare_surface_queue()
            return await self.async_step_concept_surfaces()

        errors: dict[str, str] = {}
        if user_input is not None:
            if review_required and not bool(user_input.get("confirm_all_matching", False)):
                errors["base"] = "confirm_all_matching"
            else:
                # Explicit concept review closes review_required for all-matching
                # selections without pretending that Foundation created semantic
                # binding or domain truth.
                for integration in sorted(mappings):
                    target_id = concept_target_id(concept["domain"], concept["concept"], integration)
                    selection = self._device_selections.get(target_id)
                    if selection and selection.get("device_filter_mode") == FILTER_ALL:
                        selection["selection_state"] = "configured"
                self._prepare_surface_queue()
                return await self.async_step_concept_surfaces()

        schema: dict[Any, Any] = {}
        if review_required:
            schema[vol.Required("confirm_all_matching", default=False)] = bool
        return self.async_show_form(
            step_id="concept_review",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "domain": _domain_label(concept["domain"]),
                "concept": concept["label"],
                "configuration": "\n".join(lines),
                "review_required": ", ".join(review_required) or "None",
            },
        )

    async def async_step_technical_integrations(self, user_input=None):
        """Developer-only optional technical observation outside domain concepts."""
        mapped = mapped_integrations(self._concept_mappings)
        available = sorted(_installed_integrations(self.hass) - mapped)
        options = {item: _integration_label(item) for item in available}
        if user_input is not None:
            self._technical_selections = sorted(user_input.get("technical_integrations", []))
            self._recompute_selected_integrations()
            self._technical_device_queue = [
                {
                    "target_id": f"technical:{integration}",
                    "integration_domain": integration,
                }
                for integration in self._technical_selections
            ]
            self._technical_device_index = 0
            return await self.async_step_technical_devices()
        defaults = [item for item in self._technical_selections if item in options]
        return self.async_show_form(
            step_id="technical_integrations",
            data_schema=vol.Schema({vol.Required("technical_integrations", default=defaults): cv.multi_select(options)}),
            description_placeholders={"available_count": str(len(options))},
        )

    async def async_step_technical_devices(self, user_input=None):
        """Developer-only device filters for technical observation selections."""
        if self._technical_device_index >= len(self._technical_device_queue):
            active = {item["target_id"] for item in self._technical_device_queue}
            self._device_selections = {
                key: value
                for key, value in self._device_selections.items()
                if not key.startswith("technical:") or key in active
            }
            return await self.async_step_review()

        target = self._technical_device_queue[self._technical_device_index]
        target_id = target["target_id"]
        integration = target["integration_domain"]
        devices = _candidate_devices(self.hass, integration)
        if not devices and user_input is None:
            self._device_selections[target_id] = {
                "selection_kind": "configured_technical_observer_selection",
                "integration_domain": integration,
                "device_filter_mode": FILTER_ALL,
                "selected_device_ids": [ALL_MATCHING],
                "selection_state": "configured",
                "selection_origin": "integration_scope_no_devices",
                "creates_binding": False,
                "creates_runtime_truth": False,
                "creates_public_contract": False,
                "executes_commands": False,
            }
            self._technical_device_index += 1
            return await self.async_step_technical_devices()

        errors: dict[str, str] = {}
        if user_input is not None:
            mode = str(user_input["device_filter_mode"])
            selected_devices, error = normalize_device_selection(
                mode,
                list(user_input.get("selected_devices", [])),
                set(devices),
                all_matching_token=ALL_MATCHING,
            )
            if error is None:
                self._device_selections[target_id] = {
                    "selection_kind": "configured_technical_observer_selection",
                    "integration_domain": integration,
                    "device_filter_mode": mode,
                    "selected_device_ids": selected_devices,
                    "selection_state": "review_required" if mode == FILTER_ALL else "configured",
                    "creates_binding": False,
                    "creates_runtime_truth": False,
                    "creates_public_contract": False,
                    "executes_commands": False,
                }
                self._technical_device_index += 1
                return await self.async_step_technical_devices()
            errors["base"] = error

        existing = self._device_selections.get(target_id, {})
        default_mode = existing.get("device_filter_mode")
        if default_mode not in {FILTER_ALL, FILTER_SPECIFIC}:
            default_mode = FILTER_SPECIFIC if devices else FILTER_ALL
        default_devices = [item for item in existing.get("selected_device_ids", []) if item in devices]
        is_framework = get_framework_resource_provider(self.hass, integration) is not None
        labels = (
            {FILTER_ALL: "All matching framework resources — review required", FILTER_SPECIFIC: "Specific framework resources"}
            if is_framework
            else {FILTER_ALL: "All matching devices — review required", FILTER_SPECIFIC: "Specific devices"}
        )
        schema: dict[Any, Any] = {
            vol.Required("device_filter_mode", default=default_mode): vol.In(labels)
        }
        if devices:
            schema[vol.Optional("selected_devices", default=default_devices)] = cv.multi_select(devices)
        return self.async_show_form(
            step_id="technical_devices",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "integration": _integration_label(integration),
                "position": f"{self._technical_device_index + 1}/{len(self._technical_device_queue)}",
                "device_count": str(len(devices)),
            },
        )

    async def async_step_review(self, user_input=None):
        """Final readable review; Submit confirms the complete Foundation config."""
        if user_input is not None:
            return self._finish_flow()

        grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        review_required: list[str] = []
        for mapping in self._concept_mappings.values():
            if not isinstance(mapping, dict):
                continue
            domain = str(mapping.get("domain") or "")
            concept = str(mapping.get("concept") or "")
            label = str(mapping.get("display_name") or concept)
            integration = str(mapping.get("integration_domain") or "")
            if integration:
                grouped[(domain, concept, label)].append(integration)
                selection = self._device_selections.get(concept_target_id(domain, concept, integration), {})
                if (
                    selection.get("device_filter_mode") == FILTER_ALL
                    and selection.get("selection_state") == "review_required"
                ):
                    review_required.append(f"{_domain_label(domain)} / {label} / {_integration_label(integration)}")

        domain_lines = [
            f"- {_domain_label(domain)} / {label}: " + ", ".join(_integration_label(i) for i in sorted(integrations))
            for (domain, _concept, label), integrations in sorted(grouped.items())
        ]
        technical_lines = [f"- {_integration_label(item)}" for item in sorted(self._technical_selections)]
        surface_lines = [
            f"- {_domain_label(str(item.get('domain') or ''))} / "
            f"{item.get('display_name') or item.get('object_type') or item.get('surface_id')}"
            for item in self._configuration_surface_selections.values()
            if isinstance(item, dict)
        ]
        summary = "\n".join(
            [
                "Configured domain concepts:",
                *(domain_lines or ["- None"]),
                "",
                "Custom logical device mappings:",
                *(surface_lines or ["- None"]),
                "",
                "Technical observation only:",
                *(technical_lines or ["- None"]),
                "",
                "Needs review because all matching devices were selected:",
                *([f"- {item}" for item in review_required] or ["- None"]),
                "",
                "Foundation prepares technical build input only. Semantic binding, runtime truth and commands remain owned by the domain.",
            ]
        )
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={"review_summary": summary},
        )

    def _payload(self, previous_revision: int = 0) -> dict[str, Any]:
        self._recompute_selected_integrations()
        return {
            CONF_DEVELOPER_MODE: self._developer_mode,
            CONF_SELECTED_INTEGRATIONS: self._selected_integrations,
            CONF_CONCEPT_MAPPINGS: self._concept_mappings,
            CONF_TECHNICAL_SELECTIONS: self._technical_selections,
            CONF_DEVICE_SELECTIONS: self._device_selections,
            CONF_CONFIGURATION_SURFACE_SELECTIONS: self._configuration_surface_selections,
            CONF_CONFIGURATION_REVISION: previous_revision + 1,
        }


class RhiFoundationConfigFlow(FoundationWizardMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Initial Foundation wizard."""

    VERSION = 6

    async def async_step_user(self, user_input=None):
        if not getattr(self, "_initialized", False):
            self._initialize_state()
        return await self.async_step_mode(user_input)

    def _finish_flow(self):
        return self.async_create_entry(title=MODULE_DISPLAY_NAME, data=self._payload())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RhiFoundationOptionsFlow()


class RhiFoundationOptionsFlow(FoundationWizardMixin, config_entries.OptionsFlow):
    """Re-run the complete wizard from Configure."""

    async def async_step_init(self, user_input=None):
        if not getattr(self, "_initialized", False):
            defaults = dict(self.config_entry.data)
            defaults.update(dict(self.config_entry.options))
            self._initialize_state(defaults)
        # HA starts every options flow at ``init``.  Treat only an explicit mode
        # payload as a submitted mode step; an empty/implicit payload must render
        # the real wizard instead of producing the generic empty Options form.
        if isinstance(user_input, dict) and CONF_DEVELOPER_MODE in user_input:
            return await self.async_step_mode(user_input)
        return await self.async_step_mode(None)

    def _finish_flow(self):
        previous_revision = int(
            self.config_entry.options.get(
                CONF_CONFIGURATION_REVISION,
                self.config_entry.data.get(CONF_CONFIGURATION_REVISION, 0),
            )
        )
        return self.async_create_entry(title="", data=self._payload(previous_revision))
