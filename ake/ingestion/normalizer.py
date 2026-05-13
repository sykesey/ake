"""Map raw unstructured element objects to the AKE Element schema.

The normalizer is the only place that knows about unstructured's internal
types.  Everything downstream works with Element objects.
"""
from __future__ import annotations

from typing import Any

from ake.ingestion.element import Element, ElementType, compute_element_id

# Map unstructured class names → AKE element types
_TYPE_MAP: dict[str, ElementType] = {
    "Title": "title",
    "Header": "header",
    "NarrativeText": "paragraph",
    "Text": "paragraph",
    "FigureCaption": "paragraph",
    "Footer": "paragraph",
    "Address": "paragraph",
    "EmailAddress": "paragraph",
    "Formula": "paragraph",
    "ListItem": "list",
    "Table": "table",
    "Image": "figure",
    "Figure": "figure",
}

_HEADING_TYPES: frozenset[str] = frozenset({"title", "header"})


def _map_type(raw_el: Any) -> ElementType:
    return _TYPE_MAP.get(type(raw_el).__name__, "paragraph")


def _get_page(raw_el: Any) -> int:
    meta = getattr(raw_el, "metadata", None)
    if meta is not None:
        page = getattr(meta, "page_number", None)
        if page is not None:
            return int(page)
    return 0


def _get_category_depth(raw_el: Any) -> int:
    """Return 0-based heading depth from unstructured metadata."""
    meta = getattr(raw_el, "metadata", None)
    if meta is not None:
        depth = getattr(meta, "category_depth", None)
        if depth is not None:
            return int(depth)
    return 0


def normalize(raw_elements: list[Any], doc_id: str, metadata_base: dict) -> list[Element]:
    """Convert a list of raw unstructured elements into AKE Element records.

    Args:
        raw_elements:  Output of any unstructured partition_* call.
        doc_id:        Stable content hash of the source document.
        metadata_base: Dict merged into every element's metadata field.
                       Must include at least {"source_url": ...}.

    Returns:
        List of Element records with section_path populated.
    """
    elements: list[Element] = []
    section_stack: list[str] = []

    for i, raw_el in enumerate(raw_elements):
        text: str = getattr(raw_el, "text", "") or ""
        if not text.strip():
            continue

        el_type = _map_type(raw_el)
        page = _get_page(raw_el)
        element_id = compute_element_id(doc_id, i, el_type, text)

        if el_type in _HEADING_TYPES:
            depth = _get_category_depth(raw_el)
            # depth=0 → top-level; truncate stack and push new heading.
            section_stack = section_stack[:depth] + [text]

        elements.append(
            Element(
                doc_id=doc_id,
                element_id=element_id,
                type=el_type,
                text=text,
                page=page,
                section_path=list(section_stack),
                metadata={**metadata_base, "raw_type": type(raw_el).__name__},
            )
        )

    return elements
