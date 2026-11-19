"""Domain-scoped configuration entrypoint for RHI Foundation V2.

The concept/device wizard implementation lives in ``wizard`` so lifecycle scoping can
remain small and explicit without duplicating proven matching UI logic.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_CONFIGURATION_SURFACE_SELECTIONS, CONF_CONCEPT_MAPPINGS, CONF_DEVELOPER_MODE
from .domain_config import (
    configured_domains,
    effective_entry_configuration,
    merge_domain_configuration,
    remove_domain_configuration,
)
from .shared_registry import async_refresh_framework_resource_providers
from .wizard import (
    FoundationWizardMixin,
    RhiFoundationConfigFlow as _WizardConfigFlow,
    _candidate_devices,
    _domain_label,
    _domain_records,
    _integration_label,
)
from .wizard_candidates import entity_source_selection
from .wizard_state import concept_target_id
from .discovery.site_inventory_export import build_site_inventory, capture_native_site
from .const import SITE_INVENTORY_EXPORT_CACHE

FOUNDATION_SETTINGS = "__foundation_settings__"


def _domain_is_available(record: dict[str, Any]) -> bool:
    return any(bool(concept.get("specs")) for concept in record.get("concepts", []))


def _domain_choices(records: list[dict[str, Any]], mappings: dict[str, Any], surfaces: dict[str, Any] | None = None) -> dict[str, str]:
    configured = configured_domains({CONF_CONCEPT_MAPPINGS: mappings, CONF_CONFIGURATION_SURFACE_SELECTIONS: surfaces or {}})
    choices: dict[str, str] = {}
    for record in records:
        domain_id = str(record["domain"])
        state = ["configured" if domain_id in configured else "not configured"]
        state.append("provider available" if _domain_is_available(record) else "provider unavailable")
        choices[domain_id] = f"{record['label']} — {', '.join(state)}"
    return choices


async def _scoped_initial_user(self: _WizardConfigFlow, user_input=None):
    """Install independently; explicit domain commissioning remains in Options."""
    if not getattr(self, "_initialized", False):
        self._initialize_state()
    if user_input is not None:
        # Empty technical intent is deliberate. The existing setup lifecycle
        # discovers providers; discovery alone cannot approve a device mapping.
        return self._finish_flow()
    return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

class RhiFoundationOptionsFlow(FoundationWizardMixin, config_entries.OptionsFlow):
    """Configure, reconfigure or remove exactly one Foundation-owned scope."""

    _base_config: dict[str, Any]
    _selected_domain_id: str | None
    _records: list[dict[str, Any]]

    async def async_step_init(self, user_input=None):
        if not getattr(self, "_initialized", False):
            defaults = effective_entry_configuration(
                self.config_entry.data, self.config_entry.options
            )
            # Freeze persisted intent separately from editable nested wizard state.
            self._base_config = deepcopy(defaults)
            self._selected_domain_id = None
            self._initialize_state(deepcopy(defaults))
        await async_refresh_framework_resource_providers(self.hass)
        self._records = _domain_records(self.hass, self._concept_mappings, self._configuration_surface_selections)
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "configure_domain",
                "remove_domain_select",
                "foundation_settings",
                "site_discovery",
            ],
        )

    async def async_step_site_discovery(self, user_input=None):
        """One explicit HA-native inventory operation; no options Save or reload."""
        if user_input is not None:
            if not bool(user_input.get("confirm_capture", False)):
                return self.async_show_form(
                    step_id="site_discovery",
                    data_schema=vol.Schema({vol.Required("confirm_capture", default=False): bool}),
                    errors={"base": "confirm_capture"},
                )
            export = build_site_inventory(capture_native_site(self.hass))
            self.hass.data[SITE_INVENTORY_EXPORT_CACHE] = export
            return await self.async_step_site_discovery_result()
        return self.async_show_form(
            step_id="site_discovery",
            data_schema=vol.Schema({vol.Required("confirm_capture", default=False): bool}),
        )

    async def async_step_site_discovery_result(self, user_input=None):
        """Display the result, then end without rewriting persisted configuration."""
        if user_input is not None:
            return self.async_abort(reason="site_discovery_complete")
        export = self.hass.data.get(SITE_INVENTORY_EXPORT_CACHE)
        if not isinstance(export, dict):
            return self.async_abort(reason="site_discovery_missing")
        counts = {}
        for row in export["rows"]:
            kind = str(row.get("kind") or "other")
            counts[kind] = counts.get(kind, 0) + 1
        return self.async_show_form(
            step_id="site_discovery_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "status": str(export["status"]),
                "integrations": str(counts.get("integration_entry", 0)),
                "devices": str(counts.get("device", 0)),
                "entities": str(counts.get("entity", 0)),
                "capabilities": str(counts.get("framework_capability", 0)),
                "row_count": str(export["row_count"]),
                "errors": ", ".join(sorted(export["source_errors"])) or "none",
                "fingerprint": str(export["fingerprint"][:16]),
            },
        )

    async def async_step_configure_domain(self, user_input=None):
        choices = _domain_choices(self._records, self._concept_mappings, self._configuration_surface_selections)
        if not choices:
            return self.async_abort(reason="no_configurable_domains")
        errors: dict[str, str] = {}
        if user_input is not None:
            domain_id = str(user_input.get("domain_id") or "")
            selected = next(
                (item for item in self._records if str(item["domain"]) == domain_id),
                None,
            )
            if selected is None:
                errors["base"] = "invalid_domain"
            else:
                self._selected_domain_id = domain_id
                self._domain_queue = [selected]
                self._domain_index = 0
                return await self.async_step_domain_intro()
        return self.async_show_form(
            step_id="configure_domain",
            data_schema=vol.Schema({vol.Required("domain_id"): vol.In(choices)}),
            errors=errors,
            description_placeholders={"domain_count": str(len(choices))},
        )

    async def async_step_remove_domain_select(self, user_input=None):
        configured = configured_domains(self._base_config)
        choices = {
            domain_id: label
            for domain_id, label in _domain_choices(
                self._records, self._concept_mappings, self._configuration_surface_selections
            ).items()
            if domain_id in configured
        }
        if not choices:
            return self.async_abort(reason="no_configured_domains_to_remove")
        errors: dict[str, str] = {}
        if user_input is not None:
            domain_id = str(user_input.get("domain_id") or "")
            if domain_id not in choices:
                errors["base"] = "domain_not_configured"
            else:
                self._selected_domain_id = domain_id
                return await self.async_step_remove_domain()
        return self.async_show_form(
            step_id="remove_domain_select",
            data_schema=vol.Schema({vol.Required("domain_id"): vol.In(choices)}),
            errors=errors,
        )

    async def async_step_foundation_settings(self, user_input=None):
        # Menu navigation is not a domain edit. Without this explicit global
        # transaction identity, _finish_flow uses merge_domain_configuration
        # with domain_id="", discarding the updated developer flag and source
        # observations while reporting a successful options Save.
        self._selected_domain_id = FOUNDATION_SETTINGS
        if user_input is not None:
            self._developer_mode = bool(user_input.get(CONF_DEVELOPER_MODE, False))
            if self._developer_mode:
                return await self.async_step_technical_integrations()
            self._technical_selections = []
            self._device_selections = {
                key: value
                for key, value in self._device_selections.items()
                if not str(key).startswith("technical:")
            }
            return await self.async_step_review()
        return self.async_show_form(
            step_id="foundation_settings",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVELOPER_MODE, default=self._developer_mode): bool}
            ),
        )

    async def async_step_domain_intro(self, user_input=None):
        """Edit one published concept at a time, then review the whole domain."""
        domain = self._current_domain()
        choices = {"__review__": "Review and save"}
        for concept in domain["concepts"]:
            if concept["specs"]:
                choices[concept["concept"]] = concept["label"]
        errors = {}
        if user_input is not None:
            selected = user_input.get("concept_id")
            if selected == "__review__":
                return await self.async_step_review()
            if selected in choices:
                self._concept_queue = [item for item in domain["concepts"] if item["concept"] == selected]
                self._concept_index = 0
                return await self.async_step_concept_intro()
            errors["base"] = "invalid_domain"
        return self.async_show_form(
            step_id="domain_intro",
            data_schema=vol.Schema({vol.Required("concept_id", default="__review__"): vol.In(choices)}),
            errors=errors,
            description_placeholders={
                "description": domain.get("description", ""),
                "selection_guidance": domain.get("selection_guidance", ""),
                "concepts": "\n".join(
                    f"- {item['label']}" + (" (provider unavailable; preserved)" if not item["specs"] else "")
                    for item in domain["concepts"]
                ),
            },
        )

    async def async_step_concept_intro(self, user_input=None):
        """Show guidance and source choices together; no read-only extra screen."""
        if self._concept_index >= len(self._concept_queue):
            return await self.async_step_domain_intro()
        return await self.async_step_concept_source()

    async def async_step_concept_source_back(self, user_input=None):
        """Return to the domain's concept chooser without clearing mappings."""
        return await self.async_step_domain_intro()

    async def async_step_remove_domain(self, user_input=None):
        domain_id = str(self._selected_domain_id or "")
        if user_input is not None:
            if bool(user_input.get("confirm_remove", False)):
                if not self._configuration_is_current():
                    return self.async_abort(reason="configuration_changed")
                cleaned = remove_domain_configuration(self._base_config, domain_id=domain_id)
                return self.async_create_entry(title="", data=cleaned)
            return await self.async_step_remove_domain_select()

        return self.async_show_form(
            step_id="remove_domain",
            data_schema=vol.Schema({vol.Required("confirm_remove", default=False): bool}),
            description_placeholders={
                "domain": _domain_label(domain_id),
                "effect": "Removes only this domain's Foundation mappings and device selections. Other domains remain unchanged.",
            },
        )

    async def async_step_review(self, user_input=None):
        if self._selected_domain_id == FOUNDATION_SETTINGS:
            return await self.async_step_foundation_review(user_input)
        if user_input is not None:
            return self._finish_flow()

        domain_id = str(self._selected_domain_id or "")
        lines: list[str] = []
        review_required: list[str] = []
        for mapping in self._concept_mappings.values():
            if not isinstance(mapping, dict) or str(mapping.get("domain") or "") != domain_id:
                continue
            concept = str(mapping.get("concept") or "")
            label = str(mapping.get("display_name") or concept)
            integration = str(mapping.get("integration_domain") or "")
            if not integration:
                continue
            selection = self._device_selections.get(
                concept_target_id(domain_id, concept, integration), {}
            )
            source_label = f"{label} / {_integration_label(integration)}"
            if selection.get("selection_origin") == "integration_scope_no_devices":
                detail = "integration scope (no separate HA devices)"
            elif selection.get("device_filter_mode") == "all_matching":
                detail = "all matching devices or resources"
                review_required.append(source_label)
            elif selection.get("device_filter_mode") == "specific_devices":
                choices = _candidate_devices(self.hass, integration)
                selected = [
                    f"{choices[device_id]} ({device_id})"
                    if device_id in choices else f"{device_id} (currently unavailable)"
                    for device_id in selection.get("selected_device_ids", [])
                ]
                detail = ", ".join(selected) or "no devices selected"
            else:
                detail = "device scope unresolved"
            lines.append(f"- {source_label}: {detail}")
        lines.sort()
        surface_lines: list[str] = []
        for item in self._configuration_surface_selections.values():
            if not isinstance(item, dict) or str(item.get("domain") or "") != domain_id:
                continue
            name = str(item.get("display_name") or item.get("object_type") or item.get("surface_id"))
            fields = item.get("fields") or {}
            mappings = []
            for field_id, configured in sorted(fields.items()):
                if not isinstance(configured, dict):
                    continue
                source = configured.get("source_identity") or {}
                entity_id = str(source.get("current_entity_id") or "unresolved")
                registry_id = source.get("entity_registry_id")
                if registry_id:
                    current = entity_source_selection(self.hass, str(registry_id))
                    identity = (current or {}).get("source_identity") or {}
                    if identity.get("entity_registry_id") == registry_id:
                        entity_id = str(identity["current_entity_id"])
                    else:
                        entity_id += " (currently unavailable)"
                mappings.append(f"{field_id} → {entity_id}")
            surface_lines.append(
                f"- {name}: " + (", ".join(mappings) if mappings else "no entity mappings")
            )
        summary = "\n".join(
            [
                f"Domain: {_domain_label(domain_id)}",
                *(lines or ["- No integration mappings"]),
                "",
                "Custom logical device mappings:",
                *(surface_lines or ["- None"]),
                "",
                "Broad scopes explicitly selected (all matching):",
                *([f"- {item}" for item in review_required] or ["- None"]),
                "",
                "Only this domain slice will be committed. Other Foundation domain configuration remains unchanged.",
            ]
        )
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={"review_summary": summary},
        )

    async def async_step_foundation_review(self, user_input=None):
        if user_input is not None:
            return self._finish_flow()
        technical = [f"- {_integration_label(item)}" for item in sorted(self._technical_selections)]
        summary = "\n".join(
            [
                f"Developer diagnostics: {'enabled' if self._developer_mode else 'disabled'}",
                "Technical observation integrations:",
                *(technical or ["- None"]),
                "",
                "Domain mappings are not changed by this global Foundation settings transaction.",
            ]
        )
        return self.async_show_form(
            step_id="foundation_review",
            data_schema=vol.Schema({}),
            description_placeholders={"review_summary": summary},
        )

    def _configuration_is_current(self) -> bool:
        """Reject stale transactions, including concurrent producer removal."""
        current = effective_entry_configuration(
            self.config_entry.data, self.config_entry.options
        )
        return current == self._base_config

    def _finish_flow(self):
        if not self._configuration_is_current():
            return self.async_abort(reason="configuration_changed")
        previous_revision = int(self._base_config.get("configuration_revision", 0) or 0)
        if self._selected_domain_id == FOUNDATION_SETTINGS:
            return self.async_create_entry(
                title="",
                data=self._payload(previous_revision),
            )
        domain_id = str(self._selected_domain_id or "")
        edited = self._payload(previous_revision)
        merged = merge_domain_configuration(
            self._base_config,
            edited,
            domain_id=domain_id,
            next_revision=previous_revision,
        )
        # Reviewing an unchanged domain is not a structural configuration change.
        # Preserve the exact persisted overlay (including legacy partial options).
        if merged == self._base_config:
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        merged["configuration_revision"] = previous_revision + 1
        return self.async_create_entry(title="", data=merged)


RhiFoundationConfigFlow = _WizardConfigFlow
RhiFoundationConfigFlow.async_step_user = _scoped_initial_user
RhiFoundationConfigFlow.async_get_options_flow = staticmethod(
    lambda config_entry: RhiFoundationOptionsFlow()
)
