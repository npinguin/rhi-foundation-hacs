"""Generic optional configuration-surface wizard handler."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

from .configured_surfaces import (
    minimum_satisfied,
    surface_fingerprint,
    surface_selection_key,
)
from .wizard_candidates import compatible_entity_ids, entity_source_selection

class ConfigurationSurfaceWizardMixin:
    """Render domain-owned configuration descriptors without domain semantics."""

    _configuration_surface_selections: dict[str, dict[str, Any]]
    _surface_queue: list[dict[str, Any]]
    _surface_index: int
    _active_surface_instance_id: str | None

    def _prepare_surface_queue(self) -> None:
        concept = self._current_concept()
        self._surface_queue = [dict(item) for item in concept.get("configuration_surfaces", [])]
        self._surface_index = 0
        self._active_surface_instance_id = None

    def _current_surface(self) -> dict[str, Any]:
        return self._surface_queue[self._surface_index]

    def _surface_instances(self, surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
        concept = self._current_concept()
        result: dict[str, dict[str, Any]] = {}
        for key, value in self._configuration_surface_selections.items():
            if not isinstance(value, dict):
                continue
            if (
                str(value.get("domain") or "") == str(concept["domain"])
                and str(value.get("concept") or "") == str(concept["concept"])
                and str(value.get("surface_id") or "") == str(surface.get("surface_id") or "")
            ):
                result[key] = value
        return result

    async def async_step_concept_surfaces(self, user_input=None):
        """Render the next configured surface without encoding navigation as data."""
        if self._surface_index >= len(self._surface_queue):
            self._concept_index += 1
            return await self.async_step_concept_intro()

        surface = self._current_surface()
        instances = self._surface_instances(surface)
        cardinality = str(surface.get("cardinality") or "")

        # A singleton is not an action menu. Enter its fields directly; the
        # native HA form submit is the forward/save action.
        if cardinality == "singleton":
            self._active_surface_instance_id = "singleton"
            return await self.async_step_surface_fields()

        options: dict[str, str] = {
            "surface_add": "Add logical device",
            "surface_continue": "Next",
            "surface_back": "Back",
        }
        if instances:
            options["surface_edit"] = "Edit logical device"
            options["surface_remove"] = "Remove logical device"
        return self.async_show_menu(
            step_id="concept_surfaces",
            menu_options=options,
            description_placeholders=self._surface_placeholders(surface, instances),
        )

    async def async_step_surface_add(self, user_input=None):
        self._active_surface_instance_id = uuid4().hex[:12]
        return await self.async_step_surface_fields()

    async def async_step_surface_continue(self, user_input=None):
        self._surface_index += 1
        return await self.async_step_concept_surfaces()

    async def async_step_surface_back(self, user_input=None):
        return await self.async_step_concept_source()

    async def async_step_surface_edit(self, user_input=None):
        surface = self._current_surface()
        instances = self._surface_instances(surface)
        if not instances:
            return await self.async_step_concept_surfaces()
        if user_input is not None:
            selected_key = str(user_input.get("instance_key") or "")
            if selected_key in instances:
                self._active_surface_instance_id = str(
                    instances[selected_key].get("instance_id") or ""
                )
                return await self.async_step_surface_fields()
            return self.async_show_form(
                step_id="surface_edit",
                data_schema=self._surface_instance_schema(instances),
                errors={"base": "select_instance"},
                description_placeholders=self._surface_placeholders(surface, instances),
            )
        return self.async_show_form(
            step_id="surface_edit",
            data_schema=self._surface_instance_schema(instances),
            description_placeholders=self._surface_placeholders(surface, instances),
        )

    async def async_step_surface_remove(self, user_input=None):
        surface = self._current_surface()
        instances = self._surface_instances(surface)
        if not instances:
            return await self.async_step_concept_surfaces()
        if user_input is not None:
            selected_key = str(user_input.get("instance_key") or "")
            if selected_key in instances:
                self._configuration_surface_selections.pop(selected_key, None)
                return await self.async_step_concept_surfaces()
            return self.async_show_form(
                step_id="surface_remove",
                data_schema=self._surface_instance_schema(instances),
                errors={"base": "select_instance"},
                description_placeholders=self._surface_placeholders(surface, instances),
            )
        return self.async_show_form(
            step_id="surface_remove",
            data_schema=self._surface_instance_schema(instances),
            description_placeholders=self._surface_placeholders(surface, instances),
        )

    @staticmethod
    def _surface_instance_schema(
        instances: dict[str, dict[str, Any]],
    ) -> vol.Schema:
        choices = {
            key: str(value.get("display_name") or value.get("instance_id") or key)
            for key, value in instances.items()
        }
        return vol.Schema({vol.Required("instance_key"): vol.In(choices)})

    def _surface_placeholders(
        self,
        surface: dict[str, Any],
        instances: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        concept = self._current_concept()
        names = [
            str(item.get("display_name") or item.get("instance_id") or key)
            for key, item in instances.items()
        ]
        return {
            "domain": str(concept.get("domain_presentation", {}).get("display_name") or concept["domain"]),
            "concept": str(concept.get("label") or concept["concept"]),
            "surface": str(surface.get("display_name") or surface.get("object_type") or surface.get("surface_id")),
            "cardinality": str(surface.get("cardinality") or ""),
            "configured_count": str(len(instances)),
            "configured_instances": ", ".join(names) or "None",
        }

    async def async_step_surface_fields(self, user_input=None):
        surface = self._current_surface()
        concept = self._current_concept()
        instance_id = str(self._active_surface_instance_id or "")
        key = surface_selection_key(
            str(concept["domain"]),
            str(concept["concept"]),
            str(surface["surface_id"]),
            instance_id,
        )
        existing = self._configuration_surface_selections.get(key, {})
        fields = list(surface.get("fields") or [])

        if user_input is not None:
            selected_fields = {
                str(field.get("field_id"))
                for field in fields
                if user_input.get(str(field.get("field_id")))
            }
            if not minimum_satisfied(surface.get("minimum"), selected_fields):
                return self.async_show_form(
                    step_id="surface_fields",
                    data_schema=self._surface_field_schema(surface, existing),
                    errors={"base": "minimum_not_satisfied"},
                    description_placeholders=self._surface_placeholders(
                        surface, self._surface_instances(surface)
                    ),
                )

            mapped_fields: dict[str, Any] = {}
            for field in fields:
                field_id = str(field.get("field_id") or "")
                entity_id = str(user_input.get(field_id) or "")
                if not entity_id:
                    continue
                source = entity_source_selection(self.hass, entity_id)
                if source is None:
                    return self.async_show_form(
                        step_id="surface_fields",
                        data_schema=self._surface_field_schema(surface, existing),
                        errors={"base": "entity_unavailable"},
                        description_placeholders=self._surface_placeholders(
                            surface, self._surface_instances(surface)
                        ),
                    )
                mapped_fields[field_id] = source

            display_name = str(
                user_input.get("instance_name")
                or existing.get("display_name")
                or surface.get("display_name")
                or surface.get("object_type")
                or instance_id
            )
            self._configuration_surface_selections[key] = {
                "selection_kind": "configured_domain_surface",
                "domain": str(concept["domain"]),
                "concept": str(concept["concept"]),
                "surface_id": str(surface["surface_id"]),
                "object_type": str(surface["object_type"]),
                "instance_id": instance_id,
                "cardinality": str(surface["cardinality"]),
                "display_name": display_name,
                "surface_fingerprint": surface_fingerprint(surface),
                "fields": mapped_fields,
                "creates_binding": False,
                "creates_runtime_truth": False,
                "creates_public_contract": False,
                "executes_commands": False,
            }
            if str(surface.get("cardinality") or "") == "singleton":
                self._surface_index += 1
            return await self.async_step_concept_surfaces()

        return self.async_show_form(
            step_id="surface_fields",
            data_schema=self._surface_field_schema(surface, existing),
            description_placeholders=self._surface_placeholders(
                surface, self._surface_instances(surface)
            ),
        )

    def _surface_field_schema(
        self,
        surface: dict[str, Any],
        existing: dict[str, Any],
    ) -> vol.Schema:
        schema: dict[Any, Any] = {}
        if str(surface.get("cardinality") or "") == "multiple":
            schema[vol.Optional(
                "instance_name",
                default=str(existing.get("display_name") or surface.get("display_name") or ""),
            )] = str

        existing_fields = existing.get("fields") or {}
        for field in surface.get("fields") or []:
            field_id = str(field.get("field_id") or "")
            compatible = compatible_entity_ids(self.hass, field)
            previous = (
                (existing_fields.get(field_id) or {})
                .get("source_identity", {})
                .get("current_entity_id")
            )
            config = selector.EntitySelectorConfig(
                include_entities=compatible or None,
                multiple=False,
            )
            marker = vol.Optional(field_id, default=previous) if previous else vol.Optional(field_id)
            schema[marker] = selector.EntitySelector(config)

        return vol.Schema(schema)
