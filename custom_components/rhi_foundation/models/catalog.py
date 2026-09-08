"""Foundation capability catalog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .capability import FoundationCapabilityCandidate


@dataclass(frozen=True, slots=True)
class FoundationCapabilityCatalog:
    catalog_revision: int
    candidates: tuple[FoundationCapabilityCandidate, ...]
    generated_at: str
    execution_model: Literal[
        "configuration_time_active_runtime_passive"
    ] = "configuration_time_active_runtime_passive"
    kind: Literal["foundation_capability_catalog"] = "foundation_capability_catalog"
    contract_version: Literal["1.1.0"] = "1.1.0"

    @classmethod
    def empty(cls, revision: int = 1) -> "FoundationCapabilityCatalog":
        return cls(
            catalog_revision=revision,
            candidates=(),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
