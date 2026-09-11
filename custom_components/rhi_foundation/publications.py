"""DomainBuildSpecification provider ingestion and validation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from typing import Any, Iterable

from .const import SAFETY, SOURCE_KINDS
from .shared_registry import iter_domain_build_specification_providers


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    publisher_domain: str
    publication_revision: int
    status: str
    specifications: tuple[dict[str, Any], ...]
    issues: tuple[str, ...]


def specification_fingerprint(specification: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(specification, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _provider_payload(entry: Any, registry_key: str) -> tuple[Any, str, int]:
    if isinstance(entry, dict) and "provider" in entry:
        provider = entry["provider"]
        publisher = str(entry.get("publisher_domain") or getattr(provider, "publisher_domain", registry_key))
        revision = int(entry.get("publication_revision") or getattr(provider, "publication_revision", 1))
        return provider, publisher, max(1, revision)
    provider = entry
    publisher = str(getattr(provider, "publisher_domain", registry_key))
    revision = int(getattr(provider, "publication_revision", 1))
    return provider, publisher, max(1, revision)


def _materialize(provider: Any) -> list[dict[str, Any]]:
    if hasattr(provider, "get_build_specifications"):
        payload = provider.get_build_specifications()
    elif hasattr(provider, "build_specifications"):
        payload = provider.build_specifications
    elif callable(provider):
        payload = provider()
    else:
        raise ValueError("provider_has_no_bounded_build_specification_surface")
    if inspect.isawaitable(payload):
        raise ValueError("provider_must_be_synchronous_and_bounded")
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, (list, tuple)):
        raise ValueError("provider_result_must_be_specification_or_sequence")
    result=[]
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("specification_must_be_object")
        result.append(dict(item))
    return result


def _validate_predicates(
    predicates: Any,
    *,
    input_id: Any,
    raw_capability_id: str,
    surface: str,
) -> list[str]:
    """Validate structural predicates and supporting hints with the same contract rules."""
    issues: list[str] = []
    if not isinstance(predicates, list):
        return [f"invalid:integration_match_{surface}:{input_id}:{raw_capability_id or 'missing'}"]
    allowed_fields = {
        "source_identity.unique_id", "source_identity.service_domain", "source_identity.service_name",
        "source_identity.action_domain", "source_identity.action_type", "source_identity.action_subtype",
        "source_identity.provider_key", "source_identity.api_capability_id", "source_identity.capability_key",
        "technical_capability.device_class", "technical_capability.state_class", "technical_capability.native_unit",
    }
    for condition in predicates:
        if not isinstance(condition, dict):
            issues.append(f"invalid:integration_match_{surface}_condition:{input_id}:{raw_capability_id or 'missing'}")
            continue
        field = str(condition.get("field") or "")
        operator = str(condition.get("operator") or "")
        value = str(condition.get("value") or "")
        if field not in allowed_fields:
            issues.append(f"invalid:integration_match_field:{input_id}:{field or 'missing'}")
        if operator not in {"equals", "starts_with", "ends_with", "contains"}:
            issues.append(f"invalid:integration_match_operator:{input_id}:{operator or 'missing'}")
        if not value:
            issues.append(f"invalid:integration_match_value:{input_id}:{raw_capability_id or 'missing'}")
    return issues


def validate_specification(spec: dict[str, Any], publisher_domain: str) -> list[str]:
    issues: list[str] = []
    required=("kind","contract_version","publisher","domain_id","builder_id","builder_version","concept","supported_sources","candidate_requirements","build_policy","safety")
    for key in required:
        if key not in spec:
            issues.append(f"missing:{key}")
    if issues:
        return issues
    if spec.get("kind") != "domain_build_specification": issues.append("invalid:kind")
    if spec.get("contract_version") != "1.2.0": issues.append("invalid:contract_version")
    if spec.get("publisher") != publisher_domain: issues.append("invalid:publisher_ownership")
    if not str(spec.get("domain_id") or ""): issues.append("invalid:domain_id")
    elif publisher_domain.startswith("rhi_") and str(spec.get("domain_id")) != publisher_domain[4:]: issues.append("invalid:domain_ownership")
    if not str(spec.get("builder_id") or ""): issues.append("invalid:builder_id")
    domain_presentation=spec.get("domain_presentation") or {}
    for field in ("display_name","description","selection_guidance"):
        if not str(domain_presentation.get(field) or "").strip(): issues.append(f"invalid:domain_presentation:{field}")
    concept=spec.get("concept") or {}
    for field in ("concept_id","display_name","description","selection_guidance"):
        if not str(concept.get(field) or "").strip(): issues.append(f"invalid:concept:{field}")
    sources=spec.get("supported_sources") or []
    if not isinstance(sources, list) or not sources:
        issues.append("invalid:supported_sources")
        supported_integrations: set[str] = set()
    elif any(not isinstance(item,dict) or not item.get("integration_domain") for item in sources):
        issues.append("invalid:supported_source")
        supported_integrations = {str(item.get("integration_domain")) for item in sources if isinstance(item, dict) and item.get("integration_domain")}
    else:
        supported_integrations = {str(item.get("integration_domain")) for item in sources}
    inputs=((spec.get("candidate_requirements") or {}).get("normalized_inputs") or [])
    if not isinstance(inputs,list): issues.append("invalid:normalized_inputs")
    else:
        seen_inputs=set()
        for item in inputs:
            if not isinstance(item,dict): issues.append("invalid:normalized_input"); continue
            input_id=item.get("input_id")
            if not input_id: issues.append("invalid:input_id")
            elif input_id in seen_inputs: issues.append(f"duplicate_input_id:{input_id}")
            else: seen_inputs.add(input_id)
            kinds=item.get("allowed_source_kinds") or []
            if not kinds or any(kind not in SOURCE_KINDS for kind in kinds): issues.append(f"invalid:allowed_source_kinds:{input_id}")
            caps=(item.get("technical_capabilities") or {}).get("any_of") or (item.get("technical_capabilities") or {}).get("all_of") or []
            if not caps: issues.append(f"invalid:technical_capabilities:{input_id}")
            if "required" not in item or not isinstance(item.get("required"),bool): issues.append(f"invalid:required:{input_id}")
            cardinality = item.get("cardinality")
            valid_cardinalities = {
                "one", "exactly_one", "one_per_system", "exactly_one_per_group",
                "zero_or_one", "zero_or_one_per_group",
                "one_or_more", "one_or_more_per_group",
                "zero_or_more", "zero_or_more_per_group",
            }
            if cardinality not in valid_cardinalities:
                issues.append(f"invalid:cardinality:{input_id}")
            integration_matches = item.get("integration_matches") or []
            if not isinstance(integration_matches, list) or not integration_matches:
                issues.append(f"invalid:integration_matches:{input_id}")
                continue
            for match in integration_matches:
                if not isinstance(match, dict):
                    issues.append(f"invalid:integration_match:{input_id}")
                    continue
                integration = str(match.get("integration_domain") or "")
                raw_capability_id = str(match.get("raw_capability_id") or "")
                source_kind = str(match.get("source_kind") or "")
                if not integration or integration not in supported_integrations:
                    issues.append(f"invalid:integration_match_integration:{input_id}:{integration or 'missing'}")
                if not raw_capability_id:
                    issues.append(f"invalid:raw_capability_id:{input_id}")
                if source_kind not in kinds:
                    issues.append(f"invalid:integration_match_source_kind:{input_id}:{source_kind or 'missing'}")

                # Contract 1.2.0 permits structural `all_of` to be empty when a
                # match is intentionally broad on technical capability and the
                # source-specific evidence is carried as supporting `hints`.
                # F1.7.4 incorrectly rejected that valid shape, making every
                # Mobility publication invalid after unique-id/service-name
                # predicates were demoted from authority to hints.
                conditions = match.get("all_of")
                hints = match.get("hints", [])
                if conditions is None:
                    issues.append(f"invalid:integration_match_conditions:{input_id}:{raw_capability_id or 'missing'}")
                    continue
                issues.extend(_validate_predicates(conditions, input_id=input_id, raw_capability_id=raw_capability_id, surface="condition"))
                issues.extend(_validate_predicates(hints, input_id=input_id, raw_capability_id=raw_capability_id, surface="hint"))
                if not conditions and not hints:
                    issues.append(f"invalid:integration_match_conditions:{input_id}:{raw_capability_id or 'missing'}")
    safety=spec.get("safety") or {}
    for key,value in SAFETY.items():
        if safety.get(key) is not value: issues.append(f"unsafe:{key}")
    return issues


def read_publications(hass: Any) -> tuple[list[PublicationRecord], list[dict[str, Any]]]:
    records: list[PublicationRecord] = []
    valid_specs: list[dict[str, Any]] = []
    global_builder_ids: set[str] = set()
    global_concepts: dict[tuple[str,str], str] = {}
    global_concept_labels: dict[tuple[str,str], str] = {}
    global_domain_presentations: dict[str, tuple[str,str,str]] = {}
    global_concept_presentations: dict[tuple[str,str], tuple[str,str,str]] = {}
    global_concept_integrations: dict[tuple[str,str,str], str] = {}

    for registry_key, entry in iter_domain_build_specification_providers(hass):
        issues: list[str] = []
        specs: list[dict[str, Any]] = []
        try:
            provider, publisher, revision = _provider_payload(entry, str(registry_key))
            specs = _materialize(provider)
            for spec in specs:
                issues.extend(validate_specification(spec, publisher))
            local_builders=[str(spec.get("builder_id")) for spec in specs if spec.get("builder_id")]
            if len(local_builders) != len(set(local_builders)):
                issues.append("duplicate_builder_id_within_provider")
            local_domain_presentations: dict[str, tuple[str,str,str]] = {}
            local_concept_presentations: dict[tuple[str,str], tuple[str,str,str]] = {}
            for spec in specs:
                domain=str(spec.get("domain_id") or "")
                concept=str((spec.get("concept") or {}).get("concept_id") or "")
                dp=spec.get("domain_presentation") or {}
                domain_presentation=(str(dp.get("display_name") or ""),str(dp.get("description") or ""),str(dp.get("selection_guidance") or ""))
                if domain in local_domain_presentations and local_domain_presentations[domain] != domain_presentation:
                    issues.append(f"domain_presentation_drift:{domain}")
                else:
                    local_domain_presentations[domain]=domain_presentation
                c=spec.get("concept") or {}
                concept_presentation=(str(c.get("display_name") or ""),str(c.get("description") or ""),str(c.get("selection_guidance") or ""))
                key=(domain,concept)
                if key in local_concept_presentations and local_concept_presentations[key] != concept_presentation:
                    issues.append(f"concept_presentation_drift:{domain}.{concept}")
                else:
                    local_concept_presentations[key]=concept_presentation
            local_concept_integrations: set[tuple[str,str,str]] = set()
            for spec in specs:
                builder=str(spec.get("builder_id") or "")
                domain=str(spec.get("domain_id") or "")
                concept=str((spec.get("concept") or {}).get("concept_id") or "")
                if builder and builder in global_builder_ids:
                    issues.append(f"duplicate_builder_id_global:{builder}")
                if domain:
                    dp=spec.get("domain_presentation") or {}
                    presentation=(str(dp.get("display_name") or ""),str(dp.get("description") or ""),str(dp.get("selection_guidance") or ""))
                    existing_domain_presentation=global_domain_presentations.get(domain)
                    if existing_domain_presentation and existing_domain_presentation != presentation:
                        issues.append(f"domain_presentation_drift:{domain}")
                if domain and concept:
                    owner=global_concepts.get((domain,concept))
                    if owner and owner != publisher:
                        issues.append(f"duplicate_concept_owner:{domain}.{concept}")
                    label=str((spec.get("concept") or {}).get("display_name") or "")
                    existing_label=global_concept_labels.get((domain,concept))
                    if existing_label and existing_label != label:
                        issues.append(f"concept_label_drift:{domain}.{concept}")
                    c=spec.get("concept") or {}
                    concept_presentation=(str(c.get("display_name") or ""),str(c.get("description") or ""),str(c.get("selection_guidance") or ""))
                    existing_concept_presentation=global_concept_presentations.get((domain,concept))
                    if existing_concept_presentation and existing_concept_presentation != concept_presentation:
                        issues.append(f"concept_presentation_drift:{domain}.{concept}")
                    for source in spec.get("supported_sources", []):
                        integration=str(source.get("integration_domain") or "")
                        key=(domain,concept,integration)
                        if key in local_concept_integrations:
                            issues.append(f"duplicate_concept_integration:{domain}.{concept}:{integration}")
                        local_concept_integrations.add(key)
                        global_owner=global_concept_integrations.get(key)
                        if global_owner and global_owner != publisher:
                            issues.append(f"duplicate_concept_integration_owner:{domain}.{concept}:{integration}")
            status = "valid" if not issues else "invalid"
            if status == "valid":
                for spec in specs:
                    global_builder_ids.add(str(spec["builder_id"]))
                    domain_key=(str(spec["domain_id"]),str(spec["concept"]["concept_id"]))
                    global_concepts[domain_key]=publisher
                    global_concept_labels[domain_key]=str(spec["concept"]["display_name"])
                    dp=spec["domain_presentation"]
                    global_domain_presentations[domain_key[0]]=(str(dp["display_name"]),str(dp["description"]),str(dp["selection_guidance"]))
                    c=spec["concept"]
                    global_concept_presentations[domain_key]=(str(c["display_name"]),str(c["description"]),str(c["selection_guidance"]))
                    for source in spec.get("supported_sources", []):
                        global_concept_integrations[(domain_key[0],domain_key[1],str(source["integration_domain"]))]=publisher
                    enriched=dict(spec)
                    enriched["publication_revision"]=revision
                    enriched["specification_fingerprint"]=specification_fingerprint(spec)
                    valid_specs.append(enriched)
        except Exception as exc:
            publisher = str(registry_key)
            revision = 1
            status = "invalid"
            issues.append(f"provider_error:{type(exc).__name__}:{exc}")
        records.append(PublicationRecord(publisher, revision, status, tuple(specs), tuple(sorted(set(issues)))))

    return records, sorted(valid_specs, key=lambda spec:(str(spec["domain_id"]),str(spec["concept"]["concept_id"]),str(spec["builder_id"])))


def publication_summary(records: Iterable[PublicationRecord]) -> list[dict[str, Any]]:
    return [
        {
            "publisher_domain": record.publisher_domain,
            "publication_revision": record.publication_revision,
            "status": record.status,
            "specification_count": len(record.specifications),
            "issues": list(record.issues),
        }
        for record in records
    ]
