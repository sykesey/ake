from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ake.ingestion.parsers.base import BaseParser


class DocxParser(BaseParser):
    def parse(self, path: Path) -> list[Any]:
        try:
            from unstructured.partition.docx import partition_docx
        except ImportError as exc:
            raise ImportError(
                "DOCX parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_docx(filename=str(path))

    def parse_bytes(self, content: bytes, **kwargs: Any) -> list[Any]:
        try:
            from unstructured.partition.docx import partition_docx
        except ImportError as exc:
            raise ImportError(
                "DOCX parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_docx(file=io.BytesIO(content))
