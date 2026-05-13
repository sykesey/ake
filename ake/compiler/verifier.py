"""Citation verification — the hard gate before artifact persistence (F003, ADR-002).

After LLM extraction, every non-null field must have a citation that resolves
to a real element and whose verbatim span literally appears in the source text.
Fields that fail verification are nulled; the artifact is never stored with
bad provenance.
"""
from __future__ import annotations

import logging
from typing import Any

from ake.compiler.citation import DocumentRef, GraphRef, TabularRef
from ake.compiler.artifact import DomainArtifact
from ake.ingestion.element import Element

logger = logging.getLogger(__name__)


def _verify_document_ref(ref: DocumentRef, elements: dict[str, Element]) -> bool:
    el = elements.get(ref.element_id)
    if el is None:
        logger.debug("citation_miss element_not_found element_id=%s", ref.element_id)
        return False
    # Verify verbatim_span is a substring of the declared character range
    # (F003: must be within text[char_start:char_end], not just anywhere in el.text)
    sliced = el.text[ref.char_start : ref.char_end]
    if ref.verbatim_span not in sliced:
        logger.debug(
            "citation_miss span_not_in_range element_id=%s char_start=%d char_end=%d "
            "span=%r sliced=%r",
            ref.element_id,
            ref.char_start,
            ref.char_end,
            ref.verbatim_span[:60],
            sliced[:60],
        )
        return False
    return True


def _verify_tabular_ref(ref: TabularRef, elements: dict[str, Element]) -> bool:
    el = elements.get(ref.element_id)
    if el is None:
        logger.debug("citation_miss element_not_found element_id=%s", ref.element_id)
        return False
    if ref.verbatim_value not in el.text:
        logger.debug(
            "citation_miss value_not_found element_id=%s value=%r",
            ref.element_id,
            ref.verbatim_value[:60],
        )
        return False
    return True


def _verify_graph_ref(ref: GraphRef, elements: dict[str, Element]) -> bool:
    el = elements.get(ref.element_id)
    if el is None:
        logger.debug("citation_miss element_not_found element_id=%s", ref.element_id)
        return False
    return True


def verify_citations(
    artifact: DomainArtifact,
    elements: list[Element],
) -> tuple[DomainArtifact, list[str]]:
    """Verify every non-null field's citation and null fields that fail.

    Returns the cleaned artifact and a list of field names that were nulled.
    The artifact object is mutated in-place; the same reference is returned.
    """
    by_id = {el.element_id: el for el in elements}
    failed: list[str] = []

    for field_name, value in list(artifact.payload.items()):
        if value is None:
            continue

        citation = artifact.field_citations.get(field_name)
        if citation is None:
            logger.warning(
                "citation_missing field=%s artifact_id=%s — nulling field",
                field_name,
                artifact.artifact_id,
            )
            artifact.payload[field_name] = None
            failed.append(field_name)
            continue

        if isinstance(citation, DocumentRef):
            ok = _verify_document_ref(citation, by_id)
        elif isinstance(citation, TabularRef):
            ok = _verify_tabular_ref(citation, by_id)
        elif isinstance(citation, GraphRef):
            ok = _verify_graph_ref(citation, by_id)
        else:
            ok = False

        if not ok:
            logger.warning(
                "citation_failed field=%s artifact_id=%s — nulling field",
                field_name,
                artifact.artifact_id,
            )
            artifact.payload[field_name] = None
            artifact.field_citations.pop(field_name, None)
            failed.append(field_name)

    return artifact, failed
