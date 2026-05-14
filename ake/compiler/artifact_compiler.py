"""ArtifactCompiler — LLM-based extraction + citation verification (F002)."""
from __future__ import annotations

import json
import logging
from typing import Any

from ake.compiler.artifact import DomainArtifact, DomainSchema, compute_artifact_id
from ake.compiler.citation import DocumentRef
from ake.compiler.prompts.extraction import SYSTEM_PROMPT, build_extraction_messages
from ake.compiler.verifier import verify_citations
from ake.ingestion.element import Element
from ake.llm.router import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when the LLM response cannot be parsed into an artifact."""


class ArtifactCompiler:
    """Compiles a list of Elements into a DomainArtifact via LLM extraction.

    Usage::

        compiler = ArtifactCompiler(router)
        artifact, failed_fields = await compiler.compile(elements, schema)
    """

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def compile(
        self,
        elements: list[Element],
        schema: DomainSchema,
    ) -> tuple[DomainArtifact, list[str]]:
        """Extract and verify one artifact from the given elements.

        Returns (artifact, failed_fields).  Fields in failed_fields were
        nulled by the citation verifier — their values could not be grounded.
        """
        if not elements:
            raise ValueError("elements must be non-empty")

        doc_id = elements[0].doc_id
        acl_principals: list[str] = elements[0].metadata.get("acl_principals", [])
        doc_metadata = dict(elements[0].metadata)

        messages = build_extraction_messages(elements, schema, doc_metadata=doc_metadata)
        request = LLMRequest(
            messages=messages,
            system=SYSTEM_PROMPT,
            temperature=0.0,
        )

        response = await self._router.complete(request)
        raw = self._strip_fences(response.content)

        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"LLM returned non-JSON: {exc}") from exc

        entity_id = str(data.get("entity_id") or "")
        if not entity_id:
            raise ExtractionError("LLM response missing 'entity_id'")

        fiscal_year: int | None = data.get("fiscal_year")
        if fiscal_year is not None:
            try:
                fiscal_year = int(fiscal_year)
            except (TypeError, ValueError):
                fiscal_year = None

        payload: dict[str, Any] = {}
        field_citations: dict[str, Any] = {}

        fields_data: dict[str, Any] = data.get("fields", {})
        for field_name, field_data in fields_data.items():
            if not isinstance(field_data, dict):
                continue
            value = field_data.get("value")
            payload[field_name] = value

            if value is None:
                continue

            source = field_data.get("source")
            if not isinstance(source, dict):
                continue

            element_id = source.get("element_id", "")
            verbatim_span = source.get("verbatim_span", "")
            if not element_id or not verbatim_span:
                continue

            # Resolve char offsets from the verbatim span
            el = next((e for e in elements if e.element_id == element_id), None)
            if el is not None:
                pos = el.text.find(verbatim_span)
                if pos >= 0:
                    field_citations[field_name] = DocumentRef(
                        element_id=element_id,
                        char_start=pos,
                        char_end=pos + len(verbatim_span),
                        verbatim_span=verbatim_span,
                    )

        artifact_id = compute_artifact_id(doc_id, entity_id, schema.artifact_type)

        artifact = DomainArtifact(
            artifact_id=artifact_id,
            doc_id=doc_id,
            entity_id=entity_id,
            artifact_type=schema.artifact_type,
            fiscal_year=fiscal_year,
            payload=payload,
            field_citations=field_citations,
            acl_principals=acl_principals,
        )

        artifact, failed = verify_citations(artifact, elements)

        # Post-verification backfill: for schema fields that the verifier nulled
        # (no element-text citation available), fall back to doc-level metadata
        # when it carries an authoritative value.  These fields are stored without
        # a citation; _fields_cited_ratio reflects that honestly.
        _METADATA_FALLBACK_KEYS = {"department", "owner", "classification"}
        for field_name, spec in schema.fields.items():
            if artifact.payload.get(field_name) is not None:
                continue  # already populated — don't overwrite
            meta_val = doc_metadata.get(field_name)
            if meta_val is not None and field_name in _METADATA_FALLBACK_KEYS:
                artifact.payload[field_name] = meta_val
                if field_name in failed:
                    failed.remove(field_name)

        logger.info(
            "artifact_compiled artifact_id=%s entity_id=%s fields=%d failed=%d",
            artifact_id,
            entity_id,
            len(payload),
            len(failed),
        )

        return artifact, failed

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove markdown code fences if the LLM wrapped its JSON output."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Drop opening fence line and closing fence
            inner = lines[1:] if lines[0].startswith("```") else lines
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            text = "\n".join(inner).strip()
        return text
