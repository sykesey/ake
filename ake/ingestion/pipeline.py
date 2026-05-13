"""Ingestion pipeline: fetch → parse → normalize → store."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ake.ingestion.element import Element, compute_doc_id
from ake.ingestion.normalizer import normalize
from ake.ingestion.parsers.base import BaseParser
from ake.ingestion.parsers.docx import DocxParser
from ake.ingestion.parsers.html import HtmlParser
from ake.ingestion.parsers.pdf import PDFParser

if TYPE_CHECKING:
    from ake.store.element_store import ElementStore


@dataclass
class IngestionResult:
    doc_id: str
    elements: list[Element]
    source_url: str


def _parser_for_path(path: Path) -> BaseParser:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PDFParser()
    if suffix in (".docx", ".doc"):
        return DocxParser()
    if suffix in (".html", ".htm"):
        return HtmlParser()
    raise ValueError(f"No parser registered for extension '{suffix}'")


def _parser_for_content_type(content_type: str) -> BaseParser:
    ct = content_type.lower()
    if "pdf" in ct:
        return PDFParser()
    if "docx" in ct or "officedocument" in ct:
        return DocxParser()
    if "html" in ct:
        return HtmlParser()
    raise ValueError(f"No parser registered for content type '{content_type}'")


class IngestionPipeline:
    """Orchestrates the ingestion of documents into the element store.

    Can be used with or without a store (store=None skips persistence —
    useful for offline testing and one-shot normalization).
    """

    def __init__(self, store: ElementStore | None = None) -> None:
        self._store = store

    async def ingest_file(
        self,
        path: Path | str,
        metadata: dict | None = None,
    ) -> IngestionResult:
        """Parse a file from disk and store its elements.

        Idempotent: if the doc_id already exists in the store, returns the
        stored elements without re-parsing.
        """
        path = Path(path)
        metadata = metadata or {}
        content = path.read_bytes()
        doc_id = compute_doc_id(content)

        if self._store is not None and await self._store.exists(doc_id):
            elements = await self._store.get_by_doc_id(doc_id)
            return IngestionResult(
                doc_id=doc_id,
                elements=elements,
                source_url=metadata.get("source_url", str(path)),
            )

        parser = _parser_for_path(path)
        raw_elements = parser.parse(path)

        base_meta = {
            "source_url": metadata.get("source_url", str(path)),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        elements = normalize(raw_elements, doc_id, base_meta)

        if self._store is not None:
            await self._store.save(elements)

        return IngestionResult(
            doc_id=doc_id,
            elements=elements,
            source_url=base_meta["source_url"],
        )

    async def ingest_bytes(
        self,
        content: bytes,
        content_type: str,
        metadata: dict | None = None,
    ) -> IngestionResult:
        """Parse in-memory content and store its elements.

        Idempotent by content hash.
        """
        metadata = metadata or {}
        doc_id = compute_doc_id(content)

        if self._store is not None and await self._store.exists(doc_id):
            elements = await self._store.get_by_doc_id(doc_id)
            return IngestionResult(
                doc_id=doc_id,
                elements=elements,
                source_url=metadata.get("source_url", ""),
            )

        parser = _parser_for_content_type(content_type)
        raw_elements = parser.parse_bytes(content)

        base_meta = {
            "source_url": metadata.get("source_url", ""),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        elements = normalize(raw_elements, doc_id, base_meta)

        if self._store is not None:
            await self._store.save(elements)

        return IngestionResult(
            doc_id=doc_id,
            elements=elements,
            source_url=base_meta["source_url"],
        )
