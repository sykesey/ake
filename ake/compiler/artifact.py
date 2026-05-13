"""DomainArtifact and DomainSchema types."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ake.compiler.citation import Citation


def compute_artifact_id(doc_id: str, entity_id: str, artifact_type: str) -> str:
    """Deterministic hash of (doc_id, entity_id, artifact_type).

    Re-compiling an unchanged document always yields the same artifact_id,
    making upsert idempotent.
    """
    raw = f"{doc_id}:{entity_id}:{artifact_type}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class DomainArtifact:
    artifact_id: str
    doc_id: str
    entity_id: str
    artifact_type: str
    fiscal_year: int | None
    payload: dict[str, Any]
    field_citations: dict[str, Citation]
    acl_principals: list[str]
    compiled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FieldSpec:
    description: str
    type: str = "str"  # str | int | float | bool | date
    required: bool = False


@dataclass
class DomainSchema:
    """Describes a single artifact type for one domain.

    Fields:
      artifact_type     — the string identifier stored in every artifact row
      description       — passed to the LLM so it understands what to extract
      entity_id_field   — which payload field to use as the entity_id (must be str)
      fields            — name → FieldSpec
      fiscal_year_field — optional payload field to promote as fiscal_year (int)
    """

    artifact_type: str
    description: str
    entity_id_field: str
    fields: dict[str, FieldSpec]
    fiscal_year_field: str | None = None
