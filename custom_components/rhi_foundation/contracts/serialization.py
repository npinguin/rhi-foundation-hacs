"""Safe JSON-compatible shared-contract serialization."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..models.capability import FoundationCapabilityCandidate
from ..models.catalog import FoundationCapabilityCatalog


SAFETY = {
    "creates_binding": False,
    "creates_runtime_truth": False,
    "creates_public_contract": False,
    "executes_commands": False,
}


def _jsonable(value: Any) -> Any:
    """Convert dataclass-native tuples/containers into JSON contract shapes."""
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def candidate_to_contract(
    candidate: FoundationCapabilityCandidate,
) -> dict[str, Any]:
    candidate.validate()
    data = _jsonable(asdict(candidate))
    data["safety"] = SAFETY.copy()
    return data


def catalog_to_contract(
    catalog: FoundationCapabilityCatalog,
) -> dict[str, Any]:
    return {
        "kind": catalog.kind,
        "contract_version": catalog.contract_version,
        "catalog_revision": catalog.catalog_revision,
        "generated_at": catalog.generated_at,
        "execution_model": catalog.execution_model,
        "candidates": [candidate_to_contract(c) for c in catalog.candidates],
    }
