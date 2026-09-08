"""Rename-safe candidate identity generation."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from ..models.source_identity import SourceIdentity


def _stable_source_payload(source: SourceIdentity) -> dict[str, object]:
    data = asdict(source)
    # current_entity_id is mutable runtime/display resolution and never part of identity.
    data.pop("current_entity_id", None)
    return data


def build_candidate_id(source: SourceIdentity, capability_class: str) -> str:
    payload = {
        "source": _stable_source_payload(source),
        "capability_class": capability_class,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"candidate:{source.source_kind}:{digest}:{capability_class}"
