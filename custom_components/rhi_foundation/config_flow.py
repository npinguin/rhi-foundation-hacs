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
            self._enable_ux = bool(user_input.get("enable_ux", getattr(self, "_enable_ux", True)))
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
                {
                    vol.Required(CONF_DEVELOPER_MODE, default=self._developer_mode): bool,
                    vol.Required("enable_ux", default=getattr(self, "_enable_ux", True)): bool,
                }
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
                f"Optional engineering dashboard: {'enabled' if getattr(self, "_enable_ux", True) else 'disabled'}",
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
            edited = self._payload(previous_revision)
            # Unchanged global review must not trigger structural reconciliation.
            # Preserve the exact existing options overlay, including legacy data.
            if all(
                edited.get(key) == self._base_config.get(key)
                for key in edited if key != "configuration_revision"
            ):
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
            return self.async_create_entry(title="", data=edited)
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


async def apply_engineering_selection(
    hass: Any, entry: Any, *,
    domain_id: str, expected_revision: int, changes: list[dict[str, Any]],
    confirm_all: bool = False,
) -> dict[str, Any]:
    """Commit scoped selections to the EXISTING Foundation config entry.

    Uses the same wizard validators and domain_config merge transaction as the
    native Options Flow. One HA entry, no parallel store, no domain command path.
    Concurrent edits fail closed. Unsupported/ambiguous kinds remain native
    Options Flow responsibilities rather than guessed bindings.
    """
    from .const import CONF_CONFIGURATION_REVISION, CONF_DEVICE_SELECTIONS
    from .wizard import (
        ALL_MATCHING, FILTER_ALL, FILTER_SPECIFIC, _candidate_devices,
        _installed_integrations,
    )
    from .shared_registry import get_framework_resource_provider
    from .wizard_state import (
        mapping_key, normalize_device_selection, concept_target_id,
    )
    from .configured_surfaces import (
        surface_selection_key, surface_fingerprint, minimum_satisfied,
        technical_matches_field,
    )
    from .wizard_candidates import entity_source_selection

    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise ValueError("invalid_change_count")
    current = effective_entry_configuration(entry.data, entry.options)
    revision = int(current.get(CONF_CONFIGURATION_REVISION, 0) or 0)
    if type(expected_revision) is not int or revision != expected_revision:
        raise ValueError("stale_configuration_revision")
    records = _domain_records(
        hass, current.get(CONF_CONCEPT_MAPPINGS) or {},
        current.get(CONF_CONFIGURATION_SURFACE_SELECTIONS) or {},
    )
    module = next((item for item in records if item["domain"] == domain_id), None)
    if module is None:
        raise ValueError("unknown_module")
    concepts = {str(c["concept"]): c for c in module["concepts"]}
    edited = deepcopy(current)
    maps = edited.setdefault(CONF_CONCEPT_MAPPINGS, {})
    devices = edited.setdefault(CONF_DEVICE_SELECTIONS, {})
    surfaces = edited.setdefault(CONF_CONFIGURATION_SURFACE_SELECTIONS, {})
    seen: set[tuple[str, ...]] = set()
    modified = False
    affected_surfaces: set[str] = set()
    # An explicit Save may switch between source paths, but never select
    # an integration AND an entity surface in the same submitted concept.
    selected_paths: dict[str, set[str]] = {}
    for proposed in changes:
        if isinstance(proposed, dict) and proposed.get("selected") is True:
            selected_paths.setdefault(str(proposed.get("concept_id") or ""), set()).add(
                "surface" if proposed.get("source_kind") == "entity" else
                str(proposed.get("source_kind") or "")
            )
    if any(len(kinds) > 1 for kinds in selected_paths.values()):
        raise ValueError("conflicting_source_paths")

    for change in changes:
        if not isinstance(change, dict) or type(change.get("selected")) is not bool:
            raise ValueError("invalid_selection")
        concept_id = str(change.get("concept_id") or "")
        concept = concepts.get(concept_id)
        if concept is None:
            raise ValueError("unpublished_concept")
        kind = str(change.get("source_kind") or "")
        source_id = str(change.get("source_id") or "")
        # One physical entity may legitimately fill two distinct published
        # fields. Uniqueness is the logical field target, not the entity name.
        token = ((concept_id, kind, str(change.get("surface_id") or ""),
                  str(change.get("field_id") or "")) if kind == "entity"
                 else (concept_id, kind, source_id))
        if not source_id or token in seen:
            raise ValueError("duplicate_or_missing_source")
        seen.add(token)
        selected = change["selected"]

        if kind == "integration":
            key = mapping_key(domain_id, concept_id, source_id)
            target = concept_target_id(domain_id, concept_id, source_id)
            previous = maps.get(key)
            if not selected:
                if key in maps:
                    maps.pop(key)
                    devices.pop(target, None)
                    modified = True
                continue
            builder = concept.get("builders", {}).get(source_id)
            if builder is None or (source_id not in _installed_integrations(hass)
                                   and get_framework_resource_provider(hass, source_id) is None):
                raise ValueError("source_unavailable")
            published = builder.get("specification") or {}
            resource_types = {
                str(s.get("resource_type")) for s in published.get("supported_sources") or []
                if isinstance(s, dict) and s.get("resource_type")
                and s.get("integration_domain") == source_id
            }
            available = _candidate_devices(hass, source_id, resource_types)
            mode = str(change.get("device_filter_mode") or "")
            selected_devices = change.get("selected_device_ids") or []
            if not isinstance(selected_devices, list) or len(selected_devices) > 200:
                raise ValueError("invalid_device_selection")
            if not available and (resource_types or
                                  get_framework_resource_provider(hass, source_id) is not None):
                raise ValueError("framework_scope_unavailable")
            if not available and not resource_types:
                # Existing explicit device intent must survive a temporary
                # disappearance. The HA-native wizard preserves it; the inline
                # editor must never replace it with a broader All scope.
                previous_scope = devices.get(target) or {}
                if previous_scope and (
                    previous_scope.get("selection_origin") != "integration_scope_no_devices"
                    or previous_scope.get("device_filter_mode") == FILTER_SPECIFIC
                ):
                    raise ValueError("existing_device_selection_unavailable")
                # HA integrations can expose direct entities without a DeviceEntry.
                # Exactly the existing wizard's documented integration scope.
                mode, chosen = FILTER_ALL, [ALL_MATCHING]
                selection_state = "configured"
                origin = "integration_scope_no_devices"
            else:
                if mode == FILTER_ALL and not confirm_all:
                    raise ValueError("all_scope_requires_explicit_confirmation")
                if mode not in (FILTER_ALL, FILTER_SPECIFIC):
                    raise ValueError("invalid_filter_mode")
                chosen, error = normalize_device_selection(
                    mode, selected_devices, set(available),
                    all_matching_token=ALL_MATCHING,
                )
                if error:
                    raise ValueError(error)
                selection_state = "review_required" if mode == FILTER_ALL else "configured"
                origin = "explicit_engineering_selection"
            new_mapping = {
                "selection_kind": "configured_domain_selection",
                "domain": domain_id, "concept": concept_id,
                "display_name": concept.get("label") or concept_id,
                "integration_domain": source_id,
                "builder_id": builder["builder_id"],
                "builder_version": builder["builder_version"],
                "publication_revision": builder["publication_revision"],
                "specification_fingerprint": builder["specification_fingerprint"],
                "creates_binding": False, "creates_runtime_truth": False,
                "creates_public_contract": False, "executes_commands": False,
            }
            new_selection = {
                "selection_kind": "configured_domain_selection",
                "integration_domain": source_id, "device_filter_mode": mode,
                "selected_device_ids": chosen, "selection_state": selection_state,
                "selection_origin": origin,
                "creates_binding": False, "creates_runtime_truth": False,
                "creates_public_contract": False, "executes_commands": False,
            }
            if previous != new_mapping or devices.get(target) != new_selection:
                maps[key], devices[target] = new_mapping, new_selection
                # Exactly the existing wizard's mutually exclusive source paths.
                for old_key, old in list(surfaces.items()):
                    if (isinstance(old, dict) and old.get("domain") == domain_id
                            and old.get("concept") == concept_id):
                        surfaces.pop(old_key)
                modified = True
        elif kind == "entity":
            surface_id = str(change.get("surface_id") or "")
            field_id = str(change.get("field_id") or "")
            descriptor = next(
                (s for s in concept.get("configuration_surfaces") or []
                 if s.get("surface_id") == surface_id), None
            )
            if not descriptor or descriptor.get("cardinality") != "singleton":
                raise ValueError("surface_requires_native_options_flow")
            field = next(
                (f for f in descriptor.get("fields") or []
                 if f.get("field_id") == field_id), None
            )
            if field is None or "entity" not in (field.get("allowed_source_kinds") or []):
                raise ValueError("unsupported_entity_source")
            skey = surface_selection_key(domain_id, concept_id, surface_id, "singleton")
            affected_surfaces.add(skey)
            existing = deepcopy(surfaces.get(skey) or {})
            if existing and existing.get("surface_fingerprint") != surface_fingerprint(descriptor):
                raise ValueError("surface_descriptor_changed")
            fieldmap = dict(existing.get("fields") or {})
            if selected:
                source = entity_source_selection(hass, source_id)
                if source is None or not technical_matches_field(
                    source["technical_capabilities"], source["native_unit"], field
                ):
                    raise ValueError("entity_unavailable")
                fieldmap[field_id] = source
            else:
                old = (fieldmap.get(field_id) or {}).get("source_identity") or {}
                if old.get("current_entity_id") != source_id:
                    raise ValueError("stale_entity_selection")
                fieldmap.pop(field_id, None)
            # Validate the complete edited surface once all staged fields are applied.
            if fieldmap:
                replacement = {
                    "selection_kind": "configured_domain_surface",
                    "domain": domain_id, "concept": concept_id,
                    "surface_id": surface_id, "object_type": descriptor["object_type"],
                    "instance_id": "singleton", "cardinality": "singleton",
                    "display_name": str(descriptor.get("display_name") or descriptor["object_type"]),
                    "surface_fingerprint": surface_fingerprint(descriptor),
                    "fields": fieldmap,
                    "creates_binding": False, "creates_runtime_truth": False,
                    "creates_public_contract": False, "executes_commands": False,
                }
                if surfaces.get(skey) != replacement:
                    surfaces[skey] = replacement
                    for old_key, mapping in list(maps.items()):
                        if (isinstance(mapping, dict) and mapping.get("domain") == domain_id
                                and mapping.get("concept") == concept_id):
                            maps.pop(old_key)
                            devices.pop(f"domain:{old_key}", None)
                    modified = True
            elif skey in surfaces:
                surfaces.pop(skey)
                modified = True
        else:
            raise ValueError("unsupported_selection_kind")
    # A surface's required fields are an atomic group. Multiple staged changes
    # may satisfy one all_of/any_of minimum in the SAME Save transaction.
    for skey in affected_surfaces:
        item = surfaces.get(skey)
        if not isinstance(item, dict) or item.get("domain") != domain_id:
            continue
        c = concepts.get(str(item.get("concept") or ""))
        descriptor = next(
            (v for v in (c or {}).get("configuration_surfaces", [])
             if v.get("surface_id") == item.get("surface_id")), None
        )
        if descriptor is not None and not minimum_satisfied(
            descriptor.get("minimum"), set(item.get("fields") or {})
        ):
            raise ValueError("minimum_not_satisfied")
    if not modified:
        return {"saved": False, "configuration_revision": revision}
    # One revision-guarded Foundation domain transaction, same source of truth
    # and merge semantics as RhiFoundationOptionsFlow._finish_flow.
    if int(effective_entry_configuration(entry.data, entry.options).get(
        CONF_CONFIGURATION_REVISION, 0
    ) or 0) != revision:
        raise ValueError("concurrent_configuration_change")
    merged = merge_domain_configuration(current, edited, domain_id=domain_id)
    hass.config_entries.async_update_entry(entry, options=merged)
    return {
        "saved": True, "configuration_revision": merged[CONF_CONFIGURATION_REVISION],
        "physical_readback": "NOT_VERIFIED",
    }
