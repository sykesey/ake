"""Ingestion pipeline: fetch → parse → normalize → store."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ake.ingestion.element import Element, compute_doc_id, compute_tabular_doc_id
from ake.ingestion.normalizer import normalize
from ake.ingestion.parsers.arrow_ipc import ArrowIPCParser
from ake.ingestion.parsers.base import BaseParser
from ake.ingestion.parsers.csv_parser import CsvParser
from ake.ingestion.parsers.docx import DocxParser
from ake.ingestion.parsers.html import HtmlParser
from ake.ingestion.parsers.parquet import ParquetParser
from ake.ingestion.parsers.pdf import PDFParser
from ake.ingestion.tabular_normalizer import normalize_tabular

if TYPE_CHECKING:
    from ake.store.element_store import ElementStore

_TABULAR_SUFFIXES: frozenset[str] = frozenset({".parquet", ".csv", ".arrow", ".feather", ".arrows"})


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
    if suffix in _TABULAR_SUFFIXES:
        raise ValueError(
            f"'{suffix}' is a tabular format — use IngestionPipeline.ingest_tabular_file() instead"
        )
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

        Tabular formats (.parquet, .csv, .arrow, .feather, .arrows) are
        automatically routed to :meth:`ingest_tabular_file`.
        """
        path = Path(path)
        if path.suffix.lower() in _TABULAR_SUFFIXES:
            return await self.ingest_tabular_file(path, metadata=metadata)

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

    async def ingest_tabular_file(
        self,
        path: Path | str,
        metadata: dict | None = None,
        dataset_name: str | None = None,
        batch_size: int = 10_000,
    ) -> IngestionResult:
        """Ingest a tabular file (Parquet, CSV, Arrow IPC) into the element store.

        doc_id is a stable hash of (source_uri, schema_fingerprint, content_hash),
        so schema changes and data changes both invalidate it (F009).

        Rows are streamed in batches — the full dataset is never loaded into memory.
        Sparse rows (all cells null) are silently dropped.

        Args:
            path:         Path to the tabular file.
            metadata:     Extra fields merged into every element's metadata.
            dataset_name: Override the dataset name (defaults to parent directory name).
            batch_size:   Rows per batch for Parquet / row-count hint for others.
        """
        path = Path(path)
        metadata = metadata or {}
        suffix = path.suffix.lower()

        if suffix == ".csv":
            parser: ParquetParser | CsvParser | ArrowIPCParser = CsvParser()
        elif suffix in (".arrow", ".feather", ".arrows"):
            parser = ArrowIPCParser()
        else:
            parser = ParquetParser()

        source_uri = metadata.get("source_url", str(path))
        ds_name = dataset_name or path.parent.name or path.stem
        tbl_name = path.stem

        schema = parser.get_schema(path)
        fingerprint = parser.schema_fingerprint(schema)
        content_hash = compute_doc_id(path.read_bytes())
        doc_id = compute_tabular_doc_id(source_uri, fingerprint, content_hash)
        partition = parser.partition_keys(path) if hasattr(parser, "partition_keys") else {}

        if self._store is not None and await self._store.exists(doc_id):
            elements = await self._store.get_by_doc_id(doc_id)
            return IngestionResult(
                doc_id=doc_id,
                elements=elements,
                source_url=source_uri,
            )

        base_meta = {
            "source_url": source_uri,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }

        if suffix == ".csv":
            batches = parser.iter_batches(path)
        elif suffix in (".arrow", ".feather", ".arrows"):
            batches = parser.iter_batches(path)
        else:
            batches = parser.iter_batches(path, batch_size=batch_size)

        elements = normalize_tabular(
            batches=batches,
            schema=schema,
            doc_id=doc_id,
            dataset_name=ds_name,
            table_name=tbl_name,
            metadata_base=base_meta,
            partition=partition or None,
        )

        if self._store is not None:
            await self._store.save(elements)

        return IngestionResult(
            doc_id=doc_id,
            elements=elements,
            source_url=source_uri,
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
