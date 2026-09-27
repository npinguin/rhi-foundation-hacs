"""Validation for optional domain-owned configuration surfaces."""
from __future__ import annotations

from typing import Any

from .const import SOURCE_KINDS

_ALLOWED_CARDINALITY = {"singleton", "multiple"}
_ALLOWED_MATERIALIZATION = {"discovered", "configured", "derived", "aggregate"}
_EXECUTABLE_KEYS = {"formula", "expression", "script", "jinja", "callback"}


def validate_configuration_surfaces(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["invalid:configuration_surfaces"]

    issues: list[str] = []
    seen: set[str] = set()
    for surface in value:
        if not isinstance(surface, dict):
            issues.append("invalid:configuration_surface")
            continue

        sid = str(surface.get("surface_id") or "")
        if not sid:
            issues.append("invalid:configuration_surface_id")
            continue
        if sid in seen:
            issues.append(f"duplicate_configuration_surface_id:{sid}")
        seen.add(sid)

        if surface.get("surface_type") != "logical_device_mapping":
            issues.append(f"invalid:configuration_surface_type:{sid}")
        if not str(surface.get("object_type") or ""):
            issues.append(f"invalid:configuration_surface_object_type:{sid}")
        if surface.get("cardinality") not in _ALLOWED_CARDINALITY:
            issues.append(f"invalid:configuration_surface_cardinality:{sid}")

        materialization = surface.get("materialization") or []
        if (
            not isinstance(materialization, list)
            or not materialization
            or any(item not in _ALLOWED_MATERIALIZATION for item in materialization)
            or "configured" not in materialization
            or any(item in {"derived", "aggregate"} for item in materialization)
        ):
            issues.append(f"invalid:configuration_surface_materialization:{sid}")

        for key in _EXECUTABLE_KEYS:
            if key in surface:
                issues.append(f"invalid:configuration_surface_executable_field:{sid}:{key}")

        fields = surface.get("fields")
        declared: set[str] = set()
        if not isinstance(fields, list) or not fields:
            issues.append(f"invalid:configuration_surface_fields:{sid}")
        else:
            for field in fields:
                if not isinstance(field, dict):
                    issues.append(f"invalid:configuration_surface_field:{sid}")
                    continue
                fid = str(field.get("field_id") or "")
                if not fid:
                    issues.append(f"invalid:configuration_surface_field_id:{sid}")
                    continue
                if fid in declared:
                    issues.append(f"duplicate_configuration_surface_field:{sid}:{fid}")
                declared.add(fid)
                if not str(field.get("display_name") or "").strip():
                    issues.append(f"invalid:configuration_surface_field_name:{sid}:{fid}")
                kinds = field.get("allowed_source_kinds") or []
                if not isinstance(kinds, list) or not kinds or any(kind not in SOURCE_KINDS for kind in kinds):
                    issues.append(f"invalid:configuration_surface_field_source_kind:{sid}:{fid}")
                caps = field.get("technical_capabilities") or []
                if not isinstance(caps, list) or not caps:
                    issues.append(f"invalid:configuration_surface_field_capability:{sid}:{fid}")
                for key in _EXECUTABLE_KEYS:
                    if key in field:
                        issues.append(f"invalid:configuration_surface_executable_field:{sid}:{key}")

        minimum = surface.get("minimum")
        if minimum is None:
            continue
        if not isinstance(minimum, dict) or not minimum or set(minimum) - {"all_of", "any_of"}:
            issues.append(f"invalid:configuration_surface_minimum:{sid}")
            continue

        refs: list[str] = []
        if "all_of" in minimum:
            values = minimum["all_of"]
            if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
                issues.append(f"invalid:configuration_surface_minimum:{sid}")
            else:
                refs.extend(values)
        if "any_of" in minimum:
            groups = minimum["any_of"]
            if (
                not isinstance(groups, list)
                or not groups
                or any(not isinstance(group, list) or not group or any(not isinstance(item, str) for item in group) for group in groups)
            ):
                issues.append(f"invalid:configuration_surface_minimum:{sid}")
            else:
                refs.extend(item for group in groups for item in group)

        for fid in refs:
            if fid not in declared:
                issues.append(f"invalid:configuration_surface_minimum_field:{sid}:{fid}")

    return issues
