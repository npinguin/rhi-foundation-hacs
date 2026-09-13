"""Domain-scoped configuration entrypoint for RHI Foundation V2.

The concept/device wizard implementation lives in ``wizard`` so lifecycle scoping can
remain small and explicit without duplicating proven matching UI logic.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_CONCEPT_MAPPINGS, CONF_DEVELOPER_MODE
from .domain_config import (
    configured_domains,
    merge_domain_configuration,
    remove_domain_configuration,
)
from .wizard import (
    FoundationWizardMixin,
    RhiFoundationConfigFlow as _WizardConfigFlow,
    _domain_label,
    _domain_records,
    _integration_label,
)
from .wizard_state import concept_target_id

ACTION_CONFIGURE = "configure"
ACTION_REMOVE = "remove"
FOUNDATION_SETTINGS = "__foundation_settings__"


def _domain_is_available(record: dict[str, Any]) -> bool:
    return any(bool(concept.get("specs")) for concept in record.get("concepts", []))


def _domain_choices(records: list[dict[str, Any]], mappings: dict[str, Any]) -> dict[str, str]:
    configured = configured_domains({CONF_CONCEPT_MAPPINGS: mappings})
    choices: dict[str, str] = {}
    for record in records:
        domain_id = str(record["domain"])
        state = ["configured" if domain_id in configured else "not configured"]
        state.append("provider available" if _domain_is_available(record) else "provider unavailable")
        choices[domain_id] = f"{record['label']} — {', '.join(state)}"
    return choices


async def _scoped_initial_user(self: _WizardConfigFlow, user_input=None):
    """Configure one domain on first Foundation setup instead of all domains."""
    if not getattr(self, "_initialized", False):
        self._initialize_state()

    records = _domain_records(self.hass, self._concept_mappings)
    if not records:
        return await self.async_step_no_configurable_domains()

    choices = _domain_choices(records, self._concept_mappings)
    if user_input is not None:
        domain_id = str(user_input.get("domain_id") or "")
        selected = next((item for item in records if str(item["domain"]) == domain_id), None)
        if selected is not None:
            self._domain_queue = [selected]
            self._domain_index = 0
            self._developer_mode = False
            return await self.async_step_domain_intro()

    return self.async_show_form(
        step_id="user",
        data_schema=vol.Schema({vol.Required("domain_id"): vol.In(choices)}),
        description_placeholders={
            "domain_count": str(len(records)),
            "ownership": "Choose one domain. Foundation configures technical intent only; domain semantics remain owned by that domain.",
        },
    )


class RhiFoundationOptionsFlow(FoundationWizardMixin, config_entries.OptionsFlow):
    """Configure, reconfigure or remove exactly one Foundation-owned scope."""

    _base_config: dict[str, Any]
    _selected_domain_id: str | None
    _records: list[dict[str, Any]]

    async def async_step_init(self, user_input=None):
        if not getattr(self, "_initialized", False):
            defaults = dict(self.config_entry.data)
            defaults.update(dict(self.config_entry.options))
            self._base_config = defaults
            self._selected_domain_id = None
            self._initialize_state(defaults)
        self._records = _domain_records(self.hass, self._concept_mappings)
        return await self.async_step_domain_select(user_input)

    async def async_step_domain_select(self, user_input=None):
        choices = _domain_choices(self._records, self._concept_mappings)
        choices[FOUNDATION_SETTINGS] = "Foundation technical settings — global diagnostics only"
        configured = configured_domains(self._base_config)
        errors: dict[str, str] = {}

        if user_input is not None:
            domain_id = str(user_input.get("domain_id") or "")
            action = str(user_input.get("domain_action") or ACTION_CONFIGURE)
            if domain_id == FOUNDATION_SETTINGS:
                if action == ACTION_REMOVE:
                    errors["base"] = "foundation_settings_cannot_be_removed"
                else:
                    self._selected_domain_id = FOUNDATION_SETTINGS
                    return await self.async_step_foundation_settings()
            else:
                selected = next(
                    (item for item in self._records if str(item["domain"]) == domain_id),
                    None,
                )
                if selected is None:
                    errors["base"] = "invalid_domain"
                elif action == ACTION_REMOVE:
                    if domain_id not in configured:
                        errors["base"] = "domain_not_configured"
                    else:
                        self._selected_domain_id = domain_id
                        return await self.async_step_remove_domain()
                else:
                    self._selected_domain_id = domain_id
                    self._domain_queue = [selected]
                    self._domain_index = 0
                    return await self.async_step_domain_intro()

        return self.async_show_form(
            step_id="domain_select",
            data_schema=vol.Schema(
                {
                    vol.Required("domain_id"): vol.In(choices),
                    vol.Required("domain_action", default=ACTION_CONFIGURE): vol.In(
                        {
                            ACTION_CONFIGURE: "Configure / reconfigure selected scope",
                            ACTION_REMOVE: "Remove selected domain configuration from Foundation",
                        }
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "domain_count": str(len(self._records)),
                "ownership": "Choose one domain. Only that domain is changed. Foundation technical settings are a separate global diagnostics scope.",
            },
        )

    async def async_step_foundation_settings(self, user_input=None):
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
        """Run the proven concept wizard but stop after the selected domain."""
        if self._domain_index >= len(self._domain_queue):
            return await self.async_step_review()
        return await super().async_step_domain_intro(user_input)

    async def async_step_remove_domain(self, user_input=None):
        domain_id = str(self._selected_domain_id or "")
        if user_input is not None:
            if bool(user_input.get("confirm_remove", False)):
                cleaned = remove_domain_configuration(self._base_config, domain_id=domain_id)
                return self.async_create_entry(title="", data=cleaned)
            return await self.async_step_domain_select()

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
        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        review_required: list[str] = []
        for mapping in self._concept_mappings.values():
            if not isinstance(mapping, dict) or str(mapping.get("domain") or "") != domain_id:
                continue
            concept = str(mapping.get("concept") or "")
            label = str(mapping.get("display_name") or concept)
            integration = str(mapping.get("integration_domain") or "")
            if not integration:
                continue
            grouped[(concept, label)].append(integration)
            selection = self._device_selections.get(
                concept_target_id(domain_id, concept, integration), {}
            )
            if selection.get("device_filter_mode") == "all_matching":
                review_required.append(f"{label} / {_integration_label(integration)}")

        lines = [
            f"- {label}: " + ", ".join(_integration_label(i) for i in sorted(integrations))
            for (_concept, label), integrations in sorted(grouped.items())
        ]
        summary = "\n".join(
            [
                f"Domain: {_domain_label(domain_id)}",
                *(lines or ["- Not configured"]),
                "",
                "Needs review because all matching devices were selected:",
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

    def _finish_flow(self):
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
        )
        return self.async_create_entry(title="", data=merged)


RhiFoundationConfigFlow = _WizardConfigFlow
RhiFoundationConfigFlow.async_step_user = _scoped_initial_user
RhiFoundationConfigFlow.async_get_options_flow = staticmethod(
    lambda config_entry: RhiFoundationOptionsFlow()
)
