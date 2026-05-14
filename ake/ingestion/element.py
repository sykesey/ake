from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

ElementType = Literal["title", "paragraph", "table", "list", "figure", "header", "row"]

VALID_ELEMENT_TYPES: frozenset[str] = frozenset(
    {"title", "paragraph", "table", "list", "figure", "header", "row"}
)


@dataclass
class Element:
    doc_id: str
    element_id: str
    type: ElementType
    text: str
    page: int
    section_path: list[str]
    metadata: dict = field(default_factory=dict)


def compute_doc_id(content: bytes) -> str:
    """Stable SHA-256 hex digest of raw document bytes."""
    return hashlib.sha256(content).hexdigest()


def compute_element_id(doc_id: str, index: int, el_type: str, text: str) -> str:
    """Stable ID unique within a document: position + type + first 100 chars of text."""
    content = f"{doc_id}:{index}:{el_type}:{text[:100]}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def compute_tabular_doc_id(source_uri: str, schema_fingerprint: str, content_hash: str) -> str:
    """Stable doc_id for tabular sources: hash of (source_uri, schema_fingerprint, content_hash).

    A schema change (column added, renamed, retyped) or a data change both produce a new
    doc_id, preventing artifacts compiled against an old schema from being silently served.
    """
    composite = f"tabular:{source_uri}:{schema_fingerprint}:{content_hash}"
    return hashlib.sha256(composite.encode()).hexdigest()
