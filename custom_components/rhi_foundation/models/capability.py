"""Typed technical capability models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .source_identity import SourceIdentity


@dataclass(frozen=True, slots=True)
class TechnicalCapability:
    capability_class: str
    value_type: Literal["number", "string", "boolean", "timestamp", "service", "unknown"]
    writable: bool
    device_class: str | None = None
    state_class: str | None = None
    native_unit: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    provenance: tuple[str, ...]
    name_hints: tuple[str, ...] = ()
    name_hints_supporting_only: Literal[True] = True


@dataclass(frozen=True, slots=True)
class CapabilityQuality:
    technical_match_confidence: Literal["low", "medium", "high"]
    availability: Literal["available", "temporarily_unavailable", "missing", "unknown"]
    ambiguity: Literal["none", "possible", "multiple_equal_matches"]


@dataclass(frozen=True, slots=True)
class FoundationCapabilityCandidate:
    candidate_id: str
    candidate_revision: int
    source_identity: SourceIdentity
    technical_capability: TechnicalCapability
    evidence: CapabilityEvidence
    quality: CapabilityQuality
    kind: Literal["foundation_capability_candidate"] = "foundation_capability_candidate"
    contract_version: Literal["1.1.0"] = "1.1.0"

    def validate(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.candidate_revision < 1:
            raise ValueError("candidate_revision must be >= 1")
        if not self.evidence.provenance:
            raise ValueError("evidence.provenance must not be empty")
        if self.evidence.name_hints and not self.evidence.name_hints_supporting_only:
            raise ValueError("name hints are supporting evidence only")
